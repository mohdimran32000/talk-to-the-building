"""llm_provider.py - a switchable per-ROLE provider: Gemini (default, byte-identical to
today) or Anthropic/Claude, chosen at call time by an environment switch (spec
2026-09-30, task W9-A1).

WHY THIS EXISTS
The goal-function job's remaining misses are the model's own judgment - the SQL writer
in `sql_tool.py` and the four answer prompts in `openai_client.stream_response` - and
the model drifts by day on a byte-identical prompt. The owner wants to try Claude on
both roles without disturbing Gemini, which stays the default and must stay
byte-identical when nothing is configured. This module is the ONE place that decides,
per role, which provider/model to use, and it MAKES the Claude half of the call -
`sql_tool.py` and `openai_client.py` keep their existing Gemini code completely
unchanged and add one `if get_llm_provider(role) == "anthropic": ... else: <unchanged
Gemini call>` branch in front of it. Neither file's Gemini-path config construction is
touched, so `tests/test_llm_repeatability.py`'s exact per-file `GenerateContentConfig(`
counts are unaffected by this module.

Task W9-A2 ports the tool choice too (the chat model's function-calling turn,
`openai_client.py`'s `chat_tool_loop` generation) - a third role, "tools"
(`generate_tool_choice`, below). It also closes a W9-A1 review finding: `openai_client.
_generate_checked_answer` (the ANSWER_CHECK=1 retry) now follows LLM_PROVIDER_ANSWER too
(`generate_answer_text`, below, its non-streaming counterpart to `stream_answer_text`), so a
checked retry never mixes providers.

SWITCHES, all read at call time - an environment variable, never cached, same contract
as `app.services.settings.get_llm_seed` / `get_sql_loop_max_steps`:
    LLM_PROVIDER_SQL, LLM_PROVIDER_ANSWER,       "gemini" (default) | "anthropic"
      LLM_PROVIDER_TOOLS
    ANTHROPIC_MODEL_SQL, ANTHROPIC_MODEL_ANSWER, a Claude model id
      ANTHROPIC_MODEL_TOOLS                        (default `DEFAULT_ANTHROPIC_MODEL`)
    ANTHROPIC_API_KEY                            the key (env / backend/.env) - read
                                                  fresh every call, never cached, never
                                                  logged, never put in an exception
                                                  message (there is nothing to
                                                  interpolate when it is missing, and
                                                  this module never builds a string
                                                  from it when it is present either)

PROMPT CACHING (task W9-A2, "tools" role only). `generate_tool_choice`'s system prompt and
tool list are identical across questions (~27,000 characters), so both carry a `cache_control`
breakpoint - the installed SDK's per-content-block mechanism (a list-of-blocks `system`, and a
`cache_control` key on the last tool dict; see `function_declarations_to_claude_tools`). This
changes no output, only cost/latency on a cache hit. Cache-read/cache-write token counts ride
on the SAME usage dict every role's recording already builds (`_usage_dict_anthropic` in
`llm_usage.py`) - harmless zeros for the sql/answer roles, which never cache.

LLM_SEED (`app.services.settings.get_llm_seed`) HAS NO CLAUDE EQUIVALENT - the Messages
API takes no seed parameter - so it is deliberately never read anywhere in this file. A
role switched onto "anthropic" loses seed-controlled repeatability; that is expected and
out of scope for this task.

ABOUT TEMPERATURE. The brief for this task asks for "temperature as configured today for
that role" to reach Claude too. It does not: the installed `anthropic` SDK's
`messages.create`/`messages.stream` have no `temperature` keyword at all (a fully
explicit keyword-only signature, verified against the installed version - not a
`**kwargs` passthrough), and Anthropic's own current documentation says the Claude
5-generation models reject a non-default `temperature`/`top_p`/`top_k` with a 400 even
on an SDK version that does accept the field. Sending it would make every real call fail
before a request is even issued - strictly worse than requirement 5's "surfaces as an
outage" contract. `_intended_temperature` below computes the value THIS task's brief
specifies, for the usage-recording trail only; it is never passed to the SDK call. See
the task report for the full reasoning.

USAGE
    from app.services.llm_provider import get_llm_provider, generate_sql_text
    if get_llm_provider("sql") == "anthropic":
        response = generate_sql_text(prompt, name="sql_generate")
        sql = response.text.strip()   # same shape the Gemini path already reads

    from app.services.llm_provider import generate_tool_choice
    if get_llm_provider("tools") == "anthropic":
        response = generate_tool_choice(system_text=system_text, contents=contents,
                                        function_declarations=function_declarations,
                                        name="chat_tool_loop")
        # response duck-types the SAME attributes a non-streaming google-genai response
        # carries (.candidates[0].content.parts[i].function_call.name/.args, .text,
        # .finish_reason) - every line below this one needs no change for either provider.
"""
import logging
import os

logger = logging.getLogger(__name__)

LLM_PROVIDER_SQL_ENV = "LLM_PROVIDER_SQL"
LLM_PROVIDER_ANSWER_ENV = "LLM_PROVIDER_ANSWER"
LLM_PROVIDER_TOOLS_ENV = "LLM_PROVIDER_TOOLS"
_PROVIDER_ENV_BY_ROLE = {"sql": LLM_PROVIDER_SQL_ENV, "answer": LLM_PROVIDER_ANSWER_ENV,
                          "tools": LLM_PROVIDER_TOOLS_ENV}

ANTHROPIC_MODEL_SQL_ENV = "ANTHROPIC_MODEL_SQL"
ANTHROPIC_MODEL_ANSWER_ENV = "ANTHROPIC_MODEL_ANSWER"
ANTHROPIC_MODEL_TOOLS_ENV = "ANTHROPIC_MODEL_TOOLS"
_MODEL_ENV_BY_ROLE = {"sql": ANTHROPIC_MODEL_SQL_ENV, "answer": ANTHROPIC_MODEL_ANSWER_ENV,
                       "tools": ANTHROPIC_MODEL_TOOLS_ENV}
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"

ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"

# Gemini's explicit output budget for the SQL writer and its repair call (sql_tool.py,
# both configs carry max_output_tokens=2048) - mirrored here so a role switched onto
# Claude gets at least as much room, never less.
SQL_MAX_OUTPUT_TOKENS = 2048

# The four answer prompts never set an explicit Gemini output budget (none of
# openai_client.py's answer-role configs pass max_output_tokens, so the model's own
# implicit default applies). Claude's max_tokens is a REQUIRED parameter, so this picks
# a generous streaming budget - comfortably above any plausible implicit Gemini default
# - rather than guessing at a number to match.
ANSWER_MAX_TOKENS = 64000

# The tool-choice turn (chat_tool_loop) ALSO sets no explicit Gemini output budget - and,
# unlike the sql/answer roles, its "no function call" branch can itself BE the final answer
# (stream_response reads the model's own text straight off this response when no tool was
# chosen - see the "no tool_use" branch of generate_tool_choice, below). Same reasoning as
# ANSWER_MAX_TOKENS, same value, kept as its own named constant for what it is used for.
TOOL_CHOICE_MAX_OUTPUT_TOKENS = ANSWER_MAX_TOKENS


def get_llm_provider(role: str) -> str:
    """"gemini" (default - today's behaviour, byte-identical) or "anthropic", from
    LLM_PROVIDER_SQL / LLM_PROVIDER_ANSWER. Anything other than the exact literal
    "anthropic" (surrounding whitespace tolerated, case-insensitive) stays "gemini" -
    the same defensive contract as the other environment switches in
    `app.services.settings`: a typo must silently keep today's provider, never crash
    and never silently route onto a provider nobody chose. An unknown role name also
    stays "gemini"."""
    env_name = _PROVIDER_ENV_BY_ROLE.get(role)
    if env_name is None:
        return "gemini"
    raw = os.environ.get(env_name)
    if raw is None:
        return "gemini"
    return "anthropic" if raw.strip().lower() == "anthropic" else "gemini"


def get_anthropic_model(role: str) -> str:
    """The Claude model id for ROLE, from ANTHROPIC_MODEL_SQL / ANTHROPIC_MODEL_ANSWER,
    default `DEFAULT_ANTHROPIC_MODEL`. Blank/whitespace-only falls back to the default
    rather than sending an empty model id to the API."""
    env_name = _MODEL_ENV_BY_ROLE.get(role)
    raw = os.environ.get(env_name) if env_name else None
    return raw.strip() if raw and raw.strip() else DEFAULT_ANTHROPIC_MODEL


def get_anthropic_api_key():
    """The ANTHROPIC_API_KEY environment setting, or None when unset/blank. Read at
    call time; never cached, never logged, never returned wrapped in any other text."""
    raw = os.environ.get(ANTHROPIC_API_KEY_ENV)
    return raw if raw else None


def _intended_temperature(role: str):
    """The temperature this task's brief specifies for ROLE (0 for "sql" and "tools" - Gemini
    pins temperature=0 on chat_tool_loop too, for deterministic tool routing; the configured
    `ANSWER_TEMPERATURE`, default 0.2, for "answer") - computed for the usage-recording trail
    ONLY. See the module docstring's "ABOUT TEMPERATURE" section for why this is never sent to
    the Claude API."""
    if role in ("sql", "tools"):
        return 0
    from app.services.settings import get_answer_temperature
    return get_answer_temperature()


def _get_anthropic_client():
    """A fresh `anthropic.Anthropic` client. Lazily imports `anthropic` so nothing that
    never switches a role onto "anthropic" needs the package importable.

    Mirrors `openai_client._get_client`'s own contract for a missing key: a plain,
    static ValueError - there is nothing to interpolate either way, but writing it as a
    literal (never an f-string) means it can never change that by accident later."""
    import anthropic

    api_key = get_anthropic_api_key()
    if not api_key:
        raise ValueError(
            "No Anthropic API key configured. Set ANTHROPIC_API_KEY in backend/.env."
        )
    # Same 3-minute per-request timeout as the Gemini clients
    # (openai_client._get_client, sql_tool.execute_sql_query) - a wedged HTTP
    # connection must surface as an exception, not hang the request forever. The
    # Anthropic Python client's `timeout` is seconds, not milliseconds like Gemini's
    # `HttpOptions(timeout=180_000)`.
    return anthropic.Anthropic(api_key=api_key, timeout=180.0)


def _response_text(response) -> str:
    """Every "text"-type content block of a non-streaming Claude response, joined - ""
    when there is none (a tool-use-only or empty response), mirroring how the Gemini
    path already tolerates a falsy `.text` (`(repair_resp.text or "").strip()` in
    `sql_tool.py`)."""
    blocks = getattr(response, "content", None) or []
    return "".join(
        getattr(b, "text", "") or "" for b in blocks if getattr(b, "type", None) == "text"
    )


class _TextResponse:
    """Duck-types the one attribute `execute_sql_query` reads off a non-streaming
    google-genai response (`response.text`), so the SQL-writer call site's existing
    `sql = response.text.strip()` line needs no change for either provider."""
    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text


def gemini_contents_to_claude_messages(contents) -> list:
    """Gemini `contents` (a list of `google.genai.types.Content(role="user"|"model",
    parts=[types.Part(text=...), ...])`, exactly as every answer-role call site in
    `openai_client.py` builds it from the chat's `messages`) -> Claude `messages`
    (`[{"role": "user"|"assistant", "content": "..."}]`), merging consecutive
    same-role turns into one message. Claude requires the merge (a non-merged
    consecutive pair is otherwise rejected); Gemini does not, and `stream_response`
    builds one `Content` per chat turn with no merging of its own.

    Gemini's "model" role -> Claude's "assistant"; anything else (just "user" in
    practice) passes through as "user" unchanged. A turn with more than one `Part`
    joins its own parts' text first, with no separator (matching how the app only
    ever builds single-text-part turns today)."""
    merged: list = []
    for item in contents:
        role = "assistant" if getattr(item, "role", None) == "model" else "user"
        parts = getattr(item, "parts", None) or []
        text = "".join(getattr(p, "text", "") or "" for p in parts)
        if merged and merged[-1]["role"] == role:
            merged[-1]["content"] = f'{merged[-1]["content"]}\n\n{text}'
        else:
            merged.append({"role": role, "content": text})
    return merged


def generate_sql_text(prompt: str, *, max_output_tokens: int = SQL_MAX_OUTPUT_TOKENS,
                       client=None, name: str = "sql_generate") -> _TextResponse:
    """The Claude half of `sql_tool.py`'s SQL-writer call - both the primary
    generation and the repair call send a bare prompt string with no system turn,
    exactly as the Gemini path's `contents=prompt` does. Temperature is never sent
    (see the module docstring); model from `ANTHROPIC_MODEL_SQL`.

    `client` is None in production (a real client is built here) and a fake in
    tests - the call site passes its own fake the same way `sql_tool.py`'s existing
    tests monkeypatch the Gemini client class, just as a parameter instead, because
    `anthropic.Anthropic` is instantiated inside THIS module rather than at the
    `sql_tool.py` call site."""
    model = get_anthropic_model("sql")
    anthropic_client = client if client is not None else _get_anthropic_client()
    response = anthropic_client.messages.create(
        model=model,
        max_tokens=max_output_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    _record_usage(name=name, role="sql", model=model, response=response)
    return _TextResponse(_response_text(response))


def stream_answer_text(*, system_text: str, contents, max_tokens: int = ANSWER_MAX_TOKENS,
                        client=None, name: str = "answer_stream"):
    """The Claude half of one of `openai_client.stream_response`'s four answer
    prompts. Yields plain text chunks - a caller already doing
    `for chunk in gemini_stream: if chunk.text: yield ("token", chunk.text)` becomes
    `for text in stream_answer_text(...): yield ("token", text)`, nothing else about
    the surrounding generator changes. Temperature is never sent (see the module
    docstring); model from `ANTHROPIC_MODEL_ANSWER`.

    `client` is the same test-injection point `generate_sql_text` takes."""
    model = get_anthropic_model("answer")
    anthropic_client = client if client is not None else _get_anthropic_client()
    messages = gemini_contents_to_claude_messages(contents)
    with anthropic_client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=system_text,
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            if text:
                yield text
        final_message = stream.get_final_message()
    _record_usage(name=name, role="answer", model=model, response=final_message)


def _schema_type_name(schema) -> str:
    """The google-genai `Schema.type` ('OBJECT', 'STRING', 'ARRAY', 'INTEGER', 'NUMBER',
    'BOOLEAN' - an OpenAPI-flavoured, uppercase enum) as the lowercase name JSON Schema uses
    ('object', 'string', ...). `schema.type` is a `Type` enum member whose `str()` is
    'Type.OBJECT'; `.value` is the plain 'OBJECT' this lowercases."""
    t = getattr(schema, "type", None)
    value = getattr(t, "value", t)
    return str(value).lower()


def _schema_to_json_schema(schema) -> dict:
    """ONE google-genai `types.Schema` (the shape every `_build_*_tool()` in
    `openai_client.py` builds its `parameters=` from) -> a plain JSON Schema dict,
    recursively - what Claude's `tools[].input_schema` takes (the Messages API: "JSON schema
    for this tool's input"). Task W9-A2, brief requirement 2.

    Handles exactly the shapes this app's tool declarations use (OBJECT/STRING/ARRAY/INTEGER/
    NUMBER/BOOLEAN, `enum`, `description`, `required`, nested `properties`/`items` at any
    depth) - a small, direct recursion rather than google-genai's own `Schema.from_json_schema`
    (the INVERSE direction, and a much larger OpenAPI-field mapping - `any_of`, `title`,
    `pattern`, ... - no declared tool here uses), so this stays easy to read and to test
    against every real tool exactly.

    `required=None` (never set) and `required=[]` (set empty) both mean "nothing required" and
    both produce no "required" key at all - equivalent, not a divergence worth preserving."""
    out: dict = {"type": _schema_type_name(schema)}
    description = getattr(schema, "description", None)
    if description is not None:
        out["description"] = description
    enum = getattr(schema, "enum", None)
    if enum:
        out["enum"] = list(enum)
    properties = getattr(schema, "properties", None)
    if properties:
        out["properties"] = {name: _schema_to_json_schema(prop)
                              for name, prop in properties.items()}
    required = getattr(schema, "required", None)
    if required:
        out["required"] = list(required)
    items = getattr(schema, "items", None)
    if items is not None:
        out["items"] = _schema_to_json_schema(items)
    return out


def function_declarations_to_claude_tools(function_declarations) -> list:
    """The SAME tool list `openai_client.stream_response` builds from `_build_*_tool()`
    (a list of google-genai `types.FunctionDeclaration`) -> Claude's `tools` shape: one
    `{"name", "description", "input_schema"}` dict per tool, JSON-Schema-converted (see
    `_schema_to_json_schema`) - task W9-A2, brief requirement 2. `None`/empty in -> `[]` out
    (mirrors `stream_response`'s own `function_declarations = []` default when no tool is
    enabled for the turn).

    PROMPT CACHING (requirement 4): the LAST tool carries a `cache_control` breakpoint - the
    tool list is identical across questions, and Anthropic's prompt-caching convention caches
    everything up to and including a marked block as one prefix, so a single trailing marker
    is enough to cache the whole list."""
    tools = []
    for fd in function_declarations or []:
        params = getattr(fd, "parameters", None)
        tools.append({
            "name": fd.name,
            "description": fd.description or "",
            "input_schema": (_schema_to_json_schema(params) if params is not None
                              else {"type": "object", "properties": {}}),
        })
    if tools:
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
    return tools


class _ToolChoicePart:
    """Duck-types ONE part of a non-streaming google-genai response's
    `candidates[0].content.parts[i]` - `.function_call` (truthy only on a tool_use block;
    itself duck-typing `.name`/`.args`) and `.text` (truthy only on a text block) - so
    `openai_client.stream_response`'s existing tool-dispatch code (which only ever reads
    these two attributes off each part) runs completely unchanged regardless of which
    provider produced the response (task W9-A2, brief requirement 3)."""
    __slots__ = ("function_call", "text")

    def __init__(self, *, function_call=None, text=None):
        self.function_call = function_call
        self.text = text


class _ToolChoiceFunctionCall:
    """Duck-types google-genai's `part.function_call` - `.name` (str) and `.args` (a plain
    dict; `stream_response` does `dict(fc.args) if fc.args else {}`, and `dict()` of an
    already-plain dict is just a shallow copy)."""
    __slots__ = ("name", "args")

    def __init__(self, name, args):
        self.name = name
        self.args = args


class _ToolChoiceContent:
    __slots__ = ("parts",)

    def __init__(self, parts):
        self.parts = parts


class _ToolChoiceCandidate:
    """`.finish_reason` is always None here - Gemini's MALFORMED_FUNCTION_CALL has no Claude
    equivalent (brief requirement 3), and `stream_response`'s own check
    (`response.candidates[0].finish_reason and ...name == "MALFORMED_FUNCTION_CALL"`)
    short-circuits on a falsy `finish_reason` with no `.name` access at all, so None alone is
    enough to make that branch never fire for a Claude response."""
    __slots__ = ("content", "finish_reason")

    def __init__(self, content):
        self.content = content
        self.finish_reason = None


class _ToolChoiceResponse:
    """Duck-types the one attribute `stream_response` reads off the non-streaming
    google-genai response from `chat_tool_loop`: `.candidates` (a list; this app only ever
    reads index 0)."""
    __slots__ = ("candidates",)

    def __init__(self, candidates):
        self.candidates = candidates


def _claude_message_to_gemini_shape(message) -> _ToolChoiceResponse:
    """A non-streaming Claude `Message` (`.content`: a list of blocks, each `.type` a string)
    -> the Gemini-shaped adapter above, in the SAME block order Claude returned them -
    `stream_response` only ever takes the FIRST part whose `.function_call` is truthy (brief
    requirement 3: "the FIRST tool_use block becomes exactly the (name, args)"), so order is
    the only thing that matters here."""
    parts = []
    for block in getattr(message, "content", None) or []:
        block_type = getattr(block, "type", None)
        if block_type == "tool_use":
            raw_input = getattr(block, "input", None) or {}
            parts.append(_ToolChoicePart(
                function_call=_ToolChoiceFunctionCall(block.name, dict(raw_input))))
        elif block_type == "text":
            parts.append(_ToolChoicePart(text=getattr(block, "text", "") or ""))
        # Any other block type (e.g. a "thinking" block - never requested on this call, no
        # `thinking=` config is ever sent) carries neither a function_call nor text and is
        # simply not represented - it changes nothing stream_response reads.
    return _ToolChoiceResponse([_ToolChoiceCandidate(_ToolChoiceContent(parts))])


def generate_tool_choice(*, system_text: str, contents, function_declarations,
                          max_output_tokens: int = TOOL_CHOICE_MAX_OUTPUT_TOKENS,
                          client=None, name: str = "chat_tool_loop") -> _ToolChoiceResponse:
    """The Claude half of `openai_client.stream_response`'s tool-choice turn (`chat_tool_loop`)
    - task W9-A2. Builds the SAME system text, the SAME merged conversation turns
    (`gemini_contents_to_claude_messages`), and the SAME function declarations - converted
    faithfully to Claude's tool shape (`function_declarations_to_claude_tools`) - as the Gemini
    call this replaces, with `tool_choice={"type": "auto"}` (Gemini's own `mode="AUTO"`).

    Returns an object that duck-types the ATTRIBUTES of a non-streaming google-genai response
    that `stream_response`'s existing tool-dispatch code reads
    (`.candidates[0].content.parts[i].function_call.name`/`.args`, `.text`, `.finish_reason`) -
    so that entire block of call-site code (everything AFTER the call) needs no change at all
    to handle either provider, per brief requirement 3 ("everything after it runs unchanged").

    Temperature is never sent (see the module docstring's "ABOUT TEMPERATURE") - Gemini pins
    temperature=0 for this call for deterministic tool routing; the installed Anthropic SDK has
    no temperature parameter at all and the model rejects a non-default value with an HTTP 400,
    so there is no compliant way to send it (same finding as `generate_sql_text`/
    `stream_answer_text`).

    `function_declarations` may be empty (`stream_response` still makes this call with no tools
    at all when no tool is enabled for the turn) - then no `tools`/`tool_choice` is sent,
    matching Gemini's own `tools=None, tool_config=None` in that case.

    `client` is the same test-injection point `generate_sql_text`/`stream_answer_text` take."""
    model = get_anthropic_model("tools")
    anthropic_client = client if client is not None else _get_anthropic_client()
    messages = gemini_contents_to_claude_messages(contents)
    claude_tools = function_declarations_to_claude_tools(function_declarations)

    kwargs = dict(
        model=model,
        max_tokens=max_output_tokens,
        system=[{"type": "text", "text": system_text,
                 "cache_control": {"type": "ephemeral"}}],
        messages=messages,
    )
    if claude_tools:
        kwargs["tools"] = claude_tools
        kwargs["tool_choice"] = {"type": "auto"}
    response = anthropic_client.messages.create(**kwargs)
    _record_usage(name=name, role="tools", model=model, response=response)
    return _claude_message_to_gemini_shape(response)


def generate_answer_text(*, system_text: str, contents, max_tokens: int = ANSWER_MAX_TOKENS,
                          client=None, name: str = "answer_stream") -> str:
    """The Claude half of `openai_client._generate_checked_answer`'s buffered (non-streaming)
    generation - task W9-A2, brief requirement 5. `stream_answer_text`'s non-streaming twin:
    same system/messages shape, same model (role "answer"), same never-send-temperature
    reasoning, but returns the WHOLE answer as a plain `str` (via `_response_text`) instead of
    yielding chunks - `_generate_checked_answer` needs the complete text before `find_problems`
    can run, so there is no streaming variant of this one.

    A checked retry must never mix providers (the W9-A1 review's finding): the ONE call site
    uses this function for BOTH the primary generation and the retry whenever
    `get_llm_provider("answer") == "anthropic"`, never falling back to Gemini mid-retry.

    `client` is the same test-injection point every other function in this module takes."""
    model = get_anthropic_model("answer")
    anthropic_client = client if client is not None else _get_anthropic_client()
    messages = gemini_contents_to_claude_messages(contents)
    response = anthropic_client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_text,
        messages=messages,
    )
    _record_usage(name=name, role="answer", model=model, response=response)
    return _response_text(response)


def _record_usage(*, name: str, role: str, model: str, response) -> None:
    """Best-effort - never raises, never breaks the call it is recording. See
    `app.services.llm_usage.record_anthropic_usage` for the actual LangSmith
    attachment and its own (doubly redundant, deliberately so) safety contract."""
    try:
        from app.services.llm_usage import record_anthropic_usage

        record_anthropic_usage(name, model, response,
                                intended_temperature=_intended_temperature(role))
    except Exception as e:  # noqa: BLE001 - observability must never break a request
        logger.debug(f"Claude usage recording unavailable ({type(e).__name__}: {e}); "
                     "continuing without it")
