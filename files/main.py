"""
agent/main.py

The actual entrypoint — everything else in this project has been a
module-by-module demo up to this point. This is what you run for real.

Usage:
    python3 -m agent.main --goal "Fix my Verilog CPU until it compiles"
    python3 -m agent.main --goal "Implement UART RX" --resume 20260821T120000Z

--resume continues an existing session's verbatim history instead of
starting fresh — Memory auto-detects and loads it from disk given the
same session_id; nothing extra to wire up.
"""

from __future__ import annotations

import argparse
import os
import sys

from .config import ConfigError, load_config
from .controller import Controller
from .logger import _new_session_id
from .model import GeminiProvider
from .tools.filesystem import ALL_TOOLS as FILESYSTEM_TOOLS
from .tools.git import ALL_TOOLS as GIT_TOOLS
from .tools.registry import ToolRegistry
from .tools.shell import RunShellTool


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for cls in [*FILESYSTEM_TOOLS, *GIT_TOOLS]:
        registry.register(cls())
    registry.register(RunShellTool())
    return registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent", description="Autonomous coding agent for Termux.")
    parser.add_argument("--goal", required=True, help="What you want the agent to do.")
    parser.add_argument(
        "--resume", metavar="SESSION_ID", default=None, help="Resume an existing session instead of starting fresh."
    )
    args = parser.parse_args(argv)

    try:
        config = load_config()
    except ConfigError as e:
        print(f"Cannot start: {e}", file=sys.stderr)
        return 1

    registry = build_registry()
    provider = GeminiProvider(config)

    goal = args.goal
    invoked_from = os.environ.get("AGENT_INVOKED_FROM")
    if invoked_from:
        goal = (
            f"[Context: the user's terminal was at '{invoked_from}' when they gave this goal. "
            f"If the goal below uses relative references like '.', '..', or '../something', "
            f"they mean it relative to '{invoked_from}' — NOT relative to WRITE_ROOT — since "
            f"that's the directory the user was actually standing in.]\n{args.goal}"
        )

    controller = Controller(
        config, registry, provider, goal=goal, session_id=args.resume or _new_session_id(args.goal)
    )

    print(f"Session:  {controller.session_id}")
    print(f"Goal:     {args.goal}")
    print(f"Model:    {config.gemini_model}")
    print(f"Tools:    {', '.join(registry.names())}")
    print(f"Log dir:  {controller.logger.session_dir}")
    print()

    outcome = controller.run()

    print()
    print(f"--- {outcome.status} ---")
    if outcome.summary:
        print(outcome.summary)
    print(f"Iterations used: {controller.iterations_used}")
    print(f"Full log:        {controller.logger.session_dir}")

    return 0 if outcome.status == "finished" else 1


if __name__ == "__main__":
    raise SystemExit(main())
