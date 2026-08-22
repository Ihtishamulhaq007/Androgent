"""
agent/tools/shell.py

run_shell — the raw escape hatch. Executes `bash -c <command>` with cwd
forced to WRITE_ROOT, no output filtering, no dangerous-pattern gating.
Deliberate: everything else in this project enforces boundaries in
Python; this tool mostly doesn't, by explicit choice.

One exception, added after real on-device testing showed the gap in
practice: ENABLE_EXTERNAL_WRITE_CONFIRM (on by default) pauses for a
real human y/n when a command looks like it writes outside WRITE_ROOT
but inside SHARED_ROOT — the zone where real files live. This is a
heuristic (permissions.find_external_write_targets), not a hard
guarantee like check_write: relative-path tricks, variable-built paths,
and anything routed through an interpreter other than find -exec can
still slip through unflagged. Reads (ls, cat, find, grep, ...) are never
flagged — that stays exactly as unrestricted as it always was.

ENABLE_CATASTROPHIC_CHECK below is a SEPARATE, still OFF-by-default
toggle for a different, narrower thing: a handful of patterns that are
never a legitimate build step regardless of location (rm -rf /, mkfs,
dd of=/dev/*). It would NOT have caught the external-write case above —
mkdir/cp aren't catastrophic, they're just out of scope. Two different
problems, two different checks.
"""

from __future__ import annotations

import subprocess

from ..config import Config
from ..permissions import find_external_write_targets, is_catastrophic_command
from .base import Tool, ToolResult

ENABLE_CATASTROPHIC_CHECK = False
ENABLE_EXTERNAL_WRITE_CONFIRM = True
_TIMEOUT_SECONDS = 300  # a single command running this long is almost certainly stuck


class RunShellTool(Tool):
    name = "run_shell"
    description = (
        "Run a shell command via bash -c, cwd forced to WRITE_ROOT. Raw — no dangerous-command "
        "gating. If the command looks like it writes outside WRITE_ROOT (mkdir/cp/mv/rm/dd/tee/"
        "redirects/find -exec targeting an external path), it pauses for human confirmation first "
        "— call again with confirmed=true only after that's been granted. IMPORTANT: relative "
        "paths here resolve against WRITE_ROOT (this tool's cwd) — a DIFFERENT base than "
        "read_file/write_file/list_directory/etc., which resolve relative paths against READ_ROOT "
        "instead. The same relative name (e.g. 'core') can mean two different real locations "
        "depending on which tool you used it with. When in doubt, use absolute paths, or run "
        "'pwd' first."
    )

    @property
    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run via bash -c."},
                    "confirmed": {
                        "type": "boolean",
                        "description": (
                            "Leave false. Only set true after a human has explicitly approved a "
                            "flagged external-write command — controller.py handles this, not you."
                        ),
                    },
                },
                "required": ["command"],
            },
        }

    def run(self, config: Config, command: str, confirmed: bool = False, **kwargs) -> ToolResult:
        if ENABLE_CATASTROPHIC_CHECK and is_catastrophic_command(command):
            return ToolResult.fail(
                f"Refused: {command!r} matches a catastrophic-command pattern. "
                "This check is normally off — someone turned it on."
            )

        if ENABLE_EXTERNAL_WRITE_CONFIRM and not confirmed:
            external_targets = find_external_write_targets(command, config)
            if external_targets:
                return ToolResult.needs_confirmation(
                    f"This command looks like it writes outside WRITE_ROOT: {', '.join(external_targets)}\n"
                    f"Command: {command}",
                    external_targets=external_targets,
                    command=command,
                )
        try:
            proc = subprocess.run(
                ["bash", "-c", command],
                cwd=config.write_root,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return ToolResult.fail(f"Command timed out after {_TIMEOUT_SECONDS}s: {command!r}")
        except OSError as e:
            return ToolResult.fail(f"Could not run command: {e}")

        output = proc.stdout
        if proc.stderr:
            output += ("\n--- stderr ---\n" if output else "") + proc.stderr

        if proc.returncode == 0:
            return ToolResult.ok(output, exit_code=0, command=command)
        return ToolResult.fail(output or f"exit code {proc.returncode}", exit_code=proc.returncode, command=command)


if __name__ == "__main__":
    from ..config import load_config
    from .registry import ToolRegistry

    config = load_config()
    registry = ToolRegistry()
    registry.register(RunShellTool())

    print("pwd (should print write_root) ->", registry.call("run_shell", config, command="pwd"))
    print()
    print(
        "simple success ->",
        registry.call(
            "run_shell", config, command="echo hello && mkdir -p shell_check && echo done > shell_check/out.txt"
        ),
    )
    print()
    print("nonzero exit ->", registry.call("run_shell", config, command="ls /this/does/not/exist"))
    print()
    print("pipes work (raw bash, not shelled through anything else) ->",
          registry.call("run_shell", config, command="printf 'b\\na\\nc\\n' | sort"))
    print()
    print("catastrophic check is OFF by default — this is NOT refused right now:")
    print(registry.call("run_shell", config, command="echo 'a real rm -rf / would run right now if you typed it'"))

    print()
    print("external-write check is ON by default — this SHOULD pause for confirmation:")
    external_path = str(config.shared_root / "shell_check_external" / "out.txt")
    flagged = registry.call("run_shell", config, command=f"mkdir -p {config.shared_root / 'shell_check_external'} && echo hi > {external_path}")
    print(flagged)
    print()
    print("...and after confirmed=True, it actually runs:")
    print(registry.call(
        "run_shell", config,
        command=f"mkdir -p {config.shared_root / 'shell_check_external'} && echo hi > {external_path}",
        confirmed=True,
    ))
