"""Scorecard for the doc-QA audit (DOC_QA_PLAYBOOK.md step 7).

Reads scripts/results/doc_qa_results.jsonl (append-only, one line per case
execution) and prints:
  - per-document and per-category case counts + pass rate, latest run vs first
  - failure-layer breakdown (ROUTING / RETRIEVAL / answer) across all runs
  - initial vs final pass rate

"Latest result" = the last banked line per case name; "first result" = the
first banked line per case name (the initial state before fixes).

Usage: cd backend && venv/Scripts/python scripts/doc_qa_scorecard.py
"""
import json
import os
from collections import OrderedDict, Counter

PATH = os.path.join(os.path.dirname(__file__), "results", "doc_qa_results.jsonl")


def classify_layer(failures):
    layers = set()
    for f in failures:
        if f.startswith("ROUTING"):
            layers.add("routing")
        elif f.startswith("RETRIEVAL"):
            layers.add("retrieval")
        else:
            layers.add("answer")
    return layers


def main():
    rows = [json.loads(l) for l in open(PATH, encoding="utf-8")]
    first, last = OrderedDict(), OrderedDict()
    for r in rows:
        first.setdefault(r["name"], r)
        last[r["name"]] = r

    def rate(results):
        n = len(results)
        p = sum(1 for r in results if r["status"] == "PASS")
        return f"{p}/{n} ({100*p/n:.0f}%)" if n else "n/a"

    print(f"Doc-QA scorecard — {len(rows)} banked executions, {len(last)} distinct cases\n")
    print(f"initial pass rate (first execution per case): {rate(list(first.values()))}")
    print(f"final   pass rate (latest execution per case): {rate(list(last.values()))}\n")

    for key in ("doc", "category", "style"):
        agg = {}
        for r in last.values():
            agg.setdefault(r.get(key, "?"), []).append(r)
        print(f"by {key}:")
        for k in sorted(agg):
            print(f"  {k:12} {rate(agg[k])}")
        print()

    layer_counts = Counter()
    for r in rows:
        if r["status"] == "FAIL":
            for layer in classify_layer(r["failures"]):
                layer_counts[layer] += 1
    print("failure layers (all executions, incl. since-fixed):")
    for k, v in layer_counts.most_common():
        print(f"  {k:10} {v}")

    still = [r["name"] for r in last.values() if r["status"] == "FAIL"]
    print(f"\nstill failing ({len(still)}):")
    for n in still:
        print(f"  - {n}")


if __name__ == "__main__":
    main()
