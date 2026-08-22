"""
agent/tools/base.py

The interface every tool implements, and the shape every tool call
returns. Nothing outside this module should build a ToolResult with
different fields — this is the contract controller.py relies on to run
the loop without caring which specific tool it just called.

ToolResult.requires_confirmation exists for actions a human has to
approve before they actually happen (promote_file, delete_file, once
built): the tool returns success=False, requires_confirmation=True, and
`output` describing exactly what it would do. controller.py — nothing
else — is responsible for prompting the human and, if approved, calling
the tool again with confirmed=True. Tools never prompt for input
themselves; they only ever report state back through a ToolResult.

A tool's `run` must never raise for expected failure modes (bad path,
permission denied, model unreachable, etc.) — catch those and return
ToolResult.fail(...). Only genuine bugs should propagate, and even those
are caught one layer up, in the registry's call(), so a bug in one tool
can't crash the whole loop.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..config import Config


@dataclass
class ToolResult:
    success: bool
    output: str = ""
    error: str | None = None
    requires_confirmation: bool = False
    data: dict = field(default_factory=dict)

    @classmethod
    def ok(cls, output: str = "", **data) -> "ToolResult":
        return cls(success=True, output=output, data=data)

    @classmethod
    def fail(cls, error: str, **data) -> "ToolResult":
        return cls(success=False, error=error, data=data)

    @classmethod
    def needs_confirmation(cls, output: str, **data) -> "ToolResult":
        return cls(success=False, output=output, requires_confirmation=True, data=data)


class Tool(ABC):
    name: str
    description: str

    @property
    @abstractmethod
    def schema(self) -> dict:
        """JSON-schema-shaped parameter description, in the form Gemini's
        function-calling API expects. model.py reads these off the
        registry when building the function-declarations list."""

    @abstractmethod
    def run(self, config: Config, **kwargs) -> ToolResult:
        """Execute the tool against real config/paths. See module
        docstring for the no-raise contract."""
