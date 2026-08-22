"""
agent/tools/git.py

Read-only git wrappers: git_status, git_diff. Both shell out to git with
cwd set to the repo directory, capture output, and return it. Both go
through check_read (not check_write) since neither changes anything —
same "read is unrestricted across READ_ROOT" model as everything else.

Nothing git-write-related here (no add/commit/push) — this tool was
explicitly scoped read-only. run_shell is there if you actually want
git writes.
"""

from __future__ import annotations

import subprocess

from ..config import Config
from ..permissions import PermissionDenied, check_read
from .base import Tool, ToolResult

_TIMEOUT_SECONDS = 60


def _mark_safe(cwd) -> None:
    """Termux's git treats /storage/emulated/0 (a FUSE mount) as having
    'dubious ownership' since the mounted UID doesn't match the process
    UID — a known Termux quirk, not a repo problem. Silently allow-list
    the directory before every git call so this never blocks anything.
    Best-effort: if this itself fails, the real git command below still
    runs and will surface its own clear error either way."""
    try:
        subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", str(cwd)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except OSError:
        pass


def _run_git(args: list[str], cwd) -> tuple[bool, str]:
    _mark_safe(cwd)
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return False, f"git {' '.join(args)} timed out after {_TIMEOUT_SECONDS}s"
    except OSError as e:
        return False, f"Could not run git: {e}"

    output = proc.stdout
    if proc.stderr:
        output += ("\n--- stderr ---\n" if output else "") + proc.stderr
    return proc.returncode == 0, output


class GitStatusTool(Tool):
    name = "git_status"
    description = "Run 'git status' in a directory. Read-only."

    @property
    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Repo directory, absolute or relative to READ_ROOT."}},
                "required": ["path"],
            },
        }

    def run(self, config: Config, path: str, **kwargs) -> ToolResult:
        try:
            resolved = check_read(path, config)
        except PermissionDenied as e:
            return ToolResult.fail(str(e))
        if not resolved.is_dir():
            return ToolResult.fail(f"Not a directory: {resolved}")
        ok, output = _run_git(["status"], cwd=resolved)
        if ok:
            return ToolResult.ok(output, path=str(resolved))
        return ToolResult.fail(output, path=str(resolved))


class GitDiffTool(Tool):
    name = "git_diff"
    description = "Run 'git diff' in a directory. Read-only. Optionally scope to one file."

    @property
    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repo directory, absolute or relative to READ_ROOT."},
                    "file": {"type": "string", "description": "Optional: scope the diff to one file, relative to path."},
                },
                "required": ["path"],
            },
        }

    def run(self, config: Config, path: str, file: str | None = None, **kwargs) -> ToolResult:
        try:
            resolved = check_read(path, config)
        except PermissionDenied as e:
            return ToolResult.fail(str(e))
        if not resolved.is_dir():
            return ToolResult.fail(f"Not a directory: {resolved}")
        args = ["diff"]
        if file:
            args.append(file)
        ok, output = _run_git(args, cwd=resolved)
        if ok:
            return ToolResult.ok(output, path=str(resolved))
        return ToolResult.fail(output, path=str(resolved))


ALL_TOOLS = [GitStatusTool, GitDiffTool]


if __name__ == "__main__":
    import subprocess as _sp

    from ..config import load_config
    from .registry import ToolRegistry

    config = load_config()
    registry = ToolRegistry()
    for cls in ALL_TOOLS:
        registry.register(cls())

    repo_dir = config.write_root / "git_check"
    repo_dir.mkdir(parents=True, exist_ok=True)
    _mark_safe(repo_dir)
    _sp.run(["git", "init", "-q"], cwd=repo_dir)
    _sp.run(["git", "config", "user.email", "sanity@check.local"], cwd=repo_dir)
    _sp.run(["git", "config", "user.name", "Sanity Check"], cwd=repo_dir)
    (repo_dir / "example.txt").write_text("line one\n")
    _sp.run(["git", "add", "example.txt"], cwd=repo_dir)
    _sp.run(["git", "commit", "-q", "-m", "initial commit"], cwd=repo_dir)

    print("git_status (clean) ->", registry.call("git_status", config, path=str(repo_dir)))

    (repo_dir / "example.txt").write_text("line one\nline two\n")
    print()
    print("git_status (dirty) ->", registry.call("git_status", config, path=str(repo_dir)))
    print()
    print("git_diff ->", registry.call("git_diff", config, path=str(repo_dir)))
    print()
    print("not a git repo (should fail cleanly) ->", registry.call("git_status", config, path=str(config.shared_root / "Documents")))
    print()
    print("path outside READ_ROOT (should be denied) ->", registry.call("git_status", config, path="/tmp"))
