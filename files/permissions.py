"""
agent/permissions.py

The only module allowed to say "yes, this path is OK." Every filesystem
tool must run its paths through this before touching disk. Nothing here
trusts the model — a denied path raises, it never gets silently
reinterpreted as safe.

    check_read(path, config)    -> resolved Path, or PermissionDenied
    check_write(path, config)   -> resolved Path, or PermissionDenied
    check_delete(path, config)  -> resolved Path, or PermissionDenied
                                    (same boundary as write; the human
                                    confirmation step on top of this lives
                                    in controller.py, not here)

Staging — for editing a file that's inside READ_ROOT but outside
WRITE_ROOT:
    stage_path_for(original, config)     -> where its staged copy belongs
    resolve_promotion(original, config)  -> (original, staged), verifying
                                             a staged copy actually exists

Every check resolves symlinks before comparing against the roots, so a
symlink inside WRITE_ROOT pointing outside it is caught exactly like a
literal ../ escape — both just become "outside the root" after resolve().

Also here, OFF by default: a tiny catastrophic-command check. Nothing
calls it yet — shell.py decides whether to wire it in.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from .config import Config, load_config


class PermissionDenied(RuntimeError):
    """A tool tried to touch a path outside its allowed root. Always caught
    by the caller and turned into a ToolResult — never left to crash the loop."""


def check_read(path_str: str, config: Config) -> Path:
    candidate = _resolve(path_str, base=config.shared_root)
    if not _contains(config.shared_root, candidate):
        raise PermissionDenied(f"Read denied — outside shared_root: {candidate}")
    return candidate


def check_write(path_str: str, config: Config) -> Path:
    # Relative paths resolve against shared_root, same as check_read — not
    # write_root. If these two used different bases, the same relative
    # string ("notes.txt") would mean two different absolute locations
    # depending on which check touched it, which is exactly the kind of
    # ambiguity this module exists to eliminate. To write somewhere inside
    # WRITE_ROOT with a relative path, include the "termux" segment, e.g.
    # "termux/notes.txt" — or just pass the absolute path most tools will
    # already have from an earlier read_file/list_directory/stage_file result.
    candidate = _resolve(path_str, base=config.shared_root)
    if not _contains(config.write_root, candidate):
        raise PermissionDenied(f"Write denied — outside write_root: {candidate}")
    return candidate


def check_delete(path_str: str, config: Config) -> Path:
    # Same boundary as write — deletion never leaves WRITE_ROOT. The human
    # confirmation step is a separate, controller-level gate on top of this.
    return check_write(path_str, config)


def stage_path_for(original_str: str, config: Config) -> Path:
    """Where a staged copy of `original_str` belongs, mirroring its path
    relative to shared_root under <write_root>/temp/. Raises if the file
    is already inside write_root (no need to stage it)."""
    original = check_read(original_str, config)
    if _contains(config.write_root, original):
        raise PermissionDenied(
            f"{original} is already inside write_root — edit it directly, no staging needed."
        )
    rel = original.relative_to(config.shared_root)
    return config.stage_root / rel


def resolve_promotion(original_str: str, config: Config) -> tuple[Path, Path]:
    """Verifies a staged copy exists for `original_str` and returns
    (original, staged). Does NOT perform the copy and does NOT ask for
    confirmation — that's controller.py's job, every time, no exceptions."""
    staged = stage_path_for(original_str, config)
    original = check_read(original_str, config)
    if not staged.is_file():
        raise PermissionDenied(f"No staged copy found for {original} — call stage_file first.")
    return original, staged


_CATASTROPHIC_PATTERNS = (
    "rm -rf /",
    "rm -rf ~",
    "rm -rf $HOME",
    "rm -rf --no-preserve-root",
    "mkfs",
    "dd of=/dev/",
)


def is_catastrophic_command(command: str) -> bool:
    """True for the handful of shell patterns that are never a legitimate
    build step. Not called anywhere yet — off by default, per your call."""
    normalized = " ".join(command.split())
    return any(p in normalized for p in _CATASTROPHIC_PATTERNS)


_WRITE_LIKE_COMMANDS = {
    "mkdir", "touch", "cp", "mv", "rm", "rmdir", "dd", "tee",
    "chmod", "chown", "truncate", "install", "ln", "rsync",
}
_REDIRECT_PATTERN = re.compile(r">>?\s*(\S+)")
_EXEC_PATTERN = re.compile(r"-exec\s+(\S+)((?:\s+\S+)*?)\s*[;+]")


def find_external_write_targets(command: str, config: Config) -> list[str]:
    """Heuristic, best-effort — NOT a hard guarantee, unlike check_write.
    Flags absolute paths outside WRITE_ROOT but inside SHARED_ROOT (the
    exact zone where real files live) that appear next to a write-shaped
    command (mkdir/cp/mv/rm/...), a shell redirect (>, >>), or a
    find -exec/xargs-style indirect write. Read-only commands (ls, cat,
    find, grep, pwd, ...) are never flagged — reads stay unrestricted
    everywhere, matching the rest of this project's design.

    Known gaps, on purpose rather than by accident: relative-path tricks
    ('cd .. && cp x y'), paths built from shell variables, paths piped
    through an interpreter other than find's -exec, and anything
    deliberately obfuscated all slip through. This is a speed bump for
    the honest/common case, not a security boundary — the only real
    boundary in this project is the structured tools' check_write."""
    targets: set[str] = set()

    for match in _REDIRECT_PATTERN.finditer(command):
        targets.add(match.group(1))

    for match in _EXEC_PATTERN.finditer(command):
        exec_cmd, exec_args = match.group(1), match.group(2)
        if exec_cmd in _WRITE_LIKE_COMMANDS:
            for token in exec_args.split():
                if token.startswith("/") or token.startswith("~"):
                    targets.add(token)

    for segment in re.split(r"&&|\|\||;|\|", command):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        if tokens and tokens[0] in _WRITE_LIKE_COMMANDS:
            for token in tokens[1:]:
                if token.startswith("/") or token.startswith("~"):
                    targets.add(token)

    external: set[str] = set()
    for target in targets:
        try:
            resolved = Path(target).expanduser().resolve()
        except OSError:
            continue
        if _contains(config.shared_root, resolved) and not _contains(config.write_root, resolved):
            external.add(str(resolved))
    return sorted(external)


def _resolve(path_str: str, *, base: Path) -> Path:
    p = Path(path_str).expanduser()
    if not p.is_absolute():
        p = base / p
    return p.resolve()


def _contains(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    config = load_config()
    print("Sanity check against your real config:")

    checks = [
        ("check_read(shared_root itself)", lambda: check_read(str(config.shared_root), config)),
        ("check_write(write_root itself)", lambda: check_write(str(config.write_root), config)),
        (
            "check_write('termux/sanity_check.tmp') — relative, lands inside write_root",
            lambda: check_write("termux/sanity_check.tmp", config),
        ),
        (
            "check_write('sanity_check.tmp') — relative, but OUTSIDE write_root — should be DENIED",
            lambda: check_write("sanity_check.tmp", config),
        ),
    ]
    for label, fn in checks:
        try:
            result = fn()
            print(f"  OK      {label} -> {result}")
        except PermissionDenied as e:
            print(f"  DENIED  {label} -> {e}")
