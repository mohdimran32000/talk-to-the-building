"""test_excerpt_window.py - the excerpt of a long retrieved chunk no longer depends on the process's
string-hash seed, and a hit the window rule skipped though no kept window held it is kept (wave 3,
G2, 2026-10-03).

THE DEFECT. `openai_client._windowed_excerpt` trims a chunk over 4,000 characters to its head plus
windows around query-term hits. It took the terms longest first - and terms of EQUAL length in the
order of a Python set, which changes with each process's hash seed (PYTHONHASHSEED). So the same
question over the same chunk could keep one window in one process and another in the next, and a
fact sitting in the window it dropped reached the answer writer in some runs and not in others.
And a hit within 1,300 characters of an already kept window's START was skipped even when that
window did not contain it - the hit fell in the gap.

THE FIX. Terms of equal length are taken by a fixed key: fewest hits in this chunk first (the
rarest, most telling term), then alphabetically. And a hit the start-distance rule skips although
no kept window contains it widens the nearest kept window's text to reach it; the kept START points
- which every later decision reads - do not change, so every window today's rule keeps under the
same order is still inside the excerpt. A chunk of up to 4,000 characters, a chunk with no hit
beyond its head, and a chunk whose decisions never tie or skip are cut exactly as before.

Every chunk, word and query here is invented. Every check marked RED fails against the code as it
stood before this change: it runs the real function in ten processes, one per hash seed 0-9.

Run:
    venv/Scripts/python -X utf8 tests/test_excerpt_window.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services import openai_client  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {'' if cond else detail}")
    if not cond:
        FAILS.append(name)


FILL = "QZ-row,bin,gizmo deck,tray\n"   # holds none of the query's terms


def chunk(placements, length=6200):
    """`length` characters of filler with each word written at its exact offset."""
    text = list((FILL * (length // len(FILL) + 1))[:length])
    for pos, word in placements:
        text[pos:pos + len(word)] = list(word)
    return "".join(text)


# Three query terms of five letters tie on length; "stored" is longer and printed nowhere.
QUERY = "where are the spare cells stored?"
# A: the rarer tied term's first hit (marked QZ-71) lies 900 characters before the commoner one's,
# so whichever is taken first decides the window. Rarest first keeps both.
A = chunk([(3400, "spare QZ-71"), (5600, "spare"),
           (4300, "cells"), (4400, "cells"), (4500, "cells"), (4600, "cells"), (4650, "cells")])
# B: the same, but "cells" is the rarer term: its window is kept first, and the first "spare" hit
# (marked QZ-72) falls outside it yet within 1,300 characters of its start - skipped today in
# the processes that take "cells" first; widened to now.
B = chunk([(3400, "spare QZ-72"), (5600, "spare"), (5700, "spare"), (5800, "spare"),
           (5900, "spare"), (4300, "cells"), (6000, "cells")])
# C: the two tied terms have as many hits each, so the alphabet decides: "cells" first, and the
# first "spare" hit (marked QZ-73) is reached by widening.
C = chunk([(3400, "spare QZ-73"), (5600, "spare"), (4300, "cells"), (5700, "cells")])
FIXTURES = {"A": A, "B": B, "C": C}


def today(terms_in_order, content, head=900, win=1300, max_windows=2):
    """The excerpt exactly as the function cut it before this change, for a GIVEN term order -
    the reference the new excerpt must contain."""
    if len(content) <= 4000:
        return content
    low = content.lower()
    out, covered = [content[:head]], []
    for t in terms_in_order:
        if len(covered) >= max_windows:
            break
        pos = low.find(t, head)
        if pos == -1:
            continue
        start = max(head, pos - 250)
        if any(abs(start - c) < win for c in covered):
            continue
        covered.append(start)
        out.append(content[start:start + win])
    if not covered:
        return content[:head + win]
    return " […] ".join(out[1:] + [out[0][:400]])


def fixed_order(query, content):
    """Longest first, then fewest hits in this chunk, then alphabetical - the order the function
    now takes."""
    low = content.lower()
    return sorted({t for t in re.findall(r"[\w]+", query.lower()) if len(t) >= 4},
                  key=lambda t: (-len(t), low.count(t), t))


def windows_of(excerpt):
    """The windows of an excerpt, without its trailing 400-character head."""
    return excerpt.split(" […] ")[:-1]


# ---------------------------------------------------------------------------
print("1. One excerpt under every hash seed (RED)")
# ---------------------------------------------------------------------------
RUNNER = (
    "import json, sys\n"
    f"sys.path.insert(0, {str(BACKEND)!r})\n"
    "from app.services.openai_client import _windowed_excerpt\n"
    "data = json.load(open(sys.argv[1], encoding='utf-8'))\n"
    "print(json.dumps({k: _windowed_excerpt(data['query'], v)\n"
    "                  for k, v in data['chunks'].items()}))\n"
)
by_seed = {}
with tempfile.TemporaryDirectory() as tmp:
    spec = Path(tmp) / "fixtures.json"
    spec.write_text(json.dumps({"query": QUERY, "chunks": FIXTURES}), encoding="utf-8")
    for seed in range(10):
        env = dict(os.environ, PYTHONHASHSEED=str(seed))
        done = subprocess.run([sys.executable, "-X", "utf8", "-c", RUNNER, str(spec)], env=env,
                              capture_output=True, text=True, encoding="utf-8", cwd=str(BACKEND))
        lines = [ln for ln in done.stdout.splitlines() if ln.startswith("{")]
        by_seed[seed] = json.loads(lines[-1]) if lines else {}
check("the real function ran in all ten processes",
      all(set(got) == set(FIXTURES) for got in by_seed.values()),
      {s: list(g) for s, g in by_seed.items()})
for name in FIXTURES:
    seen = {got.get(name) for got in by_seed.values()}
    check(f"RED: chunk {name} gets the same excerpt under PYTHONHASHSEED 0-9", len(seen) == 1,
          f"{len(seen)} different excerpts")
check("RED: chunk A keeps the rarest tied term's first hit under every seed",
      all("QZ-71" in got.get("A", "") for got in by_seed.values()),
      [s for s, got in by_seed.items() if "QZ-71" not in got.get("A", "")])
check("RED: chunk B keeps a hit the window rule skipped though no kept window held it, under "
      "every seed", all("QZ-72" in got.get("B", "") for got in by_seed.values()),
      [s for s, got in by_seed.items() if "QZ-72" not in got.get("B", "")])
check("RED: chunk C, whose tied terms have as many hits each, takes them alphabetically under "
      "every seed - the first term's window kept whole, the other's skipped hit reached",
      all(C[4050:5350] in got.get("C", "") and "QZ-73" in got.get("C", "")
          for got in by_seed.values()),
      [s for s, got in by_seed.items()
       if C[4050:5350] not in got.get("C", "") or "QZ-73" not in got.get("C", "")])
for name, content in FIXTURES.items():
    kept_today = windows_of(today(fixed_order(QUERY, content), content))
    check(f"RED: under every seed, every window today's rule keeps for chunk {name} (in the fixed "
          f"order) is inside the excerpt",
          bool(kept_today) and all(all(w in got.get(name, "") for w in kept_today)
                                   for got in by_seed.values()),
          [s for s, got in by_seed.items()
           if not all(w in got.get(name, "") for w in kept_today)])

# ---------------------------------------------------------------------------
print("\n2. What the widening does, read in the process with hash seed 0")
# ---------------------------------------------------------------------------
# Seed 0 is one of the processes that took the commoner tied term first before this change.
excerpt_b = by_seed.get(0, {}).get("B", "")
check("RED: chunk B's kept window was widened back to the skipped hit, not doubled: one window, "
      "then the head",
      len(excerpt_b.split(" […] ")) == 2 and "QZ-72" in (windows_of(excerpt_b) or [""])[0],
      excerpt_b[:200])
check("and that one window still holds the whole window today's rule kept",
      windows_of(today(fixed_order(QUERY, B), B))[0] in (windows_of(excerpt_b) or [""])[0])
check("RED: it reaches the skipped hit and no further back than the window rule's own margin",
      (windows_of(excerpt_b) or [""])[0] == B[3400 - 250:4300 - 250 + 1300],
      len((windows_of(excerpt_b) or [""])[0]))

# ---------------------------------------------------------------------------
check("RED: a hit already inside a kept window widens nothing: chunk A's excerpt is today's, in "
      "the fixed order, byte for byte",
      by_seed.get(0, {}).get("A") == today(fixed_order(QUERY, A), A),
      by_seed.get(0, {}).get("A", "")[:120])

# ---------------------------------------------------------------------------
print("\n3. Chunks the change never touches are cut exactly as before")
# ---------------------------------------------------------------------------
short = chunk([(1000, "spare"), (2000, "cells")], length=3900)
check("a chunk of up to 4,000 characters comes back whole",
      openai_client._windowed_excerpt(QUERY, short) == short)
none = chunk([(100, "spare")], length=6200)
check("a long chunk with no hit beyond its head is its head plus one window, as before",
      openai_client._windowed_excerpt(QUERY, none) == none[:900 + 1300])
apart_q = "do kegs or flues sit by vaults or ductwork?"   # terms of four different lengths
apart = chunk([(1500, "ductwork"), (3000, "vaults"), (4500, "flues"), (5900, "kegs")], length=6200)
check("a long chunk whose terms never tie and whose hits never skip is byte-identical to today's",
      openai_client._windowed_excerpt(apart_q, apart)
      == today(fixed_order(apart_q, apart), apart))
check("and that comparison is not empty: it keeps two windows",
      len(windows_of(today(fixed_order(apart_q, apart), apart))) == 2)

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
