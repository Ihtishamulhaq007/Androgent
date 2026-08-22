"""
agent/tools/registry.py

Where tools register themselves and where controller.py / model.py look
them up. Two jobs: keep a name -> Tool mapping, and produce the
function-declarations list Gemini's API expects.

call() is the only entry point controller.py should use to actually run
a tool — it catches anything a tool's run() lets slip through, so one
buggy tool returns a failed ToolResult instead of taking down the loop.
"""

from __future__ import annotations

from ..config import Config
from .base import Tool, ToolResult


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered.")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"No such tool: {name!r}. Registered: {self.names()}")

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict]:
        """The function-declarations list for the Gemini API request."""
        return [tool.schema for tool in self._tools.values()]

    def call(self, name: str, config: Config, **kwargs) -> ToolResult:
        try:
            tool = self.get(name)
        except KeyError as e:
            return ToolResult.fail(str(e))
        try:
            return tool.run(config, **kwargs)
        except Exception as e:  # a bug in one tool must not crash the loop
            return ToolResult.fail(f"{type(e).__name__}: {e}")


class _PingTool(Tool):
    """Not a real tool — exists only for this module's own sanity check."""

    name = "ping"
    description = "Returns 'pong'. Wiring check only."

    @property
    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {"type": "object", "properties": {}, "required": []},
        }

    def run(self, config: Config, **kwargs) -> ToolResult:
        return ToolResult.ok("pong")


class _BrokenTool(Tool):
    """Also not real — deliberately raises, to prove the registry catches it."""

    name = "broken"
    description = "Always raises. Proves registry.call() doesn't crash on a buggy tool."

    @property
    def schema(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": {"type": "object", "properties": {}}}

    def run(self, config: Config, **kwargs) -> ToolResult:
        raise RuntimeError("intentional failure for the sanity check")


if __name__ == "__main__":
    from ..config import load_config

    config = load_config()
    registry = ToolRegistry()
    registry.register(_PingTool())
    registry.register(_BrokenTool())

    print("Registered tools:", registry.names())
    print("Schemas:", registry.schemas())
    print()
    print("call('ping')          ->", registry.call("ping", config))
    print("call('broken')        ->", registry.call("broken", config))
    print("call('nonexistent')   ->", registry.call("nonexistent_tool", config))

    print()
    print("Duplicate registration should raise:")
    try:
        registry.register(_PingTool())
        print("  DID NOT RAISE — bug")
    except ValueError as e:
        print(f"  raised as expected: {e}")
