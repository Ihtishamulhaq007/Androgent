"""
agent/model.py

ModelProvider is the swappable interface controller.py talks to. Two
implementations:
    FakeModelProvider   scripted, no network — lets controller.py be
                        tested end-to-end without a live API call.
    GeminiProvider       the real thing, hitting generateContent with
                        function calling.

Request/response shape confirmed against Google's current docs
(ai.google.dev, Aug 2026) rather than assumed from memory:
  - tool schemas convert from this project's lowercase JSON-schema style
    (as returned by Tool.schema) to Gemini's uppercase Type enum
    (OBJECT/STRING/NUMBER/INTEGER/BOOLEAN/ARRAY) — Gemini's REST API
    does not accept lowercase.
  - a function result is sent back as {"role": "user", "parts":
    [{"functionResponse": {...}}]}. Current official docs use "user"
    here — older examples floating around using "function" are the
    outdated version.

No connectivity is surfaced as ModelResponse(error="No local model") —
the exact phrase requested — never a bare exception up to controller.py.
A reached-but-erroring server (bad key, bad model name, rate limit) is a
different failure mode and gets its own real message instead.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .config import Config


@dataclass
class ToolCallRequest:
    name: str
    args: dict
    call_id: str | None = None  # Gemini's optional function-call id, echoed back in the response
    thought_signature: str | None = None  # Gemini 3.x: MUST be replayed verbatim on the next turn, or the API 400s


@dataclass
class ModelResponse:
    text: str | None = None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    error: str | None = None  # e.g. "No local model" on connectivity failure
    retry_after_seconds: float | None = None  # set only for 429 rate-limit responses


class ModelProvider(ABC):
    @abstractmethod
    def generate(self, history: list[dict], tools_schema: list[dict]) -> ModelResponse:
        """history is Memory.to_list() — this project's own verbatim shape,
        NOT Gemini's. Each provider translates to/from its own API shape.
        tools_schema is ToolRegistry.schemas() — this project's lowercase
        JSON-schema style, also translated per-provider."""


class FakeModelProvider(ModelProvider):
    """Scripted, no network. Feed it a list of ModelResponse up front;
    each call to generate() returns the next one in order, regardless of
    what history/tools_schema it's actually given. Used to test
    controller.py end-to-end without hitting the real API."""

    def __init__(self, script: list[ModelResponse]):
        self._script = list(script)
        self._index = 0

    def generate(self, history: list[dict], tools_schema: list[dict]) -> ModelResponse:
        if self._index >= len(self._script):
            raise IndexError(
                f"FakeModelProvider script exhausted after {self._index} calls — "
                "controller asked for more turns than were scripted."
            )
        response = self._script[self._index]
        self._index += 1
        return response


_JSON_TYPE_TO_GEMINI = {
    "object": "OBJECT",
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
}


def _to_gemini_schema(schema: dict) -> dict:
    """Recursively convert this project's lowercase JSON-schema style into
    Gemini's uppercase Type enum. Only touches 'type' fields; everything
    else (properties, required, description, items) passes through,
    recursing into nested schemas."""
    converted = {}
    for key, value in schema.items():
        if key == "type" and isinstance(value, str):
            converted[key] = _JSON_TYPE_TO_GEMINI.get(value.lower(), value.upper())
        elif key == "properties" and isinstance(value, dict):
            converted[key] = {name: _to_gemini_schema(sub) for name, sub in value.items()}
        elif key == "items" and isinstance(value, dict):
            converted[key] = _to_gemini_schema(value)
        else:
            converted[key] = value
    return converted


def _tools_to_gemini(tools_schema: list[dict]) -> list[dict]:
    declarations = [
        {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": _to_gemini_schema(tool["parameters"]),
        }
        for tool in tools_schema
    ]
    return [{"functionDeclarations": declarations}]


def _history_to_gemini(history: list[dict]) -> list[dict]:
    """Translate this project's Turn.to_dict() shape (kind/content/data)
    into Gemini's contents list. 'goal' and 'summary' have no Gemini
    equivalent, so both become plain user-role text."""
    contents = []
    for turn in history:
        kind = turn["kind"]
        data = turn.get("data", {})
        if kind in ("goal", "summary", "user_note"):
            contents.append({"role": "user", "parts": [{"text": turn["content"]}]})
        elif kind == "model_response":
            contents.append({"role": "model", "parts": [{"text": turn["content"]}]})
        elif kind == "tool_call":
            fc_part = {"functionCall": {"name": data.get("tool", ""), "args": data.get("args", {})}}
            if data.get("thought_signature"):
                fc_part["thoughtSignature"] = data["thought_signature"]
            contents.append({"role": "model", "parts": [fc_part]})
        elif kind == "tool_result":
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": data.get("tool", ""),
                                "response": {"result": turn["content"], "success": data.get("success", True)},
                            }
                        }
                    ],
                }
            )
        else:
            contents.append({"role": "user", "parts": [{"text": turn["content"]}]})
    return contents


def _extract_retry_seconds(message: str, default: float = 30.0) -> float:
    """Google's 429 messages include 'Please retry in 36.9s.' — pull the
    real number out rather than guessing a fixed backoff."""
    match = re.search(r"retry in ([\d.]+)s", message)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return default


class GeminiProvider(ModelProvider):
    _ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, config: Config, timeout_seconds: int = 60):
        self.config = config
        self.timeout_seconds = timeout_seconds

    def generate(self, history: list[dict], tools_schema: list[dict]) -> ModelResponse:
        try:
            payload = {"contents": _history_to_gemini(history)}
            if tools_schema:
                payload["tools"] = _tools_to_gemini(tools_schema)

            url = self._ENDPOINT.format(model=self.config.gemini_model)
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "x-goog-api-key": self.config.gemini_api_key},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                # reached the server, got an error status — read the real message out of the body
                try:
                    error_body = json.loads(e.read().decode("utf-8"))
                    message = error_body.get("error", {}).get("message", str(e))
                except Exception:
                    message = str(e)
                if e.code == 429:
                    return ModelResponse(
                        error=f"Rate limited: {message}", retry_after_seconds=_extract_retry_seconds(message)
                    )
                return ModelResponse(error=f"Gemini API error: {message}")
            except (urllib.error.URLError, TimeoutError, OSError):
                # never reached the server at all — DNS failure, connection refused, timeout
                return ModelResponse(error="No local model")

            if "error" in body:
                return ModelResponse(error=f"Gemini API error: {body['error'].get('message', body['error'])}")

            try:
                parts = body["candidates"][0]["content"]["parts"]
            except (KeyError, IndexError):
                return ModelResponse(error=f"Unexpected response shape from Gemini: {body}")

            text_parts = []
            tool_calls = []
            for part in parts:
                if "text" in part:
                    text_parts.append(part["text"])
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    tool_calls.append(
                        ToolCallRequest(
                            name=fc["name"],
                            args=fc.get("args", {}),
                            call_id=fc.get("id"),
                            thought_signature=part.get("thoughtSignature"),
                        )
                    )

            return ModelResponse(text="\n".join(text_parts) if text_parts else None, tool_calls=tool_calls)
        except Exception as e:  # a bug here must not crash the loop either
            return ModelResponse(error=f"Unexpected error calling Gemini: {type(e).__name__}: {e}")


if __name__ == "__main__":
    print("=== FakeModelProvider: scripted playback ===")
    fake = FakeModelProvider(
        [
            ModelResponse(tool_calls=[ToolCallRequest(name="write_file", args={"path": "demo.txt", "content": "hi"})]),
            ModelResponse(text="Done — wrote demo.txt."),
        ]
    )
    print("turn 1 ->", fake.generate(history=[], tools_schema=[]))
    print("turn 2 ->", fake.generate(history=[], tools_schema=[]))
    try:
        fake.generate(history=[], tools_schema=[])
        print("turn 3 -> DID NOT RAISE — bug")
    except IndexError as e:
        print("turn 3 (script exhausted, should raise) ->", e)

    print()
    print("=== schema translation: lowercase JSON-schema -> Gemini's uppercase Type enum ===")
    from .tools.filesystem import WriteFileTool

    sample_schema = WriteFileTool().schema
    print("original:", sample_schema["parameters"])
    print("for Gemini:", _tools_to_gemini([sample_schema])[0]["functionDeclarations"][0]["parameters"])

    print()
    print("=== history translation ===")
    sample_history = [
        {"kind": "goal", "content": "Write hi to demo.txt", "data": {}},
        {"kind": "tool_call", "content": "write_file(...)", "data": {"tool": "write_file", "args": {"path": "demo.txt"}}},
        {"kind": "tool_result", "content": "wrote 2 chars", "data": {"tool": "write_file", "success": True}},
        {"kind": "model_response", "content": "Done.", "data": {}},
    ]
    for entry in _history_to_gemini(sample_history):
        print(" ", entry)

    print()
    print("=== GeminiProvider — live call against your real key/model ===")
    from .config import load_config

    config = load_config()
    provider = GeminiProvider(config)
    response = provider.generate(
        history=[{"kind": "goal", "content": "Say the word 'pong' and nothing else.", "data": {}}],
        tools_schema=[],
    )
    print(response)
