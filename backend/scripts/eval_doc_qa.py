"""Doc-QA eval suite — "talk to the building" (DOC_QA_PLAYBOOK.md).

Drives stream_response directly (no HTTP) as the admin eval user, who owns
both the structured_data tables AND the 8 ingested O&M manuals — so routing
is tested with both answer paths live.

Three pipeline layers per case:
  - ROUTING:   doc questions must use a document tool and must NOT use
               query_structured_data (asserted via tool_start events)
  - RETRIEVAL: cases with an "evidence" phrase also call retrieve_chunks()
               directly and assert the phrase appears in the top-k chunks —
               separates retrieval failures from answer-generation failures
  - ANSWER:    final streamed text must contain the ground-truth fact
               (numbers within 0.01, case-insensitive substrings, accepted
               spelling variants from the playbook), negatives must use a
               not-found phrasing without fabricating specifics

Case spec keys (superset of eval_routing.py):
  doc             short doc tag for the scorecard (acs/cctv/bms/ups/swgr/zip/san/wh)
  category        spec | entity | location | maintenance | negative
  style           clean | conversational | multi-turn | typo | format
  messages        list of {role, content}
  tools_any       at least one of these tools must be used
                  (default: any doc tool — search_documents/analyze_document/
                  explore_knowledge_base — unless the case is a pure follow-up)
  tools_exclude   none of these may appear (default: ["query_structured_data"])
  contains_any / contains_all / has_number / has_numbers / min_rows /
  row_marker / require_digits / negative / forbid_regex
  evidence        phrase that must appear in retrieve_chunks(top_k=5) output

Results are appended per case to scripts/results/doc_qa_results.jsonl so an
interrupted run loses only the case in flight.

Usage: cd backend && venv/Scripts/python scripts/eval_doc_qa.py [1-15 | name...]
"""
import os, sys, re, json, time
from datetime import datetime, timezone

# Windows consoles default to cp1252 — case names/answers contain Unicode
# (e.g. "→"), which otherwise CRASHES the runner mid-suite with
# UnicodeEncodeError and silently truncates full runs.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)
from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND, ".env"))
from supabase import create_client

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
sd = sb.table("structured_data").select("user_id, table_name, columns").execute()
user_id = sd.data[0]["user_id"]
structured_tables = [{"table_name": t["table_name"], "columns": t["columns"]} for t in sd.data]

from app.services.openai_client import stream_response, retrieve_chunks

RESULTS_PATH = os.path.join(os.path.dirname(__file__), "results", "doc_qa_results.jsonl")
# Any of these counts as "routed to the documents" — the five precision tools
# (tree/glob/grep/list_files/read_document) are legitimate document paths too.
DOC_TOOLS = ["search_documents", "analyze_document", "explore_knowledge_base",
             "tree", "glob", "grep", "list_files", "read_document"]

LEAK_STRINGS = ["IMPORTANT (for interpreting", "SQL: `", "sql: `"]
HTML_TAGS = ["<table", "<tr>", "<td", "<th", "<br", "<div", "<span"]
NOT_FOUND_PHRASES = [
    "not found", "does not exist", "doesn't exist", "couldn't find",
    "could not find", "no data", "no record", "no results", "not present",
    "unable to find", "no information", "not appear", "no match", "not listed",
    "don't have", "do not have", "not contain", "doesn't contain", "no entry",
    "isn't", "is not in", "not available", "not exist", "not specified",
    "not stated", "not mentioned", "not documented", "not detailed",
    "not provided", "doesn't specify", "does not specify", "no mention",
    "not included", "doesn't state", "does not state", "not applicable",
    "doesn't mention", "does not mention", "not covered", "no specific",
    "doesn't include", "does not include", "not explicitly",
    # plural-subject verb forms ("the documents do not state ...")
    "do not state", "do not specify", "do not mention", "do not contain",
    "do not include", "do not provide", "do not outline", "do not list",
    "does not list", "doesn't list", "do not detail", "not state",
]


def u(content):
    return {"role": "user", "content": content}


def m(content):
    return {"role": "assistant", "content": content}


def _run_once(messages):
    tools_used, text = [], []
    for evt, data in stream_response(
        messages=messages,
        user_id=user_id, supabase_client=sb,
        has_documents=True, has_structured_data=True,
        structured_tables=structured_tables,
    ):
        if evt == "tool_start":
            tools_used.append(json.loads(data).get("tool"))
        elif evt == "token":
            text.append(data)
    return tools_used, "".join(text)


def run(messages, attempts=3, delay=5):
    # Per-case watchdog with DAEMON worker threads: a wedged streamed read can
    # hang forever, and non-daemon workers (ThreadPoolExecutor) also blocked
    # process exit after the final attempt, stalling whole suite chains.
    import threading, queue
    for i in range(attempts):
        q = queue.Queue()
        def _work():
            try:
                q.put(("ok", _run_once(messages)))
            except Exception as exc:
                q.put(("err", exc))
        threading.Thread(target=_work, daemon=True).start()
        try:
            kind, val = q.get(timeout=300)
        except queue.Empty:
            kind, val = "err", TimeoutError("watchdog: case exceeded 300s")
        if kind == "ok":
            return val
        e = val
        if i == attempts - 1:
            raise e
        wait = 65 if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e) else delay * (i + 1)
        print(f"  (transient error, retry {i + 1}/{attempts - 1} in {wait}s: {type(e).__name__})")
        time.sleep(wait)


def answer_numbers(text):
    nums = []
    for tok in re.findall(r"-?\d[\d,]*(?:\.\d+)?", text):
        try:
            nums.append(float(tok.replace(",", "")))
        except ValueError:
            pass
    return nums


def table_rows(text, marker):
    return [l for l in text.splitlines()
            if l.strip().startswith("|") and marker.lower() in l.lower()]


def check_retrieval(case):
    """Layer 2: evidence phrase must appear in the top-k retrieved chunks for
    the (last) user question. Returns a failure string or None."""
    evidence = case.get("evidence")
    if not evidence:
        return None
    q = case["messages"][-1]["content"]
    # top_k=10 matches what _execute_search_documents actually fetches
    chunks = retrieve_chunks(q, user_id, sb, top_k=10)
    blob = " ".join(c["content"] for c in chunks).lower()
    if evidence.lower() not in blob:
        return (f"RETRIEVAL: evidence '{evidence}' not in top-10 chunks "
                f"(files={sorted(set(c['file_name'] for c in chunks))})")
    return None


def check(case, tools, answer):
    failures = []
    a = answer.lower()

    if not answer.strip():
        failures.append("empty answer")
    for leak in LEAK_STRINGS:
        if leak.lower() in a:
            failures.append(f"leaked internal note: '{leak}'")
            break
    for tag in HTML_TAGS:
        if tag in a:
            failures.append(f"raw HTML in answer: '{tag}'")
            break

    # ROUTING layer
    tools_any = case.get("tools_any", DOC_TOOLS)
    if tools_any and not any(t in tools for t in tools_any):
        failures.append(f"ROUTING: none of {tools_any} used (tools={tools})")
    for t in case.get("tools_exclude", ["query_structured_data"]):
        if t in tools:
            failures.append(f"ROUTING: tool '{t}' must NOT be used (tools={tools})")

    # ANSWER layer
    for s in case.get("contains_all", []):
        if s.lower() not in a:
            failures.append(f"answer missing '{s}'")
    if case.get("contains_any"):
        if not any(s.lower() in a for s in case["contains_any"]):
            failures.append(f"answer contains none of {case['contains_any']}")

    wants = case.get("has_numbers", [])
    if "has_number" in case:
        wants = wants + [case["has_number"]]
    nums = answer_numbers(answer) if wants else []
    for want in wants:
        if not any(abs(v - want) < 0.01 for v in nums):
            failures.append(f"number {want} not in answer")

    if case.get("require_digits") and not any(ch.isdigit() for ch in answer):
        failures.append("answer has no digits")

    if "min_rows" in case:
        rows = table_rows(answer, case.get("row_marker", "|"))
        if len(rows) < case["min_rows"]:
            failures.append(f"expected >= {case['min_rows']} table rows with "
                            f"'{case.get('row_marker')}', got {len(rows)}")

    if case.get("negative"):
        if not any(p in a for p in NOT_FOUND_PHRASES):
            failures.append("no not-found phrasing in answer")

    if case.get("forbid_regex"):
        if re.search(case["forbid_regex"], a):
            failures.append(f"answer matches forbidden pattern {case['forbid_regex']}")

    return failures


# ---------------------------------------------------------------------------
# Case bank — from DOC_QA_PLAYBOOK.md ground truth. Respect the do-NOT-ask
# caveats; accepted spelling variants are folded into contains_any lists.
# ---------------------------------------------------------------------------
CASES = [
    # ===================== ACS.md =====================
    {
        "name": "ACS1 brand (clean/entity)",
        "doc": "acs", "category": "entity", "style": "clean",
        "messages": [u("What brand of access control hardware is installed?")],
        "contains_any": ["paxton"],
        "evidence": "Paxton",
    },
    {
        "name": "ACS2 single-door controller count (clean/spec)",
        "doc": "acs", "category": "spec", "style": "clean",
        "messages": [u("How many single-door access controllers are in the access control system?")],
        "has_number": 103,
        "evidence": "103",
    },
    {
        "name": "ACS3 workstation location (conversational/location)",
        "doc": "acs", "category": "location", "style": "conversational",
        "messages": [u("hey, where would I find the access control workstation in the building?")],
        "contains_all": ["security monitor"],
        "contains_any": ["1st floor", "first floor", "1st-floor", "first-floor", "level 1", "level 01"],
    },
    {
        "name": "ACS4 same-floor controller protocol (clean/spec)",
        "doc": "acs", "category": "spec", "style": "clean",
        "messages": [u("What protocol connects the door controllers on the same floor?")],
        "contains_any": ["rs-485", "rs485", "rs 485"],
        "evidence": "RS-485",
    },
    {
        "name": "ACS5 Net2 Plus max users (clean/spec)",
        "doc": "acs", "category": "spec", "style": "clean",
        "messages": [u("What is the maximum number of users the Net2 Plus controller supports?")],
        "has_number": 50000,
    },
    {
        "name": "ACS6 installer warranty (clean/entity)",
        "doc": "acs", "category": "entity", "style": "clean",
        "messages": [u("What warranty did the installer IIS give on the access control system?")],
        "contains_any": ["3 year", "3-year", "three year", "three-year"],
    },
    {
        "name": "ACS7 fire alarm door behavior (conversational/maintenance)",
        "doc": "acs", "category": "maintenance", "style": "conversational",
        "messages": [u("what happens to the doors with access control if the fire alarm goes off?")],
        "contains_any": ["release", "open", "unlock"],
    },
    {
        "name": "ACS8 PPM frequency (negative)",
        "doc": "acs", "category": "negative", "style": "clean",
        "messages": [u("What is the PPM service frequency for the access control equipment?")],
        "negative": True,
    },
    {
        "name": "ACS9 brand then manufacturer warranty (multi-turn/entity)",
        "doc": "acs", "category": "entity", "style": "multi-turn",
        "messages": [
            u("What brand of access control hardware is installed?"),
            m("The access control system uses **Paxton** (UK) hardware — Net2 Plus controllers with P50 proximity readers, installed by Integrated Ideal Solutions (IIS)."),
            u("and what's their manufacturer warranty?"),
        ],
        "contains_any": ["5 year", "5-year", "five year", "five-year"],
    },
    # ===================== CCTV =====================
    {
        # Genuinely ambiguous routing: cameras ARE electrical equipment, so a
        # SQL-first attempt is legitimate — the empty-result fallback must then
        # reach the documents and answer correctly. tools_exclude relaxed.
        "name": "CCTV1 total cameras (clean/spec)",
        "doc": "cctv", "category": "spec", "style": "clean",
        "messages": [u("How many CCTV cameras are installed in total?")],
        "tools_exclude": [],
        "has_number": 166,
        "evidence": "166",
    },
    {
        # Same ambiguity as CCTV1, plus typos (vector-search dependent once
        # keyword terms don't match — see migration 021).
        "name": "CCTV2 camera count typos (typo/spec)",
        "doc": "cctv", "category": "spec", "style": "typo",
        "messages": [u("how many camras are instaled in the campas cctv sytem?")],
        "tools_exclude": [],
        "has_number": 166,
    },
    {
        "name": "CCTV3 server room location (clean/location)",
        "doc": "cctv", "category": "location", "style": "clean",
        "messages": [u("Where is the CCTV server room located?")],
        "contains_any": ["1st floor", "first floor", "1st-floor", "first-floor", "level 1", "level 01"],
    },
    {
        "name": "CCTV4 VMS software (clean/entity)",
        "doc": "cctv", "category": "entity", "style": "clean",
        "messages": [u("What video management software is used for the CCTV system?")],
        "contains_any": ["videoxpert"],
        "evidence": "VideoXpert",
    },
    {
        "name": "CCTV5 camera-to-IDF cable (clean/spec)",
        "doc": "cctv", "category": "spec", "style": "clean",
        "messages": [u("What cable runs from the field cameras to the IDF?")],
        "contains_any": ["cat6a", "cat 6a", "cat-6a"],
    },
    {
        "name": "CCTV6 camera position check frequency (clean/maintenance)",
        "doc": "cctv", "category": "maintenance", "style": "clean",
        "messages": [u("How often are the CCTV camera positions and views checked?")],
        "contains_any": ["month"],
    },
    {
        "name": "CCTV7 default camera IP (conversational/spec)",
        "doc": "cctv", "category": "spec", "style": "conversational",
        "messages": [u("what's the default IP address the Pelco cameras come with out of the box?")],
        "contains_any": ["192.168.0.20"],
    },
    {
        "name": "CCTV8 PTZ cameras (negative)",
        "doc": "cctv", "category": "negative", "style": "clean",
        "messages": [u("How many PTZ cameras are installed in the CCTV system?")],
        "contains_any": ["fixed", "no ptz", "none", "not"],
        "forbid_regex": r"\b\d+\s*(?:x\s*)?ptz",
    },
    {
        # Grader note: answers consistently (and correctly) say "not stated in
        # the docs, SIRA governs it; SIRA typically mandates 31 days" with the
        # general-knowledge label. No regex can split a disclaimed mention from
        # an assertion, so fabrication policing here is the required not-found
        # phrasing (below) — which a fabricated-as-fact answer would lack.
        "name": "CCTV9 retention period (negative)",
        "doc": "cctv", "category": "negative", "style": "clean",
        "messages": [u("What is the CCTV recording retention period in days?")],
        "contains_any": ["sira"] + NOT_FOUND_PHRASES,
    },
    {
        "name": "CCTV10 count then server room (multi-turn/location)",
        "doc": "cctv", "category": "location", "style": "multi-turn",
        "messages": [
            u("how many cameras are there in total on the campus?"),
            m("There are **166 CCTV cameras** installed in total across the HWUD main campus, all Pelco Sarix fixed dome models."),
            u("and where is their server room?"),
        ],
        "contains_any": ["1st floor", "first floor", "1st-floor", "first-floor", "level 1", "level 01"],
    },
    # ===================== BMS =====================
    {
        # Cross-domain trap: FCUs ARE table vocabulary (db_circuits.load_type),
        # but "does the BMS control" is a system-scope question only the BMS
        # manual answers (424; the load schedule counts 550 circuit points).
        # SQL-first is tolerated IF the empty-result fallback lands on the
        # manual — the 424 assertion kills the wrong-550 path.
        "name": "BMS1 FCU count controlled by BMS (clean/spec)",
        "doc": "bms", "category": "spec", "style": "clean",
        "messages": [u("How many FCUs does the BMS control?")],
        "tools_exclude": [],
        "has_number": 424,
        "evidence": "424",
    },
    {
        "name": "BMS2 supplier (clean/entity)",
        "doc": "bms", "category": "entity", "style": "clean",
        "messages": [u("Who supplied the BMS?")],
        "contains_any": ["siemens"],
    },
    {
        # "DDC panel" collides with the `panels` table vocabulary — SQL-first
        # with docs fallback is tolerated; the "roof" assertion is the check.
        "name": "BMS3 FAHU DDC panel location (clean/location)",
        "doc": "bms", "category": "location", "style": "clean",
        "messages": [u("Where is the DDC panel that controls the two FAHUs located?")],
        "tools_exclude": [],
        "contains_any": ["roof"],
    },
    {
        "name": "BMS4 valve gland leakage check (clean/maintenance)",
        "doc": "bms", "category": "maintenance", "style": "clean",
        "messages": [u("How often are the BMS control valves checked for gland leakage?")],
        "contains_any": ["month"],
    },
    {
        "name": "BMS5 DDC PCB battery replacement (conversational/maintenance)",
        "doc": "bms", "category": "maintenance", "style": "conversational",
        "messages": [u("when am I supposed to change the battery on the DDC controller PCB?")],
        "contains_any": ["five year", "5 year", "5-year"],
    },
    {
        # "explain" triggers the analyze_document hijack; its topic phrase
        # matches no file name, so the by-design chain is analyze → SQL(empty)
        # → search_documents. Mid-chain SQL is fine — the fact checks decide.
        "name": "BMS6 leak detection integration (conversational/entity)",
        "doc": "bms", "category": "entity", "style": "conversational",
        "messages": [u("explain in simple terms whose leak detection system is integrated with the BMS and how")],
        "tools_exclude": [],
        "contains_all": ["honeywell"],
        "contains_any": ["bacnet"],
    },
    {
        "name": "BMS7 chiller plant (negative)",
        "doc": "bms", "category": "negative", "style": "clean",
        "messages": [u("What make and model is the chiller plant in the BMS manual?")],
        "negative": True,
    },
    {
        "name": "BMS8 supplier then warranty (multi-turn/entity)",
        "doc": "bms", "category": "entity", "style": "multi-turn",
        "messages": [
            u("who supplied the BMS?"),
            m("The BMS was supplied by **Siemens** (Siemens LLC Building Technologies BT SPP) — it's a Siemens Desigo system."),
            u("what warranty did they give on it?"),
        ],
        "contains_any": ["12 month", "12-month", "1 year", "one year", "one-year", "february 2022"],
    },
    # ===================== UPS =====================
    {
        "name": "UPS1 brand (clean/entity)",
        "doc": "ups", "category": "entity", "style": "clean",
        "messages": [u("What brand of UPS is installed in the building?")],
        "contains_any": ["tripp lite", "tripp-lite", "tripplite"],
        "evidence": "Tripp Lite",
    },
    {
        "name": "UPS2 designed runtime (clean/spec)",
        "doc": "ups", "category": "spec", "style": "clean",
        "messages": [u("What runtime are the main UPS units designed for at full load?")],
        "contains_any": ["30 min", "30-min", "30min", "thirty min"],
    },
    {
        "name": "UPS3 8kVA model (clean/spec)",
        "doc": "ups", "category": "spec", "style": "clean",
        "messages": [u("What is the model number of the 8kVA UPS units?")],
        "contains_any": ["su8000rt3uhw"],
        "evidence": "SU8000RT3UHW",
    },
    {
        # "8kVA … units" reads electrical → SQL-first tolerated (fallback must
        # land the docs). Purpose is "Server Racks (ICT)" — accept either word.
        "name": "UPS4 8kVA count and purpose (clean/spec)",
        "doc": "ups", "category": "spec", "style": "clean",
        "messages": [u("How many 8kVA UPS units are installed and what do they power?")],
        "tools_exclude": [],
        "has_number": 3,
        "contains_any": ["server", "ict"],
    },
    {
        "name": "UPS5 warranty (clean/entity)",
        "doc": "ups", "category": "entity", "style": "clean",
        "messages": [u("What is the warranty on the UPS system?")],
        "contains_any": ["2 year", "two year", "two-year", "2-year"],
    },
    {
        "name": "UPS6 overload fault action (conversational/maintenance)",
        "doc": "ups", "category": "maintenance", "style": "conversational",
        "messages": [u("the UPS is showing an overload fault, what should I do?")],
        "contains_any": ["reduce"],
    },
    {
        "name": "UPS7 8kVA battery pack (clean/spec)",
        "doc": "ups", "category": "spec", "style": "clean",
        "messages": [u("Which external battery pack pairs with the 8kVA UPS and how many are installed?")],
        "contains_any": ["bp240v10rt3u", "bp240v10rt-3u"],
    },
    {
        "name": "UPS8 spare parts (negative — explicitly not applicable)",
        "doc": "ups", "category": "negative", "style": "clean",
        "messages": [u("What spare parts are recommended for the UPS system?")],
        "contains_any": ["not applicable", "no spare", "none"] + NOT_FOUND_PHRASES,
    },
    {
        # "capacity" is quantitative vocabulary → SQL-first tolerated.
        "name": "UPS9 standby generator capacity (negative)",
        "doc": "ups", "category": "negative", "style": "clean",
        "messages": [u("What is the capacity of the standby generator backing the UPS?")],
        "tools_exclude": [],
        "negative": True,
        "forbid_regex": r"\b\d+(?:\.\d+)?\s*(?:kva|kw)\b.{0,40}generator|generator.{0,40}\b\d+(?:\.\d+)?\s*(?:kva|kw)\b",
    },
    {
        "name": "UPS10 brand then installer (multi-turn/entity)",
        "doc": "ups", "category": "entity", "style": "multi-turn",
        "messages": [
            u("what brand of UPS is installed?"),
            m("The UPS units are **Tripp Lite** (USA) — 8kVA, 6kVA and 3kVA models across the server room, MDF and IDF rooms."),
            u("and who installed and commissioned them?"),
        ],
        "contains_any": ["integrated ideal", "iis"],
    },
    # ===================== LV Switchgear =====================
    {
        "name": "SWGR1 manufacturer (clean/entity)",
        "doc": "swgr", "category": "entity", "style": "clean",
        "messages": [u("Who manufactured the LV switchgear and distribution boards?")],
        "contains_any": ["gulf dynamic", "gds"],
        "evidence": "Gulf Dynamic",
    },
    {
        "name": "SWGR2 build standard (clean/spec)",
        "doc": "swgr", "category": "spec", "style": "clean",
        "messages": [u("What standard are the LV distribution boards built to?")],
        "contains_any": ["61439"],
    },
    {
        "name": "SWGR3 MDB IP rating (clean/spec)",
        "doc": "swgr", "category": "spec", "style": "clean",
        "messages": [u("What is the IP rating of the main distribution boards?")],
        "contains_any": ["ip 54", "ip54", "ip-54"],
        "evidence": "IP 54",
    },
    {
        # The manual states 400V in the system description and 380/415V on the
        # component BOM — the same nominal LV system; any of the three is a
        # faithful answer.
        "name": "SWGR4 rated voltage and frequency (clean/spec)",
        "doc": "swgr", "category": "spec", "style": "clean",
        "messages": [u("What is the rated operational voltage and frequency of the switchgear?")],
        "has_numbers": [50],
        "contains_any": ["400", "415", "380"],
    },
    {
        "name": "SWGR5 GDS warranty (clean/entity)",
        "doc": "swgr", "category": "entity", "style": "clean",
        "messages": [u("How long is the warranty on the GDS switchgear panels?")],
        "contains_any": ["12 month", "12-month", "one year", "one-year", "1 year", "1-year", "one) year"],
    },
    {
        "name": "SWGR6 power monitoring unit (clean/spec)",
        "doc": "swgr", "category": "spec", "style": "clean",
        "messages": [u("What power monitoring unit is installed in the distribution panels?")],
        "contains_any": ["pm2120", "easylogic"],
    },
    {
        "name": "SWGR7 maintenance-module ground-fault inhibit (clean/maintenance)",
        "doc": "swgr", "category": "maintenance", "style": "clean",
        "messages": [u("How long does the maintenance-module button inhibit ground-fault protection on the switchgear?")],
        "contains_any": ["15 min", "15-min", "fifteen min"],
    },
    {
        "name": "SWGR8 busbar torque values (negative)",
        "doc": "swgr", "category": "negative", "style": "clean",
        "messages": [u("What torque values apply to the busbar connections in the switchgear manual?")],
        "negative": True,
        "forbid_regex": r"\b\d+(?:\.\d+)?\s*nm\b",
    },
    {
        "name": "SWGR9 thermographic survey frequency (negative)",
        "doc": "swgr", "category": "negative", "style": "clean",
        "messages": [u("How often should thermographic surveys be done on the switchgear?")],
        "negative": True,
    },
    # ===================== Zip HydroTaps =====================
    {
        # "units installed" count → SQL-first tolerated (docs fallback checks).
        "name": "ZIP1 unit count (clean/spec)",
        "doc": "zip", "category": "spec", "style": "clean",
        "messages": [u("How many Zip HydroTap units are installed in the building?")],
        "tools_exclude": [],
        "has_number": 8,
        "evidence": "8",
    },
    {
        # "explain" triggers the analyze hijack → no doc-name match → combined
        # SQL + doc-search chain (mid-chain SQL is by design).
        "name": "ZIP2 filter change frequency (conversational/maintenance)",
        "doc": "zip", "category": "maintenance", "style": "conversational",
        "messages": [u("explain in simple terms how often the zip tap water filters need changing")],
        "tools_exclude": [],
        "contains_any": ["6 month", "six month", "6-month"],
    },
    {
        "name": "ZIP3 supplier and service email (clean/entity)",
        "doc": "zip", "category": "entity", "style": "clean",
        "messages": [u("Who is the local supplier for the Zip taps and what is their service email?")],
        "contains_all": ["culligan"],
        "contains_any": ["service@culligan.ae"],
    },
    {
        "name": "ZIP4 replacement filter code (clean/spec)",
        "doc": "zip", "category": "spec", "style": "clean",
        "messages": [u("What is the replacement filter order code for the Zip HydroTaps?")],
        "contains_any": ["91290"],
        "evidence": "91290",
    },
    {
        "name": "ZIP5 booster heater rating (clean/spec)",
        "doc": "zip", "category": "spec", "style": "clean",
        "messages": [u("What is the booster heater power rating of the Zip taps?")],
        "has_number": 2.2,
    },
    {
        "name": "ZIP6 Culligan warranty (clean/entity)",
        "doc": "zip", "category": "entity", "style": "clean",
        "messages": [u("What warranty did Culligan give on the Zip tap installation?")],
        "contains_any": ["24 month", "24-month", "two year", "two-year", "2 year", "2-year"],
    },
    {
        "name": "ZIP7 after long shutdown (conversational/maintenance)",
        "doc": "zip", "category": "maintenance", "style": "conversational",
        "messages": [u("one of the zip taps was switched off for ages — anything I should do before people drink from it?")],
        "contains_any": ["5 min", "five min", "5-min"],
    },
    {
        # Dual-source: db_circuits.room_area lists zip tap locations too, and
        # they agree with the manual — either source is a correct answer path.
        "name": "ZIP8 locations as table (format/location)",
        "doc": "zip", "category": "location", "style": "format",
        "messages": [u("give me the locations of all the Zip taps as a table")],
        "tools_any": [], "tools_exclude": [],
        "contains_any": ["coffee"],
        "min_rows": 3, "row_marker": "offee",
    },
    {
        "name": "ZIP9 energy star rating (negative)",
        "doc": "zip", "category": "negative", "style": "clean",
        "messages": [u("What is the energy star rating of the Zip HydroTap units?")],
        "negative": True,
    },
    {
        # Dual-source: circuit schedule answers locations correctly too.
        "name": "ZIP10 count then locations (multi-turn/location)",
        "tools_any": [], "tools_exclude": [],
        "doc": "zip", "category": "location", "style": "multi-turn",
        "messages": [
            u("how many zip taps do we have?"),
            m("There are **8 Zip HydroTap units** installed, supplied by Culligan International (Emirates)."),
            u("where exactly are they installed?"),
        ],
        "contains_any": ["coffee"],
    },
    # ===================== Sanitary Accessories =====================
    {
        "name": "SAN1 supplier/installer (clean/entity)",
        "doc": "san", "category": "entity", "style": "clean",
        "messages": [u("Who supplied and installed the sanitary accessories?")],
        "contains_any": ["khansaheb"],
        "evidence": "Khansaheb",
    },
    {
        "name": "SAN2 A05 mirror size (clean/spec)",
        "doc": "san", "category": "spec", "style": "clean",
        "messages": [u("What size is the A05 mirror and who supplied it?")],
        "has_numbers": [600, 1000],
        "contains_any": ["aquazone"],
    },
    {
        "name": "SAN3 SIGMA30 flush plate location (clean/location)",
        "doc": "san", "category": "location", "style": "clean",
        "messages": [u("Where is the Geberit SIGMA30 flush plate installed?")],
        "contains_any": ["gf male", "ground floor", "male & female", "male and female"],
    },
    {
        "name": "SAN4 fit-out warranty (clean/entity)",
        "doc": "san", "category": "entity", "style": "clean",
        "messages": [u("What is the warranty on the sanitary fit-out?")],
        "contains_any": ["1 year", "one year", "one-year", "12 month", "12-month", "1-year", "one) year"],
    },
    {
        "name": "SAN5 hand dryer lead time (conversational/maintenance)",
        "doc": "san", "category": "maintenance", "style": "conversational",
        "messages": [u("if a hand dryer breaks, how long does a replacement take to arrive?")],
        "contains_any": ["4-5 week", "4–5 week", "4 to 5 week", "5 week", "four to five week"],
    },
    {
        "name": "SAN6 700mm grab rail location (clean/location)",
        "doc": "san", "category": "location", "style": "clean",
        "messages": [u("Where is the 700mm foldable grab rail installed?")],
        "contains_any": ["level 3", "disabled"],
    },
    {
        "name": "SAN7 spare material (negative — explicitly not applicable)",
        "doc": "san", "category": "negative", "style": "clean",
        "messages": [u("Was spare material supplied for the sanitary accessories?")],
        "contains_any": ["not applicable", "no spare", "none"] + NOT_FOUND_PHRASES,
    },
    # ===================== Water Heaters =====================
    {
        # "water heater" is mdb_calc vocabulary -> SQL-first tolerated.
        "name": "WH1 30L heater count and make (clean/spec)",
        "tools_exclude": [],
        "doc": "wh", "category": "spec", "style": "clean",
        "messages": [u("How many 30L water heaters are installed and whose make are they?")],
        "has_number": 3,
        "contains_any": ["ariston"],
        "evidence": "Ariston",
    },
    {
        # Same noun collision as WH1.
        "name": "WH2 100L heater locations (clean/location)",
        "tools_exclude": [],
        "doc": "wh", "category": "location", "style": "clean",
        "messages": [u("Where are the 100L water heaters located?")],
        "contains_any": ["pantry", "kitchen"],
    },
    {
        # Same noun collision as WH1.
        "name": "WH3 power ratings 30L vs 100L (clean/spec)",
        "tools_exclude": [],
        "doc": "wh", "category": "spec", "style": "clean",
        "messages": [u("What are the power ratings of the 30L and 100L water heaters?")],
        "has_numbers": [1.5, 3.0],
    },
    {
        "name": "WH4 anode replacement (conversational/maintenance)",
        "doc": "wh", "category": "maintenance", "style": "conversational",
        "messages": [u("how often should the magnesium anode be replaced on the water heaters?")],
        "contains_any": ["two year", "2 year", "2-year"],
    },
    {
        "name": "WH5 100L cylinder warranty (clean/entity)",
        "doc": "wh", "category": "entity", "style": "clean",
        "messages": [u("What warranty do the 100L hot water cylinders carry?")],
        "contains_any": ["5 year", "5-year", "five year", "five-year"],
    },
    {
        "name": "WH6 100L manufacturer — variant spellings (clean/entity)",
        "doc": "wh", "category": "entity", "style": "clean",
        "messages": [u("Who manufactured the 100L water heaters?")],
        "contains_any": ["heatrae sadia", "heater sadie", "heatrae"],
    },
    {
        "name": "WH7 30L element part number typos (typo/spec)",
        "doc": "wh", "category": "spec", "style": "typo",
        "messages": [u("wat is the part numbr of the 30L ariston heating element")],
        "contains_any": ["65114894"],
    },
    {
        # "kWh consumption" is quantitative vocabulary → SQL-first tolerated.
        "name": "WH8 annual kWh consumption (negative)",
        "doc": "wh", "category": "negative", "style": "clean",
        "messages": [u("What is the annual kWh consumption of the water heaters?")],
        "tools_exclude": [],
        "negative": True,
        "forbid_regex": r"\b\d[\d,]*(?:\.\d+)?\s*kwh\b",
    },
    {
        "name": "WH9 100L count then manufacturer (multi-turn/entity)",
        "doc": "wh", "category": "entity", "style": "multi-turn",
        "messages": [
            u("how many 100L water heaters are there?"),
            m("There are **2 × 100L water heaters** — one in the Level 01 Block C pantry and one in the GF Block B kitchen."),
            u("and who manufactured them?"),
        ],
        "contains_any": ["heatrae sadia", "heater sadie", "heatrae"],
    },
    # ============ Routing boundary traps (both directions) ============
    {
        "name": "RB1 DB warranty → docs not SQL",
        "doc": "swgr", "category": "routing", "style": "clean",
        "messages": [u("what warranty do the distribution boards have?")],
        "tools_any": DOC_TOOLS,
        "tools_exclude": ["query_structured_data"],
        # "(one) year" covers the "01 (one) year" contract phrasing
        "contains_any": ["12 month", "one year", "1 year", "1-year", "one) year", "12-month"],
    },
    {
        "name": "RB2 panel feed → SQL not docs",
        "doc": "sql", "category": "routing", "style": "clean",
        "messages": [u("which panel feeds SMDB-B-4F?")],
        "tools_any": ["query_structured_data"],
        "tools_exclude": ["search_documents", "analyze_document"],
        "contains_any": ["mdb-c-g2"],
    },
    {
        "name": "RB3 MDB-C-G2 load → SQL despite manual naming it",
        "doc": "sql", "category": "routing", "style": "clean",
        "messages": [u("what is the total connected load of MDB-C-G2?")],
        "tools_any": ["query_structured_data"],
        "tools_exclude": ["search_documents", "analyze_document"],
        "has_number": 1445.45,
    },
    {
        "name": "RB4 MDB construction spec → docs despite panel name",
        "doc": "swgr", "category": "routing", "style": "clean",
        "messages": [u("what internal separation form are the MDBs constructed to?")],
        "tools_any": DOC_TOOLS,
        "tools_exclude": ["query_structured_data"],
        "contains_any": ["form 2", "form-2", "form ii"],
    },
    {
        "name": "RB5 summarize switchgear manual → analyze_document",
        "doc": "swgr", "category": "routing", "style": "conversational",
        "messages": [u("summarize the LV switchgear manual for me")],
        "tools_any": ["analyze_document"],
        "tools_exclude": ["query_structured_data"],
    },
]


def persist(record):
    os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)
    with open(RESULTS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main():
    q_filters = sys.argv[1:]
    mrange = re.fullmatch(r"(\d+)-(\d+)", q_filters[0]) if len(q_filters) == 1 else None
    if mrange:
        cases = CASES[int(mrange.group(1)) - 1:int(mrange.group(2))]
    else:
        cases = [c for c in CASES
                 if not q_filters or any(f.lower() in c["name"].lower() for f in q_filters)]

    run_ts = datetime.now(timezone.utc).isoformat()
    passed = 0
    by_doc, by_cat = {}, {}
    for case in cases:
        tools, answer = run(case["messages"])
        failures = check(case, tools, answer)
        r_fail = check_retrieval(case)
        if r_fail:
            failures.append(r_fail)
        status = "PASS" if not failures else "FAIL"
        if not failures:
            passed += 1
        by_doc.setdefault(case["doc"], [0, 0])[0 if status == "PASS" else 1] += 1
        by_cat.setdefault(case["category"], [0, 0])[0 if status == "PASS" else 1] += 1
        print(f"\n[{status}] {case['name']}")
        print(f"  q: {case['messages'][-1]['content']}")
        print(f"  tools={tools}")
        for f in failures:
            print(f"  - {f}")
        if failures:
            print(f"  answer ({len(answer)} chars):")
            print("\n".join("  | " + l for l in answer.splitlines()[:15]))
        persist({"run": run_ts, "name": case["name"], "doc": case["doc"],
                 "category": case["category"], "style": case["style"],
                 "status": status, "tools": tools, "failures": failures,
                 "answer": answer[:600]})

    print(f"\n{'='*50}")
    print(f"{passed}/{len(cases)} cases passed")
    print("by doc:      " + ", ".join(f"{k}={v[0]}/{v[0]+v[1]}" for k, v in sorted(by_doc.items())))
    print("by category: " + ", ".join(f"{k}={v[0]}/{v[0]+v[1]}" for k, v in sorted(by_cat.items())))
    sys.exit(0 if passed == len(cases) else 1)


if __name__ == "__main__":
    main()
