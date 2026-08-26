"""
agent/controller.py

The loop. Owns nothing about specific tools, storage, or the model API —
only the sequence: ask the model, run whatever it asked for, feed results
back, repeat. Every actual boundary (paths, confirmation, iteration
budget) is enforced by modules built earlier; this wires them together
and makes the pause/resume/extend decisions a human interface needs.

Ends when:
  - the model calls finish_task and the human confirms stopping here, or
  - the human declines to extend the iteration budget when it runs out, or
  - the human declines to keep retrying after "No local model", or
  - Ctrl+C — treated as a clean cancel, not a crash.

Confirmation-required tool results (ToolResult.requires_confirmation) are
the other pause point: nothing with that flag ever executes without an
explicit y/n from a real human at the terminal, regardless of what the
model says or how many times it asks.

Context-limit detection (_looks_like_context_limit) is a best-effort
heuristic on the error message text — I can't verify Gemini's exact
wording for a real context-overflow error without actually triggering
one, so treat this as "probably catches it," not a guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from .config import Config
from .logger import SessionLogger, _new_session_id
from .memory import Memory
from .model import ModelProvider
from .tools.base import Tool, ToolResult
from .tools.registry import ToolRegistry


class FinishTaskTool(Tool):
    name = "finish_task"
    description = (
        "Call this when you believe the goal has been achieved, or you're unrecoverably stuck. "
        "Doesn't end anything by itself — the run pauses here so the human can check your "
        "summary against the logs before deciding whether to actually stop."
    )

    @property
    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "What you did, and why you believe this is done (or stuck).",
                    },
                    "success": {"type": "boolean", "description": "Your own assessment: did the goal succeed?"},
                },
                "required": ["summary", "success"],
            },
        }

    def run(self, config: Config, summary: str, success: bool = True, **kwargs) -> ToolResult:
        return ToolResult.ok(summary, success=success)


_CONTEXT_LIMIT_HINTS = ("exceed", "too long", "maximum", "limit")


def _looks_like_context_limit(error_message: str) -> bool:
    lower = error_message.lower()
    return "token" in lower and any(hint in lower for hint in _CONTEXT_LIMIT_HINTS)


@dataclass
class RunOutcome:
    status: str  # "finished" | "cancelled" | "no_connectivity" | "budget_declined" | "context_limit"
    summary: str | None = None


class Controller:
    def __init__(
        self,
        config: Config,
        registry: ToolRegistry,
        provider: ModelProvider,
        goal: str,
        session_id: str | None = None,
    ):
        self.config = config
        self.registry = registry
        self.provider = provider
        self.goal = goal
        if "finish_task" not in registry.names():
            registry.register(FinishTaskTool())

        self.session_id = session_id or _new_session_id(goal)
        self.logger = SessionLogger(self.config, session_id=self.session_id)
        self.iterations_used = 0
        self.budget = config.iteration_block

    def run(self) -> RunOutcome:
        memory = Memory(self.config, goal=self.goal, session_id=self.session_id)
        preview = self.provider.context_preview()
        if preview:
            self.logger.log("system_context", text=preview)
        try:
            return self._loop(memory)
        except KeyboardInterrupt:
            self.logger.log_pause("cancelled by user (Ctrl+C)")
            print("\nCancelled.")
            return RunOutcome(status="cancelled")

    def _loop(self, memory: Memory) -> RunOutcome:
        while True:
            if self.iterations_used >= self.budget:
                if not self._ask_extend():
                    return RunOutcome(status="budget_declined")
                self.budget += self.config.iteration_block

            self.iterations_used += 1
            response = self.provider.generate(memory.to_list(), self.registry.schemas())

            if response.error == "No local model":
                self.logger.log_error("No local model — connectivity lost")
                if not self._ask_yes_no("No local model (connectivity lost). Retry?"):
                    return RunOutcome(status="no_connectivity")
                self.iterations_used -= 1  # don't burn budget on a connectivity retry
                continue

            if response.retry_after_seconds is not None:
                wait = response.retry_after_seconds + 1.0  # small buffer past what Google reports
                self.logger.log("rate_limited", wait_seconds=wait, message=response.error)
                print(f"\nRate limited — waiting {wait:.0f}s before retrying (free tier: 15 requests/minute)...")
                time.sleep(wait)
                self.iterations_used -= 1  # a wait-and-retry isn't a real turn, don't burn budget on it
                continue

            if response.error:
                self.logger.log_error(response.error)
                if _looks_like_context_limit(response.error):
                    print()
                    print(self.logger.tail(20))
                    print()
                    print("This looks like it hit the model's context limit — retrying won't fix it.")
                    print("Consider calling memory.summarize(...) before continuing.")
                    if not self._ask_yes_no("Try again anyway?"):
                        return RunOutcome(status="context_limit")
                    continue
                memory.add_user_note(f"[error] {response.error}")
                continue

            if response.text:
                memory.add_model_response(response.text)
                self.logger.log_model_response(response.text)

            for call in response.tool_calls:
                memory.add_tool_call(call.name, call.args, thought_signature=call.thought_signature)
                self.logger.log_tool_call(call.name, call.args)

                result = self.registry.call(call.name, self.config, **call.args)

                needed_confirmation = result.requires_confirmation
                if needed_confirmation:
                    approved, feedback = self._ask_confirm(call.name, result.output)
                    if approved:
                        result = self.registry.call(call.name, self.config, **{**call.args, "confirmed": True})
                    else:
                        reason = f"Human declined: {result.output}"
                        if feedback:
                            reason += f"\nHuman feedback: {feedback}"
                        result = ToolResult.fail(reason)

                summary = result.output if result.success else (result.error or "")
                memory.add_tool_result(call.name, result.success, summary)
                self.logger.log_tool_result(call.name, result.success, summary)

                if call.name == "finish_task":
                    claimed_success = call.args.get("success", True)
                    accepted, feedback = self._ask_finish(call.args.get("summary", ""), claimed_success)
                    if accepted:
                        self.logger.log_pause(f"human confirmed finish: {call.args.get('summary', '')}")
                        return RunOutcome(status="finished", summary=call.args.get("summary", ""))
                    note = f"[human] Not done yet. {feedback}" if feedback else "[human] Not done yet — keep going."
                    memory.add_user_note(note)
                    self.logger.log_resume()

                if needed_confirmation:
                    break  # halt the rest of this batch — model gets a fresh turn either way

    # ---- human interaction — every pause point funnels through here ----

    def _ask_yes_no(self, prompt: str) -> bool:
        while True:
            try:
                answer = input(f"{prompt} [y/n] ").strip().lower()
            except EOFError:
                print("\n(no input available on stdin — treating this as 'no'; nothing proceeds without an explicit yes)")
                return False
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            print("Please answer y or n.")

    def _ask_extend(self) -> bool:
        print()
        print(self.logger.tail(20))
        print()
        print(f"Iteration budget ({self.budget}) reached.")
        return self._ask_yes_no(f"Run another {self.config.iteration_block} iterations?")

    def _ask_feedback(self, prompt: str) -> str:
        try:
            return input(f"{prompt}\n> ").strip()
        except EOFError:
            return ""

    def _ask_confirm(self, tool_name: str, description: str) -> tuple[bool, str]:
        print()
        print(f"CONFIRMATION NEEDED for {tool_name}:")
        print(f"  {description}")
        if self._ask_yes_no("Proceed?"):
            return True, ""
        feedback = self._ask_feedback("Why not, or what should it do instead? (blank = just skip it)")
        return False, feedback

    def _ask_finish(self, summary: str, claimed_success: bool) -> tuple[bool, str]:
        print()
        print(self.logger.tail(30))
        print()
        print(f"Model believes the task is {'done' if claimed_success else 'stuck/failed'}:")
        print(f"  {summary}")
        if self._ask_yes_no("Accept and stop here?"):
            return True, ""
        feedback = self._ask_feedback("What's missing, or what should happen next? (blank = just keep going)")
        return False, feedback


if __name__ == "__main__":
    from .config import load_config
    from .model import FakeModelProvider, ModelResponse, ToolCallRequest
    from .tools.filesystem import ALL_TOOLS as FS_TOOLS

    config = load_config()
    registry = ToolRegistry()
    for cls in FS_TOOLS:
        registry.register(cls())

    outside_file = config.shared_root / "ctrl_check_outside" / "note.txt"
    outside_file.parent.mkdir(parents=True, exist_ok=True)
    outside_file.write_text("original\n")

    write_path = str(config.write_root / "ctrl_check" / "hello.txt")

    script = FakeModelProvider(
        [
            ModelResponse(tool_calls=[ToolCallRequest(name="write_file", args={"path": write_path, "content": "hello\n"})]),
            ModelResponse(tool_calls=[ToolCallRequest(name="stage_file", args={"path": str(outside_file)})]),
            ModelResponse(
                tool_calls=[
                    ToolCallRequest(
                        name="write_file",
                        args={
                            "path": str(config.stage_root / "ctrl_check_outside" / "note.txt"),
                            "content": "edited by controller demo\n",
                        },
                    )
                ]
            ),
            ModelResponse(tool_calls=[ToolCallRequest(name="promote_file", args={"path": str(outside_file)})]),
            ModelResponse(
                tool_calls=[
                    ToolCallRequest(
                        name="finish_task",
                        args={"summary": "Wrote hello.txt, edited, and promoted the staged file.", "success": True},
                    )
                ]
            ),
        ]
    )

    controller = Controller(
        config,
        registry,
        script,
        goal="Write hello.txt and promote the staged outside file, then finish.",
        session_id="controller_sanity_check",
    )

    print("This will really ask you twice: once to approve the promote_file confirmation,")
    print("once to accept finish_task's claim that the goal is done. Type y both times.\n")
    outcome = controller.run()

    print()
    print("Outcome:", outcome)
    print("Iterations used:", controller.iterations_used)
    print(f"Session dir: {controller.logger.session_dir}")
    print()
    print("write_file target exists:", (config.write_root / "ctrl_check" / "hello.txt").exists())
    print("outside file now reads:", repr(outside_file.read_text()))
