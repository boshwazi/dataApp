"""Client for any OpenAI-compatible chat API (ilmu.ai by default).

The connection test checks three things the later stages depend on:
- a plain chat reply (the key, base URL and model are right),
- tool calling (stage agents use it to return change sets),
- JSON mode (the fallback protocol when tool calling is missing).
"""

import json
import time
from dataclasses import asdict, dataclass

import openai

from .settings import ProviderSettings

TEST_TOOL = {
    "type": "function",
    "function": {
        "name": "add_numbers",
        "description": "Add two numbers together.",
        "parameters": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    },
}


def make_client(settings: ProviderSettings) -> openai.OpenAI:
    return openai.OpenAI(base_url=settings.base_url, api_key=settings.api_key, timeout=60, max_retries=1)


@dataclass
class ConnectionReport:
    ok: bool
    latency_ms: int | None = None
    reply: str | None = None
    supports_tools: bool | None = None
    supports_json_mode: bool | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def describe_error(exc: Exception) -> str:
    if isinstance(exc, openai.AuthenticationError):
        return "The API key was rejected. Check it in Settings."
    if isinstance(exc, openai.PermissionDeniedError):
        return "The API key is not allowed to use this model."
    if isinstance(exc, openai.NotFoundError):
        return "Model or endpoint not found. Check the base URL and model name."
    if isinstance(exc, openai.RateLimitError):
        return "Rate limited or out of credit at the provider. Try again shortly."
    if isinstance(exc, openai.APITimeoutError):
        return "The provider took too long to respond."
    if isinstance(exc, openai.APIConnectionError):
        return "Could not reach the provider. Check the base URL and your network."
    if isinstance(exc, openai.APIStatusError):
        return f"The provider returned HTTP {exc.status_code}: {exc.message}"
    return f"{type(exc).__name__}: {exc}"


def test_connection(client: openai.OpenAI, model: str) -> ConnectionReport:
    started = time.monotonic()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with the single word OK."}],
            max_tokens=200,
        )
    except Exception as exc:  # any failure here means the basic setup is wrong
        return ConnectionReport(ok=False, error=describe_error(exc))
    latency_ms = int((time.monotonic() - started) * 1000)
    reply = (response.choices[0].message.content or "").strip()
    return ConnectionReport(
        ok=True,
        latency_ms=latency_ms,
        reply=reply[:200],
        supports_tools=_check_tools(client, model),
        supports_json_mode=_check_json_mode(client, model),
    )


def _check_tools(client: openai.OpenAI, model: str) -> bool:
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Use the add_numbers tool to add 2 and 3."}],
            tools=[TEST_TOOL],
            tool_choice="auto",
            max_tokens=500,
        )
    except openai.APIError:
        return False
    calls = response.choices[0].message.tool_calls or []
    if not calls or calls[0].function.name != "add_numbers":
        return False
    try:
        args = json.loads(calls[0].function.arguments)
    except (TypeError, json.JSONDecodeError):
        return False
    return {"a", "b"} <= set(args)


def _check_json_mode(client: openai.OpenAI, model: str) -> bool:
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": 'Return a JSON object with the key "status" set to "ok".'}],
            response_format={"type": "json_object"},
            max_tokens=500,
        )
    except openai.APIError:
        return False
    try:
        return isinstance(json.loads(response.choices[0].message.content or ""), dict)
    except json.JSONDecodeError:
        return False


class AIError(Exception):
    pass


def ask_json(
    client: openai.OpenAI,
    model: str,
    capabilities: dict,
    system: str,
    prompt: str,
    tool_name: str,
    schema: dict,
    max_tokens: int = 8000,
) -> dict:
    """Ask for one JSON object matching `schema`, using the best protocol the provider supports.

    Tool calling first, then JSON mode, then plain text with the JSON pulled out of the reply.
    """
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    try:
        if capabilities.get("tools"):
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=[{"type": "function", "function": {"name": tool_name, "parameters": schema}}],
                tool_choice={"type": "function", "function": {"name": tool_name}},
                max_tokens=max_tokens,
            )
            calls = response.choices[0].message.tool_calls or []
            if calls and calls[0].function.name == tool_name:
                return _parse_object(calls[0].function.arguments)
        schema_note = f"\n\nReply with only a JSON object matching this JSON Schema:\n{json.dumps(schema)}"
        messages[-1] = {"role": "user", "content": prompt + schema_note}
        extra = {"response_format": {"type": "json_object"}} if capabilities.get("json_mode") else {}
        response = client.chat.completions.create(model=model, messages=messages, max_tokens=max_tokens, **extra)
    except openai.APIError as exc:
        raise AIError(describe_error(exc)) from exc
    return _parse_object(response.choices[0].message.content or "")


def _parse_object(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise AIError("The AI reply did not contain JSON.")
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AIError("The AI reply was not valid JSON.") from exc
    if not isinstance(value, dict):
        raise AIError("The AI reply was not a JSON object.")
    return value
