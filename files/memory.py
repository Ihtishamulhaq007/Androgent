"""
agent/memory.py

Holds everything the agent needs to remember for one run: the goal, and
every turn since — model responses, tool calls, tool results. Kept
verbatim, in order, persisted to disk after every single append, so a
process killed mid-run loses nothing.

Deliberately NOT shaped like Gemini's function-calling schema — that
translation is model.py's job when it's built. This only knows "things
that happened," in the order they happened.

Two things this is not:
  - Not what decides what to send the model each turn — that's
    controller.py, using .to_list().
  - Not something that truncates automatically. The only thing that
    shrinks history is an explicit call to summarize().

If a Memory is constructed with a session_id that already has history on
disk, it resumes from that history instead of starting fresh — this is
what makes "Android killed the process" recoverable rather than a total
loss.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .config import Config


def _new_session_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass
class Turn:
    kind: str  # "goal" | "model_response" | "tool_call" | "tool_result" | "summary"
    content: str
    data: dict = field(default_factory=dict)
    ts: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return asdict(self)


class Memory:
    def __init__(self, config: Config, goal: str, session_id: str | None = None):
        self.config = config
        self.session_id = session_id or _new_session_id()
        self.turns: list[Turn] = []

        self._path = config.log_dir / self.session_id / "memory.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)

        if self._path.exists() and self._path.stat().st_size > 0:
            self.load()
            self.goal = self.turns[0].content if self.turns else goal
        else:
            self.goal = goal
            self.add("goal", goal)

    def add(self, kind: str, content: str, **data) -> Turn:
        turn = Turn(kind=kind, content=content, data=data)
        self.turns.append(turn)
        self._append(turn)
        return turn

    def add_model_response(self, text: str) -> Turn:
        return self.add("model_response", text)

    def add_user_note(self, text: str) -> Turn:
        """Text fed TO the model that isn't the goal, a summary, or a tool
        result — human feedback after declining a confirmation, an error
        notice, etc. Must map to Gemini's 'user' role, never 'model'. Using
        add_model_response for this was the actual cause of a real bug:
        two model-role turns back to back (or one at the very end of
        history with nothing from 'user' after it) gets flatly rejected by
        the API with "Requests ending with a model turn are not
        supported." Anything that isn't the model's own generated text
        belongs here instead."""
        return self.add("user_note", text)

    def add_tool_call(self, tool_name: str, args: dict, thought_signature: str | None = None) -> Turn:
        extra = {"thought_signature": thought_signature} if thought_signature else {}
        return self.add("tool_call", f"{tool_name}({args})", tool=tool_name, args=args, **extra)

    def add_tool_result(self, tool_name: str, success: bool, output: str) -> Turn:
        return self.add("tool_result", output, tool=tool_name, success=success)

    def to_list(self) -> list[dict]:
        """Full verbatim history, in order — what gets sent to the model
        every turn. Never truncated automatically."""
        return [t.to_dict() for t in self.turns]

    def summarize(self, replacement_text: str, keep_last: int = 0) -> None:
        """Explicit compaction only — never called automatically. Collapses
        everything except the goal and the last `keep_last` turns into one
        summary turn, and rewrites the persisted file to match."""
        goal_turn = self.turns[0]
        tail = self.turns[-keep_last:] if keep_last else []
        summary_turn = Turn(kind="summary", content=replacement_text)
        self.turns = [goal_turn, summary_turn, *tail]
        self._rewrite()

    def load(self) -> None:
        """Reload turns from disk. Called automatically by __init__ when
        resuming a session that already has history."""
        if not self._path.exists():
            return
        self.turns = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            self.turns.append(Turn(**json.loads(line)))

    def _append(self, turn: Turn) -> None:
        with self._path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(turn.to_dict(), ensure_ascii=False, default=str) + "\n")

    def _rewrite(self) -> None:
        with self._path.open("w", encoding="utf-8") as fp:
            for turn in self.turns:
                fp.write(json.dumps(turn.to_dict(), ensure_ascii=False, default=str) + "\n")


if __name__ == "__main__":
    from .config import load_config

    config = load_config()
    session_id = "memory_sanity_check"

    memory = Memory(config, goal="Write a short todo list to notes/todo.md", session_id=session_id)
    memory.add_tool_call("write_file", {"path": "notes/todo.md", "content": "- buy groceries"})
    memory.add_tool_result("write_file", True, "created notes/todo.md, 15 bytes")
    memory.add_model_response("Done — todo.md created with one item.")

    print(f"Memory file: {memory._path}")
    print(f"{len(memory.turns)} turns in memory")
    print()
    print("Full verbatim history (to_list):")
    for entry in memory.to_list():
        print(f"  [{entry['kind']}] {entry['content']}")

    print()
    print("Simulating a restart — new Memory object, same session_id...")
    resumed = Memory(config, goal="THIS SHOULD BE IGNORED", session_id=session_id)
    print(f"Resumed goal: {resumed.goal!r}  (should be the ORIGINAL goal, not 'THIS SHOULD BE IGNORED')")
    print(f"Resumed with {len(resumed.turns)} turns — should match the count above")

    print()
    print("Testing summarize() (only ever called explicitly)...")
    resumed.summarize("Summary: created todo.md with a groceries reminder.", keep_last=1)
    print(f"{len(resumed.turns)} turns after summarize:")
    for entry in resumed.to_list():
        print(f"  [{entry['kind']}] {entry['content']}")
