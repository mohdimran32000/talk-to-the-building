"""Record Gemini token usage on LangSmith runs — without ever breaking a request.

WHY THIS EXISTS
LangSmith only records token counts on runs typed `llm`. Every Gemini call in this
app sits *inside* a function traced as `chain` or `tool` (rerank_chunks,
query_structured_data, extract_metadata), so LangSmith shows how long each step took
but not what it cost. This module adds the missing `llm` run.

THE SAFETY CONTRACT
Observability must never be able to break the thing it observes. So:

  * The Gemini call itself is made in a plain, untraced code path.
  * Tracing is attempted around it; if ANYTHING in the tracing layer raises — client
    not configured, LangSmith unreachable, DNS blip, API change — the exception is
    swallowed and the caller still gets its response.
  * A tracing failure is logged at debug level once per process, not per call, so a
    LangSmith outage cannot flood the logs.

Net effect: worst case you lose visibility, never a request.

USAGE
    from app.services.llm_usage import generate_with_usage
    response = generate_with_usage(client, model=..., contents=..., config=...,
                                   name="rerank")
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

_warned = False


def _usage_dict(response: Any) -> dict:
    """Pull token counts off a google-genai response into LangSmith's shape."""
    u = getattr(response, "usage_metadata", None)
    if u is None:
        return {}
    inp = getattr(u, "prompt_token_count", None) or 0
    out = getattr(u, "candidates_token_count", None) or 0
    thoughts = getattr(u, "thoughts_token_count", None) or 0
    total = getattr(u, "total_token_count", None) or (inp + out + thoughts)
    return {
        "input_tokens": int(inp),
        "output_tokens": int(out) + int(thoughts),
        "total_tokens": int(total),
    }


def _record(name: str, model: str, contents: Any, response: Any) -> None:
    """Attach an `llm` child run carrying the token usage. Never raises."""
    global _warned
    try:
        from langsmith.run_helpers import get_current_run_tree

        parent = get_current_run_tree()
        if parent is None:
            return  # not inside a traced call — nothing to attach to
        usage = _usage_dict(response)
        if not usage:
            return
        child = parent.create_child(
            name=name,
            run_type="llm",
            inputs={"model": model, "prompt_chars": len(str(contents))},
        )
        child.end(outputs={"usage_metadata": usage})
        child.post()
    except Exception as e:  # noqa: BLE001 - observability must never break a request
        if not _warned:
            _warned = True
            logger.debug(f"LangSmith usage recording unavailable ({type(e).__name__}: {e}); "
                         "continuing without token traces")


def generate_with_usage(client, *, model: str, contents: Any, config: Any = None,
                        name: str = "gemini_call"):
    """Call Gemini, then best-effort record its token usage on the current trace.

    The call is made FIRST and returned unconditionally. Recording happens after and
    cannot affect the return value or raise.
    """
    response = (client.models.generate_content(model=model, contents=contents, config=config)
                if config is not None
                else client.models.generate_content(model=model, contents=contents))
    _record(name, model, contents, response)
    return response


# --------------------------------------------------------------------------- Claude (task W9-A1)
#
# A separate function, not a branch inside `_record`/`generate_with_usage` above - so
# neither provider's usage recording can regress the other. Unlike `generate_with_usage`,
# which both MAKES Gemini's call and records it, `record_anthropic_usage` only records -
# the Claude call itself is made in `app.services.llm_provider`, which also owns the
# Gemini/Claude role switch and the Gemini<->Claude prompt conversion, neither of which
# belongs in a usage-recording module.


def _usage_dict_anthropic(response: Any) -> dict:
    """Pull token counts off an Anthropic Messages API response into the same shape
    `_usage_dict` builds for Gemini, read off `response.usage.input_tokens` /
    `.output_tokens` instead of `usage_metadata.prompt_token_count` /
    `.candidates_token_count`. Claude has no separate "thoughts" token count to fold
    in the way Gemini's `thoughts_token_count` is.

    task W9-A2: also carries `cache_creation_input_tokens` / `cache_read_input_tokens`
    (the prompt-caching write/read counts `generate_tool_choice`'s cache_control marks
    can produce) - 0 when the response carries neither, which is every sql/answer-role
    response today (those roles never mark anything cacheable), so this is additive and
    harmless for them."""
    u = getattr(response, "usage", None)
    if u is None:
        return {}
    inp = getattr(u, "input_tokens", None) or 0
    out = getattr(u, "output_tokens", None) or 0
    cache_write = getattr(u, "cache_creation_input_tokens", None) or 0
    cache_read = getattr(u, "cache_read_input_tokens", None) or 0
    return {
        "input_tokens": int(inp),
        "output_tokens": int(out),
        "total_tokens": int(inp) + int(out),
        "cache_creation_input_tokens": int(cache_write),
        "cache_read_input_tokens": int(cache_read),
    }


def record_anthropic_usage(name: str, model: str, response: Any, *,
                           intended_temperature=None) -> None:
    """Attach an `llm` child run carrying one Claude call's token usage - the same
    LangSmith mechanism `_record` uses for Gemini (same `_warned`-once-per-process
    throttle), kept as a fully separate function so neither provider's path can
    regress the other. `intended_temperature` is recorded as an input for the audit
    trail only (see `llm_provider`'s "ABOUT TEMPERATURE" docstring section for why it
    is never sent to the API itself). Never raises - see the module safety contract
    at the top of this file; it applies here identically."""
    global _warned
    try:
        from langsmith.run_helpers import get_current_run_tree

        parent = get_current_run_tree()
        if parent is None:
            return  # not inside a traced call — nothing to attach to
        usage = _usage_dict_anthropic(response)
        if not usage:
            return
        child = parent.create_child(
            name=name,
            run_type="llm",
            inputs={"model": model, "provider": "anthropic",
                    "intended_temperature": intended_temperature},
        )
        child.end(outputs={"usage_metadata": usage})
        child.post()
    except Exception as e:  # noqa: BLE001 - observability must never break a request
        if not _warned:
            _warned = True
            logger.debug(f"LangSmith usage recording unavailable ({type(e).__name__}: {e}); "
                         "continuing without token traces")
