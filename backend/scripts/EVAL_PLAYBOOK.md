# RAG Consistency Audit Playbook

## How to launch (copy-paste this as the goal)

```
/goal Run the RAG consistency audit defined in backend/scripts/EVAL_PLAYBOOK.md: extend the three eval suites in backend/scripts to cover the full matrix in that playbook — every question category, every phrasing style (including conversational wording like "explain in simple terms", multi-turn follow-ups like "give a breakdown of it", typos, and negative questions about things that don't exist), across all three pipeline layers (tool routing, SQL generation, answer formatting) — minimum 40 cases. Fix every failure at the layer it belongs to, add each fix as a permanent regression case, and stop only when two consecutive full runs pass 100%. Finish with the scorecard defined in the playbook.
```

Purpose: verify the load-schedule RAG pipeline gives correct, consistent answers
across ALL question types, phrasings, and pipeline layers — and fix it when it
doesn't. This file is the reference for the `/goal` that runs the audit; it is
self-contained so a fresh session can execute it.

## Why a matrix, not a question list

A previous 20-question audit passed 22/22 and a user-facing bug STILL appeared
the next day ("whats the total load for block B? ... explain me in simple
terms" → "No document matching ... found"). The questions were fine; the bug
was in a different LAYER (tool routing) triggered by a PHRASING style
(conversational "explain") the audit never used. Coverage must span all three
dimensions below. A failure in any cell of the matrix is a real user-facing bug.

## Dimension 1 — Pipeline layers (each needs its own harness)

1. **Tool routing** (`app/services/openai_client.py: stream_response`) — which
   tool gets called. Test by driving `stream_response` directly and asserting
   on `tool_start` events (see `eval_routing.py`).
2. **SQL generation** (`app/services/sql_tool.py: execute_sql_query`) — is the
   generated SQL right. Test by comparing against HAND-WRITTEN ground-truth SQL
   over the same data (see `eval_rag_vs_truth.py`). Ground truth is always
   deterministic SQL, never another LLM.
3. **Answer formatting** (final Gemini call with OUTPUT_FORMAT_RULES) — test by
   asserting on the streamed final text: full tables when asked, no leaked
   internals, corrections surfaced.

## Dimension 2 — Question categories (every one needs cases)

| Category | Example | Known trap |
|---|---|---|
| Count via quantity column | "how many FCUs on 4th floor Block B?" → 29 | must SUM `points`, not COUNT(*) |
| Breakdown / list / "Excel format" | "give me the breakdown" | must select room_area + points, ALL 16 rows, total row |
| Hierarchy totals | "total load of Block B" → 1234.30 | `fed_from` parents include children; sum topmost only |
| Single-value lookup | "incomer rating of DB-04(B)-SP-02?" → 100 | should include notes column |
| Relationship | "which panel feeds SMDB-B-4F?" → MDB-C-G2 | |
| Superlative | "highest-loaded SMDB in Block B?" → SMDB-B-6F | |
| Cross-table join | floor/block filters need panels ⋈ db_circuits | never drop the equipment-type filter |
| Corrected values | "max demand of MDB-C-G2?" → 1156.36 | 1120.40 is DEWA-struck/superseded — never present it as current |
| Ambiguous framing | "total load for Block B" | Block B alone (1234.30) vs serving board MDB-C-G2 (1445.45) — answer should pick one and ideally note the distinction |
| Negative / absent | "load of panel XYZ-99?" | graceful "not found", no hallucinated number |

## Dimension 3 — Phrasing styles (each category × several of these)

- Clean/technical ("what is the TCL of ...")
- Conversational + summarize-words: "... **explain me in simple terms**",
  "can you review the loads for ...", "give me an overview of ..." — these
  historically hijacked routing into analyze_document (dead end)
- Follow-up with pronouns, multi-turn: first ask the count, then "**give a
  breakdown of it**", "**list it down for me**" — pass real message history to
  stream_response, not a single message
- Explicit format requests: "in an Excel sheet format", "as a table"
- Typos/informal: "how many FCU's conected to 4th flor block B"
- Genuine summarization control case: "summarize <doc name>" must still route
  to analyze_document

## Known failure modes already fixed (regression cases must stay green)

1. Breakdown SQL selected only identifier columns (no room_area/points).
2. Equipment-type filter (FCU) dropped when a floor/block filter was present.
3. Generated SQL truncated mid-string — thinking models spend "thought" tokens
   from max_output_tokens; keep it ≥8192.
4. Summarization heuristic hijacked quantitative questions containing
   "explain"; fixed with quantitative-word guard. Dead-ended analyze_document
   now falls back to SQL when tabular data exists.
5. Internal notes leaked verbatim into answers ("IMPORTANT (for interpreting
   these results)...", "SQL: ..."). Final answers must never contain these.
6. Provenance notes in data cells echoed verbatim; but SUBSTANTIVE corrections
   (struck/superseded values) must be surfaced as current-vs-original.
7. Data gap: SMDB-B-6F and MCC-B-RS were missing from panels (added 2026-07-09
   from the MDB-C-G2 feeder schedule, provenance in their notes fields).

## Data facts for ground truth (verify against live data before trusting)

- Tables in Supabase `structured_data`: panels (103), smdb_feeders (238),
  db_circuits (2251), mdb_calc (23). Owner user id starts 01e29250.
- Block B 4F FCUs: 16 circuits, 29 points, across DB-04(B)-SP-01/-02.
- Block B topmost-panels TCL sum: 1234.30 kW. MDB-C-G2 (serves Block B + some
  Block C/shared): TCL 1445.45, MDL 1156.36 (current; 1120.40 superseded).
- Known unresolved: 6F lab DBs reference parent "SMDB-B-6F-LAB" (4 spellings)
  that exists in no table → possible ~12.4 kW double count. Do NOT normalize
  without user confirmation.

## Protocol

1. Backend running on :8001 (`cd backend && venv/Scripts/python -m uvicorn
   app.main:app --port 8001`). Evals import the services directly, so the
   server is only needed for manual UI checks — but after ANY backend code
   change, kill ALL stray uvicorn/python processes and restart (no --reload;
   zombie processes serve stale code).
2. Extend the three eval scripts until the matrix is covered — minimum 40
   cases total, including at least 5 multi-turn follow-ups and 3 negative
   cases. Reuse the existing checker patterns (numeric cell within 0.01 of
   ground truth; expected strings present; tools-used assertions).
3. Run everything: `cd backend && venv/Scripts/python scripts/eval_rag_vs_truth.py
   && venv/Scripts/python scripts/eval_routing.py && venv/Scripts/python
   scripts/eval_sql_breakdown.py`.
4. On failure: classify the layer → fix at that layer (sql_tool prompt /
   OUTPUT_FORMAT_RULES / routing guard / data). Data fixes are ADDITIVE ONLY
   with provenance notes — never delete or overwrite user rows. Every fix gets
   a permanent regression case.
5. Done when: two consecutive full runs pass 100%.
6. Report a scorecard: cases per category/layer, initial vs final pass rate,
   fixes applied, data gaps found, remaining caveats. Commit the changes.
