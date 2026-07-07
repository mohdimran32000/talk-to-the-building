"""
E2E tool-routing regression tests (drives stream_response directly, no HTTP).

Case 1 (user-reported bug, 2026-07-07): a quantitative question containing
"explain" ("whats the total load for block B? ... explain me in simple terms")
was hijacked by the summarization heuristic into analyze_document and
dead-ended with "No document matching ... found". It must route to
query_structured_data and produce a numeric answer.

Case 2: a genuine "summarize <name>" request must still be forced to
analyze_document (the heuristic's original purpose).

Usage: cd backend && venv/Scripts/python scripts/eval_routing.py
"""
import os, sys
BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)
from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND, ".env"))
import json
from supabase import create_client

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
sd = sb.table("structured_data").select("user_id, table_name, columns").execute()
user_id = sd.data[0]["user_id"]
structured_tables = [{"table_name": t["table_name"], "columns": t["columns"]} for t in sd.data]
docs = sb.table("documents").select("id, file_name").eq("user_id", user_id).limit(5).execute()
has_documents = bool(docs.data)
print(f"user={user_id[:8]} has_documents={has_documents} docs={[d['file_name'] for d in docs.data]}")

from app.services.openai_client import stream_response

def run(question):
    tools_used, text = [], []
    for evt, data in stream_response(
        messages=[{"role": "user", "content": question}],
        user_id=user_id, supabase_client=sb,
        has_documents=has_documents, has_structured_data=True,
        structured_tables=structured_tables,
    ):
        if evt == "tool_start":
            tools_used.append(json.loads(data).get("tool"))
        elif evt == "token":
            text.append(data)
    return tools_used, "".join(text)

# Case 1: the reported failure
q1 = "whats the total load for block B? and what is this max demand? explain me in simple terms"
tools, answer = run(q1)
ok1 = ("query_structured_data" in tools
       and "no document matching" not in answer.lower()
       and any(ch.isdigit() for ch in answer))
print(f"\n[{'PASS' if ok1 else 'FAIL'}] {q1}")
print(f"  tools={tools}")
print(f"  answer ({len(answer)} chars):\n" + "\n".join("  | " + l for l in answer.splitlines()[:25]))

# Case 2: genuine summarization must still route to analyze_document
if has_documents:
    name_hint = docs.data[0]["file_name"].rsplit(".", 1)[0]
    q2 = f"summarize {name_hint}"
    tools2, answer2 = run(q2)
    ok2 = "analyze_document" in tools2
    print(f"\n[{'PASS' if ok2 else 'FAIL'}] {q2}")
    print(f"  tools={tools2}")
else:
    ok2 = True
    print("\n[SKIP] no documents — summarization regression not applicable")

sys.exit(0 if (ok1 and ok2) else 1)
