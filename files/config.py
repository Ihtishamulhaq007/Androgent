"""
agent/config.py

Resolves and validates all runtime configuration for the agent. Pure
loading — no tool logic, no model calls. Every other module can assume
a `Config` it receives is already valid.

Required environment variables:
    GEMINI_API_KEY   Your Gemini API key.
    GEMINI_MODEL     The model string you already validated for the
                      terminal chatbot — not hardcoded here on purpose,
                      since that can go stale.

Optional environment variables:
    AGENT_SHARED_ROOT      Overrides READ_ROOT. Defaults to
                            ~/storage/shared (what termux-setup-storage
                            creates).
    AGENT_ITERATION_BLOCK  Iteration budget per run, and size of each
                            extension offered when it runs out. Default 40.

Derived, not configurable:
    write_root        = <shared_root>/termux      WRITE_ROOT
    stage_root        = <write_root>/temp          staging area for edits to
                                                     files that live outside
                                                     write_root
    log_dir           = <write_root>/agent/logs    runtime logs — NOT the
                                                     same "agent" as this
                                                     source package, just a
                                                     coincidence of naming
    capabilities_path = <write_root>/agent/capabilities.txt
                          Persistent (not per-session) knowledge the agent
                          reads AND writes: "capability :: dependency"
                          lines describing what's installed and what it
                          enables (e.g. "convert audio/video :: ffmpeg").
                          Seeded with a quick auto-detect on first run;
                          the agent is expected to append to it going
                          forward via the normal append_file tool.
    preferences_path   = <write_root>/agent/preferences.txt
                          100% user-authored, never auto-modified by the
                          agent. Free-text standing instructions/
                          preferences ("prefer ffmpeg over X", "ask
                          before touching Documents/Official"), included
                          in every request's system instruction. This is
                          the human-editable, human-readable window into
                          what gets fed to the model beyond the literal
                          goal text.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    """Something required for startup is missing or invalid. Always actionable."""


@dataclass(frozen=True)
class Config:
    shared_root: Path      # READ_ROOT
    write_root: Path       # WRITE_ROOT
    stage_root: Path
    gemini_api_key: str
    gemini_model: str
    iteration_block: int
    log_dir: Path
    capabilities_path: Path
    preferences_path: Path

    def safe_summary(self) -> dict:
        """Config as a dict with the API key redacted — safe to log or print."""
        key = self.gemini_api_key
        redacted = f"***{key[-4:]}" if len(key) >= 4 else "***"
        return {
            "shared_root": str(self.shared_root),
            "write_root": str(self.write_root),
            "stage_root": str(self.stage_root),
            "log_dir": str(self.log_dir),
            "capabilities_path": str(self.capabilities_path),
            "preferences_path": str(self.preferences_path),
            "gemini_model": self.gemini_model,
            "iteration_block": self.iteration_block,
            "gemini_api_key": redacted,
        }


# A small, deliberately short list of common Termux tools worth knowing about
# on sight. Not exhaustive — the point is a cheap baseline, not completeness.
# The agent is expected to grow this file itself over time via append_file.
_KNOWN_TOOLS = {
    "git": "version control",
    "ffmpeg": "convert/process audio and video",
    "yt-dlp": "download video/audio from YouTube and other sites",
    "pandoc": "convert between document formats",
    "pip": "install Python packages",
    "node": "run JavaScript/Node tooling",
    "npm": "install Node packages",
    "convert": "ImageMagick — image conversion/processing",
    "jq": "parse/filter JSON on the command line",
    "curl": "make HTTP requests",
    "wget": "download files over HTTP",
    "rsync": "sync/copy files efficiently",
    "tar": "archive/extract .tar files",
    "unzip": "extract .zip files",
    "sqlite3": "query SQLite databases",
}


def _seed_capabilities_file(path: Path) -> None:
    if path.exists():
        return
    lines = [
        "# Agent capabilities — what's installed, what it enables.",
        "# Format: capability :: dependency",
        "# Edit freely, human or agent. The agent reads this at the start of",
        "# every run and is expected to append a line here whenever it installs",
        "# something new or discovers a non-obvious working approach, so future",
        "# runs don't have to rediscover it from scratch.",
        "",
    ]
    for tool, description in _KNOWN_TOOLS.items():
        if shutil.which(tool):
            lines.append(f"{description} :: {tool}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _seed_preferences_file(path: Path) -> None:
    if path.exists():
        return
    template = """# Your standing preferences for the agent — plain English, edit freely.
# Included in every request the agent makes, alongside device paths and
# capabilities. The agent never writes to this file itself; it's yours.
#
# Examples (delete these, replace with your own):
#   Prefer ffmpeg over other tools for audio/video conversion.
#   Ask before touching anything under Documents/Official.
#   Keep generated filenames lowercase with underscores, no spaces.
#   When creating scripts, put them in termux/py, not scattered around.
"""
    path.write_text(template, encoding="utf-8")


def load_config() -> Config:
    shared_root = _resolve_shared_root()

    write_root = (shared_root / "termux").resolve()
    if not write_root.is_dir():
        raise ConfigError(
            f"WRITE_ROOT does not exist: {write_root}\n"
            "Expected a 'termux' folder directly under your shared storage root."
        )

    stage_root = write_root / "temp"
    stage_root.mkdir(parents=True, exist_ok=True)

    log_dir = write_root / "agent" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    capabilities_path = write_root / "agent" / "capabilities.txt"
    _seed_capabilities_file(capabilities_path)

    preferences_path = write_root / "agent" / "preferences.txt"
    _seed_preferences_file(preferences_path)

    return Config(
        shared_root=shared_root,
        write_root=write_root,
        stage_root=stage_root,
        gemini_api_key=_require_env("GEMINI_API_KEY"),
        gemini_model=_require_env("GEMINI_MODEL"),
        iteration_block=_positive_int_env("AGENT_ITERATION_BLOCK", default=40),
        log_dir=log_dir,
        capabilities_path=capabilities_path,
        preferences_path=preferences_path,
    )


def _resolve_shared_root() -> Path:
    default = Path.home() / "storage" / "shared"
    shared_root = Path(os.environ.get("AGENT_SHARED_ROOT", str(default))).expanduser().resolve()
    if not shared_root.is_dir():
        raise ConfigError(
            f"SHARED_ROOT does not exist: {shared_root}\n"
            "This should be the folder termux-setup-storage created "
            "(normally ~/storage/shared). Run termux-setup-storage, or "
            "set AGENT_SHARED_ROOT to the right path."
        )
    return shared_root


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"{name} is not set. Export it before starting the agent:\n  export {name}=...")
    return value


def _positive_int_env(name: str, *, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got: {raw!r}")
    if value <= 0:
        raise ConfigError(f"{name} must be a positive integer, got: {value}")
    return value


if __name__ == "__main__":
    cfg = load_config()
    print("Config OK:")
    for key, value in cfg.safe_summary().items():
        print(f"  {key:16} = {value}")
