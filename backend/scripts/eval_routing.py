"""
E2E routing + answer-formatting regression suite (drives stream_response
directly, no HTTP). Covers two pipeline layers of the consistency-audit matrix:

  - TOOL ROUTING: which tool gets called, asserted via tool_start events.
    Historically the summarization heuristic hijacked quantitative questions
    containing "explain"/"review"/"overview" into analyze_document (dead end).
  - ANSWER FORMATTING: the final streamed text. Must never leak internal
    notes ("SQL: `...`", "IMPORTANT (for interpreting these results)"), never
    emit raw HTML, must surface corrected values as current-vs-original, and
    must render FULL tables when a breakdown/list/Excel format is asked for.

Phrasing styles covered: clean/technical, conversational + summarize-words,
multi-turn follow-ups with pronouns (real message history), explicit format
requests, typos/informal, negative questions about entities that don't exist,
and a genuine-summarization control that must still reach analyze_document.

Case spec keys:
  messages        list of {role, content} — multi-turn histories are real
  tools_include   every tool named here must appear in tool_start events
  tools_exclude   none of these may appear
  contains_any    at least one string must appear in the final answer (casefold)
  contains_all    every string must appear (casefold)
  has_number      numeric value that must appear in the answer (tol 0.01,
                  commas tolerated)
  min_rows        at least this many markdown table rows containing row_marker
  row_marker      substring identifying data rows (default "DB-04(B)-SP")
  negative        True → not-found phrasing required, and no kW/amp figure may
                  be attached to the nonexistent entity
  require_digits  True → answer must contain at least one digit

Global checks applied to EVERY case: no leaked internals, no raw HTML tags,
answer non-empty.

Usage: cd backend && venv/Scripts/python scripts/eval_routing.py
"""
import os, sys, re, json, time
BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)
from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND, ".env"))
from supabase import create_client

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
sd = sb.table("structured_data").select("user_id, table_name, columns").execute()
user_id = sd.data[0]["user_id"]
structured_tables = [{"table_name": t["table_name"], "columns": t["columns"]} for t in sd.data]
docs = sb.table("documents").select("id, file_name").eq("user_id", user_id).limit(5).execute()
has_documents = bool(docs.data)
doc_name_hint = docs.data[0]["file_name"].rsplit(".", 1)[0] if has_documents else None
print(f"user={user_id[:8]} has_documents={has_documents} docs={[d['file_name'] for d in docs.data]}")

from app.services.openai_client import stream_response


def _run_once(messages):
    tools_used, text = [], []
    for evt, data in stream_response(
        messages=messages,
        user_id=user_id, supabase_client=sb,
        has_documents=has_documents, has_structured_data=True,
        structured_tables=structured_tables,
    ):
        if evt == "tool_start":
            tools_used.append(json.loads(data).get("tool"))
        elif evt == "token":
            text.append(data)
    return tools_used, "".join(text)


def run(messages, attempts=3, delay=5):
    """Retry transient network/API failures — an eval run must not be killed
    by one TLS blip to Supabase or Gemini."""
    for i in range(attempts):
        try:
            return _run_once(messages)
        except Exception as e:
            if i == attempts - 1:
                raise
            print(f"  (transient error, retry {i + 1}/{attempts - 1}: {type(e).__name__}: {e})")
            time.sleep(delay * (i + 1))


LEAK_STRINGS = ["IMPORTANT (for interpreting", "SQL: `", "sql: `"]
HTML_TAGS = ["<table", "<tr>", "<td", "<th", "<br", "<div", "<span"]
NOT_FOUND_PHRASES = [
    "not found", "no panel", "does not exist", "doesn't exist", "couldn't find",
    "could not find", "no data", "no record", "no results", "not present",
    "unable to find", "no information", "not appear", "no match", "not listed",
    "don't have", "do not have", "not contain", "doesn't contain", "no entry",
    "isn't", "is not in", "not available", "not exist",
]


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
            if l.strip().startswith("|") and marker in l]


def check(case, tools, answer):
    failures = []
    a = answer.lower()

    # Global answer-formatting invariants
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

    for t in case.get("tools_include", []):
        if t not in tools:
            failures.append(f"tool '{t}' not used (tools={tools})")
    for t in case.get("tools_exclude", []):
        if t in tools:
            failures.append(f"tool '{t}' must NOT be used (tools={tools})")

    for s in case.get("contains_all", []):
        if s.lower() not in a:
            failures.append(f"answer missing '{s}'")
    if case.get("contains_any"):
        if not any(s.lower() in a for s in case["contains_any"]):
            failures.append(f"answer contains none of {case['contains_any']}")

    if "has_number" in case:
        want = case["has_number"]
        if not any(abs(v - want) < 0.01 for v in answer_numbers(answer)):
            failures.append(f"number {want} not in answer")

    if case.get("require_digits") and not any(ch.isdigit() for ch in answer):
        failures.append("answer has no digits")

    if "min_rows" in case:
        marker = case.get("row_marker", "DB-04(B)-SP")
        rows = table_rows(answer, marker)
        if len(rows) < case["min_rows"]:
            failures.append(f"expected >= {case['min_rows']} table rows with "
                            f"'{marker}', got {len(rows)}")

    if case.get("negative"):
        if not any(p in a for p in NOT_FOUND_PHRASES):
            failures.append("no not-found phrasing in answer")
        # a kW/amp figure attached to a nonexistent entity is a hallucination
        if re.search(r"\d+(?:\.\d+)?\s*(?:kw|kva|kwh|watts?|amps?|a\b)", a):
            failures.append("answer attaches a load/rating figure to a "
                            "nonexistent entity")

    return failures


def u(content):
    return {"role": "user", "content": content}


def m(content):
    return {"role": "assistant", "content": content}


CASES = [
    # -- Routing regressions (single turn) --------------------------------
    {
        "name": "R1 conversational-explain hijack (user-reported 2026-07-07)",
        "messages": [u("whats the total load for block B? and what is this max demand? explain me in simple terms")],
        "tools_include": ["query_structured_data"],
        "tools_exclude": ["analyze_document"],
        "contains_any": ["1234", "1,234", "1445", "1,445"],
    },
    {
        "name": "R2 genuine summarize control",
        "messages": [u(f"summarize {doc_name_hint}")],
        "tools_include": ["analyze_document"],
        "skip_if_no_docs": True,
    },
    {
        "name": "R3 conversational 'review the loads'",
        "messages": [u("can you review the loads for the 4th floor of Block B?")],
        "tools_include": ["query_structured_data"],
        "tools_exclude": ["analyze_document"],
        "require_digits": True,
    },
    {
        "name": "R4 conversational 'overview of'",
        "messages": [u("give me an overview of the FCU loads on the 4th floor of block B")],
        "tools_include": ["query_structured_data"],
        "tools_exclude": ["analyze_document"],
        "require_digits": True,
    },
    {
        "name": "R5 typos/informal",
        "messages": [u("how many FCU's conected to 4th flor block B")],
        "tools_include": ["query_structured_data"],
        "has_number": 29,
    },
    {
        "name": "R6 conversational 'help me understand ... in simple words'",
        "messages": [u("help me understand the max demand of MDB-C-G2 in simple words")],
        "tools_include": ["query_structured_data"],
        "tools_exclude": ["analyze_document"],
        "has_number": 1156.36,
    },
    {
        "name": "R7 casual greeting routes to no tool",
        "messages": [u("hello! how are you today?")],
        "tools_exclude": ["query_structured_data", "analyze_document",
                          "search_documents", "explore_knowledge_base"],
    },
    # -- Negative / absent entities ---------------------------------------
    {
        "name": "N1 nonexistent panel",
        "messages": [u("what is the load of panel XYZ-99?")],
        "negative": True,
    },
    {
        "name": "N2 nonexistent panel rating",
        "messages": [u("what is the incomer rating of panel ABC-123?")],
        "negative": True,
    },
    # -- Answer formatting (single turn) ----------------------------------
    {
        "name": "F1 corrected value surfaced (DEWA-struck MDL)",
        "messages": [u("what is the maximum demand of MDB-C-G2?")],
        "tools_include": ["query_structured_data"],
        "has_number": 1156.36,
    },
    {
        "name": "F2 full breakdown table on explicit ask",
        "messages": [u("give me a breakdown of the FCUs on the 4th floor of Block B as a table")],
        "tools_include": ["query_structured_data"],
        "min_rows": 16,
        "has_number": 29,
    },
    {
        "name": "F3 list panels as a table",
        "messages": [u("list all panels on the 4th floor of Block B as a table")],
        "tools_include": ["query_structured_data"],
        "contains_all": ["SMDB-B-4F", "DB-04(B)-LP-02", "DB-04(B)-SP-01", "DB-04(B)-SP-02"],
    },
    # -- Multi-turn follow-ups (real message history) ----------------------
    {
        "name": "M1 count then 'give a breakdown of it'",
        "messages": [
            u("how many FCUs are connected on the 4th floor of Block B?"),
            m("There are **29 FCU points** on the 4th floor of Block B, "
              "across panels DB-04(B)-SP-01 and DB-04(B)-SP-02."),
            u("give a breakdown of it"),
        ],
        "tools_include": ["query_structured_data"],
        "tools_exclude": ["analyze_document"],
        "min_rows": 16,
    },
    {
        "name": "M2 count then 'list it down for me'",
        "messages": [
            u("how many FCUs are connected on the 4th floor of Block B?"),
            m("There are **29 FCU points** on the 4th floor of Block B, "
              "across panels DB-04(B)-SP-01 and DB-04(B)-SP-02."),
            u("list it down for me"),
        ],
        "tools_include": ["query_structured_data"],
        "tools_exclude": ["analyze_document"],
        "min_rows": 16,
    },
    {
        "name": "M3 total then 'explain it in simple terms'",
        "messages": [
            u("what is the total connected load of Block B?"),
            m("The total connected load of Block B is **1234.30 kW**, summed "
              "over the topmost panels of the block (SMDBs and MCCs fed from "
              "outside the block)."),
            u("explain it in simple terms"),
        ],
        "tools_exclude": ["analyze_document"],
        "contains_any": ["1234", "1,234"],
    },
    {
        "name": "M4 corrected value follow-up 'is that the corrected value?'",
        "messages": [
            u("what is the maximum demand of MDB-C-G2?"),
            m("The maximum demand load of MDB-C-G2 is **1156.36 kW** "
              "(corrected by DEWA from the printed 1120.40 kW)."),
            u("is that the corrected value?"),
        ],
        "contains_any": ["1120", "corrected", "dewa", "revised", "supersed", "yes"],
    },
    {
        "name": "M5 count then 'give me that in an Excel sheet format'",
        "messages": [
            u("how many FCUs are connected on the 4th floor of Block B?"),
            m("There are **29 FCU points** on the 4th floor of Block B, "
              "across panels DB-04(B)-SP-01 and DB-04(B)-SP-02."),
            u("can you give me that in an Excel sheet format"),
        ],
        "tools_include": ["query_structured_data"],
        "tools_exclude": ["analyze_document"],
        "min_rows": 16,
    },
    {
        # Regression (run 2026-07-12): pronoun follow-up with a summarize-word
        # and NO quantitative word in the last message was forced into
        # analyze_document — the guard must find the quantitative context in
        # the conversation history.
        "name": "M6 MDL then 'explain that to me in simple words'",
        "messages": [
            u("what is the maximum demand of MDB-C-G2?"),
            m("The maximum demand load of MDB-C-G2 is **1156.36 kW** "
              "(corrected by DEWA from the printed 1120.40 kW)."),
            u("explain that to me in simple words"),
        ],
        "tools_exclude": ["analyze_document"],
        "contains_any": ["1156", "1,156"],
    },
]


def main():
    # Optional CLI filter: any args are substrings matched against case names,
    # e.g. `eval_routing.py R3 R4 M6` runs only those cases.
    name_filters = sys.argv[1:]
    cases = [c for c in CASES
             if not name_filters or any(f.lower() in c["name"].lower() for f in name_filters)]
    passed, failed, skipped = 0, 0, 0
    for case in cases:
        if case.get("skip_if_no_docs") and not has_documents:
            skipped += 1
            print(f"\n[SKIP] {case['name']} — no documents")
            continue
        tools, answer = run(case["messages"])
        failures = check(case, tools, answer)
        status = "PASS" if not failures else "FAIL"
        if failures:
            failed += 1
        else:
            passed += 1
        print(f"\n[{status}] {case['name']}")
        print(f"  q: {case['messages'][-1]['content']}")
        print(f"  tools={tools}")
        for f in failures:
            print(f"  - {f}")
        if failures:
            print(f"  answer ({len(answer)} chars):")
            print("\n".join("  | " + l for l in answer.splitlines()[:20]))

    print(f"\n{passed} passed, {failed} failed, {skipped} skipped / {len(cases)} cases")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
