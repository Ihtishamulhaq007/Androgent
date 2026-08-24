"""
agent/logger.py

Two logs, written together, every time:
  - <log_dir>/<session_id>/audit.jsonl   full structured record, one JSON
                                          object per line. Reproducible.
                                          Nothing is summarized or dropped.
  - <log_dir>/<session_id>/session.log   the same events, formatted for a
                                          human to skim. This is what you
                                          read when you pause a run and
                                          want to check progress.

Session id is a UTC timestamp, so every run gets its own folder under
<WRITE_ROOT>/agent/logs — old sessions are never overwritten.

Call log_tool_call / log_tool_result / log_shell / log_file_change /
log_model_response / log_error / log_pause / log_resume as things
happen. Everything funnels through log(), so the two files never drift
apart from each other.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import Config, load_config


def _slugify(text: str, max_len: int = 40) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", text.lower().strip()).strip("-")
    return text[:max_len].rstrip("-") or "untitled"


def _new_session_id(goal: str | None = None) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    return f"{timestamp}_{_slugify(goal)}" if goal else timestamp


@dataclass
class SessionLogger:
    config: Config
    session_id: str = field(default_factory=_new_session_id)
    session_dir: Path = field(init=False)
    audit_path: Path = field(init=False)
    human_path: Path = field(init=False)
    _logger: logging.Logger = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.session_dir = self.config.log_dir / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.audit_path = self.session_dir / "audit.jsonl"
        self.human_path = self.session_dir / "session.log"

        self._logger = logging.getLogger(f"agent.session.{self.session_id}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        if not self._logger.handlers:
            file_handler = logging.FileHandler(self.human_path, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", "%H:%M:%S"))
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(file_handler)
            self._logger.addHandler(console_handler)

    # ---- generic ----

    def log(self, event: str, **fields) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": self.session_id,
            "event": event,
            **fields,
        }
        with self.audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        self._logger.info(self._humanize(event, fields))

    # ---- convenience wrappers ----

    def log_tool_call(self, tool_name: str, args: dict) -> None:
        self.log("tool_call", tool=tool_name, args=args)

    def log_tool_result(self, tool_name: str, success: bool, summary: str) -> None:
        self.log("tool_result", tool=tool_name, success=success, summary=summary)

    def log_shell(self, command: str, returncode: int) -> None:
        self.log("shell", command=command, returncode=returncode)

    def log_file_change(self, path: str, action: str) -> None:
        self.log("file_change", path=path, action=action)

    def log_model_response(self, text: str) -> None:
        self.log("model_response", text=text)

    def log_error(self, message: str) -> None:
        self.log("error", message=message)

    def log_pause(self, reason: str) -> None:
        self.log("pause", reason=reason)

    def log_resume(self) -> None:
        self.log("resume")

    # ---- reading back, for the pause/verify view ----

    def tail(self, n: int = 20) -> str:
        """Last n lines of the human-readable log — what controller.py
        shows you when you pause a run."""
        if not self.human_path.exists():
            return ""
        lines = self.human_path.read_text(encoding="utf-8").splitlines()
        return "\n".join(lines[-n:])

    @staticmethod
    def _humanize(event: str, fields: dict) -> str:
        if event == "tool_call":
            return f"CALL {fields['tool']} {fields['args']}"
        if event == "tool_result":
            status = "ok" if fields["success"] else "FAILED"
            return f"RESULT {fields['tool']} [{status}] {fields['summary']}"
        if event == "shell":
            return f"SHELL (exit {fields['returncode']}) $ {fields['command']}"
        if event == "file_change":
            return f"FILE {fields['action']} {fields['path']}"
        if event == "model_response":
            return f"MODEL: {fields['text']}"
        if event == "error":
            return f"ERROR: {fields['message']}"
        if event == "pause":
            return f"PAUSED: {fields['reason']}"
        if event == "resume":
            return "RESUMED"
        return f"{event.upper()} {fields}"


if __name__ == "__main__":
    config = load_config()
    logger = SessionLogger(config)
    print(f"Session: {logger.session_id}")
    print(f"Audit log:  {logger.audit_path}")
    print(f"Human log:  {logger.human_path}")
    print()

    logger.log_tool_call("write_file", {"path": "notes/todo.md", "content": "- buy groceries"})
    logger.log_tool_result("write_file", True, "created notes/todo.md, 15 bytes")
    logger.log_shell("mkdir -p notes && ls notes", 0)
    logger.log_file_change("notes/todo.md", "created")
    logger.log_error("write denied: outside write_root")
    logger.log_pause("sanity check complete")

    print()
    print("last 5 lines of human-readable log:")
    print(logger.tail(5))
