"""
agent/tools/filesystem.py

Every filesystem tool the agent has: read_file, write_file, append_file,
list_directory, find_files, grep, plus the staging trio — stage_file,
promote_file, delete_file. Every path any of these touches goes through
permissions.py first; nothing here decides on its own that a path is OK.

grep is implemented natively here (re + rglob), not shelled out, so it
stays inside the same path-validated code as everything else.

promote_file and delete_file follow the confirmation contract from
tools/base.py: called with confirmed=False (the default), they report
what they WOULD do via ToolResult.needs_confirmation and touch nothing.
controller.py (not yet built) is the only thing that prompts the human
and, if approved, calls them again with confirmed=True.
"""

from __future__ import annotations

import re
import shutil

from ..config import Config
from ..permissions import (
    PermissionDenied,
    check_delete,
    check_read,
    check_write,
    resolve_promotion,
    stage_path_for,
)
from .base import Tool, ToolResult

_MAX_READ_BYTES = 5_000_000  # past this, use grep/find_files instead of reading the whole file
_MAX_GREP_MATCHES = 200
_MAX_LIST_ENTRIES = 2000


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read a text file's full contents. Refuses files over ~5MB — use grep or find_files on those instead."

    @property
    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Absolute, or relative to READ_ROOT."}},
                "required": ["path"],
            },
        }

    def run(self, config: Config, path: str, **kwargs) -> ToolResult:
        try:
            resolved = check_read(path, config)
        except PermissionDenied as e:
            return ToolResult.fail(str(e))
        if not resolved.is_file():
            return ToolResult.fail(f"Not a file: {resolved}")
        size = resolved.stat().st_size
        if size > _MAX_READ_BYTES:
            return ToolResult.fail(
                f"{resolved} is {size} bytes, over the {_MAX_READ_BYTES}-byte read limit. "
                "Use grep or find_files instead of reading the whole thing."
            )
        try:
            content = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return ToolResult.fail(f"Could not read {resolved}: {e}")
        return ToolResult.ok(content, path=str(resolved), bytes=size)


class WriteFileTool(Tool):
    name = "write_file"
    description = "Write text to a file inside WRITE_ROOT, overwriting it if it exists. Creates parent directories as needed."

    @property
    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Absolute, or relative to READ_ROOT (not WRITE_ROOT) — include the 'termux' "
                            "segment, e.g. 'termux/notes.txt', to land inside WRITE_ROOT, which is where "
                            "writes are actually allowed. A bare relative name like 'notes.txt' resolves "
                            "outside WRITE_ROOT and will be denied."
                        ),
                    },
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        }

    def run(self, config: Config, path: str, content: str, **kwargs) -> ToolResult:
        try:
            resolved = check_write(path, config)
        except PermissionDenied as e:
            return ToolResult.fail(str(e))
        resolved.parent.mkdir(parents=True, exist_ok=True)
        try:
            resolved.write_text(content, encoding="utf-8")
        except OSError as e:
            return ToolResult.fail(f"Could not write {resolved}: {e}")
        return ToolResult.ok(f"wrote {len(content)} chars to {resolved}", path=str(resolved))


class AppendFileTool(Tool):
    name = "append_file"
    description = "Append text to a file inside WRITE_ROOT. Creates the file (and parent directories) if needed."

    @property
    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Absolute, or relative to READ_ROOT (not WRITE_ROOT) — include the 'termux' "
                            "segment, e.g. 'termux/notes.txt', to land inside WRITE_ROOT, which is where "
                            "writes are actually allowed."
                        ),
                    },
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        }

    def run(self, config: Config, path: str, content: str, **kwargs) -> ToolResult:
        try:
            resolved = check_write(path, config)
        except PermissionDenied as e:
            return ToolResult.fail(str(e))
        resolved.parent.mkdir(parents=True, exist_ok=True)
        try:
            with resolved.open("a", encoding="utf-8") as fp:
                fp.write(content)
        except OSError as e:
            return ToolResult.fail(f"Could not append to {resolved}: {e}")
        return ToolResult.ok(f"appended {len(content)} chars to {resolved}", path=str(resolved))


class ListDirectoryTool(Tool):
    name = "list_directory"
    description = "List entries in a directory (name, type, size). Readable anywhere under READ_ROOT."

    @property
    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Directory, absolute or relative to READ_ROOT."}},
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
        entries = []
        for i, child in enumerate(sorted(resolved.iterdir())):
            if i >= _MAX_LIST_ENTRIES:
                entries.append(f"... truncated at {_MAX_LIST_ENTRIES} entries")
                break
            kind = "dir" if child.is_dir() else "file"
            size = child.stat().st_size if child.is_file() else ""
            entries.append(f"{kind:4} {size!s:>10}  {child.name}")
        return ToolResult.ok("\n".join(entries), path=str(resolved), count=len(entries))


class FindFilesTool(Tool):
    name = "find_files"
    description = "Find files under a directory matching a glob pattern (e.g. '*.py', '**/*.v')."

    @property
    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory to search from, relative to READ_ROOT."},
                    "pattern": {"type": "string", "description": "Glob pattern, e.g. '*.py' or '**/*.v'."},
                },
                "required": ["path", "pattern"],
            },
        }

    def run(self, config: Config, path: str, pattern: str, **kwargs) -> ToolResult:
        try:
            resolved = check_read(path, config)
        except PermissionDenied as e:
            return ToolResult.fail(str(e))
        if not resolved.is_dir():
            return ToolResult.fail(f"Not a directory: {resolved}")
        matches = []
        for i, match in enumerate(sorted(resolved.glob(pattern))):
            if i >= _MAX_LIST_ENTRIES:
                matches.append(f"... truncated at {_MAX_LIST_ENTRIES} matches")
                break
            matches.append(str(match))
        return ToolResult.ok("\n".join(matches), path=str(resolved), pattern=pattern, count=len(matches))


class GrepTool(Tool):
    name = "grep"
    description = "Search for a regex pattern across files under a directory. Implemented natively, not shelled out."

    @property
    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regular expression to search for."},
                    "path": {"type": "string", "description": "Directory to search under, relative to READ_ROOT."},
                    "file_glob": {"type": "string", "description": "Optional filename filter, e.g. '*.py'. Default '*'."},
                },
                "required": ["pattern", "path"],
            },
        }

    def run(self, config: Config, pattern: str, path: str, file_glob: str = "*", **kwargs) -> ToolResult:
        try:
            resolved = check_read(path, config)
        except PermissionDenied as e:
            return ToolResult.fail(str(e))
        if not resolved.is_dir():
            return ToolResult.fail(f"Not a directory: {resolved}")
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ToolResult.fail(f"Bad regex {pattern!r}: {e}")

        matches = []
        for file_path in sorted(resolved.rglob(file_glob)):
            if len(matches) >= _MAX_GREP_MATCHES:
                matches.append(f"... truncated at {_MAX_GREP_MATCHES} matches")
                break
            if not file_path.is_file():
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    matches.append(f"{file_path}:{line_no}: {line.strip()}")
                    if len(matches) >= _MAX_GREP_MATCHES:
                        break
        return ToolResult.ok("\n".join(matches), path=str(resolved), pattern=pattern, count=len(matches))


class StageFileTool(Tool):
    name = "stage_file"
    description = (
        "Copy a file from anywhere under READ_ROOT into a staging area inside WRITE_ROOT, "
        "so it can be edited/tested safely. Use before editing anything outside WRITE_ROOT."
    )

    @property
    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Original path, outside WRITE_ROOT."}},
                "required": ["path"],
            },
        }

    def run(self, config: Config, path: str, **kwargs) -> ToolResult:
        try:
            staged = stage_path_for(path, config)
            original = check_read(path, config)
        except PermissionDenied as e:
            return ToolResult.fail(str(e))
        if not original.is_file():
            return ToolResult.fail(f"Not a file: {original}")
        staged.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(original, staged)
        except OSError as e:
            return ToolResult.fail(f"Could not stage {original}: {e}")
        return ToolResult.ok(f"staged {original} -> {staged}", original=str(original), staged=str(staged))


class PromoteFileTool(Tool):
    name = "promote_file"
    description = (
        "Copy a staged, edited file back over its original location outside WRITE_ROOT. "
        "Only call this after the user has explicitly asked to update the original — "
        "it always requires a human confirmation before anything is actually copied."
    )

    @property
    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Original path (not the staged path)."},
                    "confirmed": {
                        "type": "boolean",
                        "description": "Leave false. controller.py sets this true only after the human explicitly approves.",
                    },
                },
                "required": ["path"],
            },
        }

    def run(self, config: Config, path: str, confirmed: bool = False, **kwargs) -> ToolResult:
        try:
            original, staged = resolve_promotion(path, config)
        except PermissionDenied as e:
            return ToolResult.fail(str(e))

        if not confirmed:
            return ToolResult.needs_confirmation(
                f"Would overwrite {original} with the staged copy at {staged}.",
                original=str(original),
                staged=str(staged),
            )
        try:
            shutil.copy2(staged, original)
        except OSError as e:
            return ToolResult.fail(f"Could not promote {staged} -> {original}: {e}")
        return ToolResult.ok(f"promoted {staged} -> {original}", original=str(original), staged=str(staged))


class DeleteFileTool(Tool):
    name = "delete_file"
    description = (
        "Delete a single file inside WRITE_ROOT. Always requires human confirmation. "
        "Directories are refused — use run_shell for those, at your own risk."
    )

    @property
    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Absolute, or relative to READ_ROOT (not WRITE_ROOT) — same rule as write_file. "
                            "Must resolve inside WRITE_ROOT or it's denied regardless of confirmation."
                        ),
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "Leave false. controller.py sets this true only after the human explicitly approves.",
                    },
                },
                "required": ["path"],
            },
        }

    def run(self, config: Config, path: str, confirmed: bool = False, **kwargs) -> ToolResult:
        try:
            resolved = check_delete(path, config)
        except PermissionDenied as e:
            return ToolResult.fail(str(e))
        if resolved.is_dir():
            return ToolResult.fail(f"{resolved} is a directory — delete_file only handles single files.")
        if not resolved.is_file():
            return ToolResult.fail(f"Not a file: {resolved}")

        if not confirmed:
            return ToolResult.needs_confirmation(f"Would delete {resolved}.", path=str(resolved))
        try:
            resolved.unlink()
        except OSError as e:
            return ToolResult.fail(f"Could not delete {resolved}: {e}")
        return ToolResult.ok(f"deleted {resolved}", path=str(resolved))


ALL_TOOLS = [
    ReadFileTool,
    WriteFileTool,
    AppendFileTool,
    ListDirectoryTool,
    FindFilesTool,
    GrepTool,
    StageFileTool,
    PromoteFileTool,
    DeleteFileTool,
]


if __name__ == "__main__":
    from ..config import load_config
    from .registry import ToolRegistry

    config = load_config()
    registry = ToolRegistry()
    for cls in ALL_TOOLS:
        registry.register(cls())
    print("Registered:", registry.names())
    print()

    check_dir = str(config.write_root / "fs_check")
    hello_path = str(config.write_root / "fs_check" / "hello.txt")

    registry.call("write_file", config, path=hello_path, content="hello world\nline two\n")
    print("read_file  ->", registry.call("read_file", config, path=hello_path))
    registry.call("append_file", config, path=hello_path, content="line three\n")
    print("list_dir   ->", registry.call("list_directory", config, path=check_dir))
    print("find_files ->", registry.call("find_files", config, path=check_dir, pattern="*.txt"))
    print("grep       ->", registry.call("grep", config, pattern="line", path=check_dir))
    print()

    print(
        "write outside write_root (should fail) ->",
        registry.call("write_file", config, path=str(config.shared_root / "should_not_write.txt"), content="x"),
    )

    print()
    print("--- staging round trip: stage, edit the staged copy, promote ---")
    outside_target = config.shared_root / "fs_check_outside" / "note.txt"
    outside_target.parent.mkdir(parents=True, exist_ok=True)
    outside_target.write_text("original content\n")

    stage_result = registry.call("stage_file", config, path=str(outside_target))
    print("stage_file  ->", stage_result)
    staged_path = stage_result.data["staged"]

    print("edit the staged copy via write_file ->", registry.call("write_file", config, path=staged_path, content="edited content\n"))
    print("promote (unconfirmed, should NOT copy yet) ->", registry.call("promote_file", config, path=str(outside_target)))
    print("promote (confirmed) ->", registry.call("promote_file", config, path=str(outside_target), confirmed=True))
    print("original file now reads:", repr(outside_target.read_text()))

    print()
    print("--- delete_file ---")
    print("delete (unconfirmed, should NOT delete yet) ->", registry.call("delete_file", config, path=hello_path))
    print("delete (confirmed) ->", registry.call("delete_file", config, path=hello_path, confirmed=True))
    print("delete a directory (should fail) ->", registry.call("delete_file", config, path=check_dir))
