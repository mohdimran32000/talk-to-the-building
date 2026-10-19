"""llm_config.py - one helper every `GenerateContentConfig` the app builds passes through, so
`LLM_SEED` reaches all of them with no exceptions (spec 2026-09-30, task W8-A1 part A).

WHY THIS EXISTS
The goal-function job's remaining misses are the model's own judgment, and the model drifts
by day on a byte-identical prompt. `LLM_SEED` is the first step toward measuring that drift
out: set it, and EVERY call the app makes - tool choice, SQL writer and repair, the four
answer prompts, the reranker's LLM path, the sub-agent/explorer calls, metadata extraction -
carries the same seed. Every call site keeps building its own `GenerateContentConfig(...)`
exactly as before and wraps the result with `apply_llm_seed(...)` - one line changed per site,
nothing about what each site asks for.

Unset (today), this is a no-op: the config returned is the one it was given, `seed` left at
its default of `None` - byte-identical to a config that never passed through here.

USAGE
    from app.services.llm_config import apply_llm_seed
    config = apply_llm_seed(types.GenerateContentConfig(temperature=0.2))
"""
from app.services.settings import get_llm_seed


def apply_llm_seed(config):
    """Set `config.seed` from the `LLM_SEED` environment setting, in place, and return the
    SAME object - a decorator, not a rebuild, so a call site wraps its existing
    `GenerateContentConfig(...)` construction and changes nothing else about it.

    `GenerateContentConfig` is a mutable pydantic model (not frozen), so setting `.seed`
    after construction reaches the request exactly as setting it in the constructor would.
    Unset or unreadable `LLM_SEED` leaves `config.seed` at its default (`None`): the config
    this returns is then indistinguishable from one that never passed through here.
    """
    seed = get_llm_seed()
    if seed is not None:
        config.seed = seed
    return config
