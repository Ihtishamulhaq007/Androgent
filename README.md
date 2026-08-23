# agent

An autonomous coding agent that runs entirely on-device on Android, using Termux
as the runtime and the Gemini API as the model backend. You give it a goal in
plain English; it plans, calls tools (read/write files, run shell commands, git,
grep) in a loop, and stops when it believes the goal is done or it gets stuck —
pausing for your confirmation at anything risky along the way.

## How it works

```
you (--goal) → main.py → Controller loop → Gemini (model.py)
                              │                  │
                              │          "call tool X with args Y"
                              ▼                  ▼
                         ToolRegistry ──────► Tool.run()
                              │
                    permissions.py checks every
                    path before any read/write
```

**The loop (`controller.py`)** repeatedly: sends the conversation + goal to
Gemini, gets back a tool call, executes it, feeds the result back, repeats.
It stops when the model calls `finish_task` and you confirm, when you decline
to extend the iteration budget, or on Ctrl+C.

**Tools available to the model:**

| Tool | Does |
|---|---|
| `read_file` / `write_file` / `append_file` | File I/O, routed through permission checks |
| `list_directory` / `find_files` / `grep` | Explore the filesystem |
| `stage_file` / `promote_file` | Edit a copy first, then promote it into place |
| `delete_file` | Remove a file |
| `run_shell` | Execute an arbitrary shell command |
| `git_status` / `git_diff` | Read-only git inspection |
| `finish_task` | Signal the goal is done or the agent is stuck — always pauses for human confirmation |

**Safety boundaries:**
- `permissions.py` checks every file path before any read or write against
  `WRITE_ROOT` — the agent can't casually write outside its sandboxed folder.
- Anything flagged `requires_confirmation` (like `promote_file`, `delete_file`)
  never executes without an explicit y/n from you at the terminal, no matter
  what the model asks.
- Sessions are logged to disk — a JSONL audit trail plus a human-readable log
  per run — so you can review exactly what happened.

**Sessions** are identified by a timestamp ID and can be resumed with
`--resume SESSION_ID`, continuing the exact prior conversation history instead
of starting fresh.

## Dependencies

None — the agent itself is pure Python standard library (`urllib` for the
Gemini API calls, no `pip install` needed for the agent code).

You do need on the Termux side:

| Requirement | Why |
|---|---|
| Termux (F-Droid build recommended) | Runtime environment |
| `python` (Termux pkg) | Runs the agent |
| `git` (Termux pkg) | Used by the agent's read-only git tools, and to clone/manage this repo |
| Gemini API key | Free tier at https://aistudio.google.com/apikey |
| `termux-setup-storage` access | Lets the agent read/write to shared storage |

`install.sh` installs the two Termux packages and walks you through the rest.

## Setup

```bash
git clone https://github.com/Ihtishamulhaq007/Androgent.git
cd Androgent
bash install.sh
source ~/.bashrc
```

`install.sh` installs `python` and `git`, sets up storage access, fixes a
known Termux/git "dubious ownership" issue, and prompts you once for your
Gemini API key and model — then saves both to `~/.bashrc` so you never
re-enter them. Get a free key at https://aistudio.google.com/apikey before
you start the script, since it'll ask for it.

## Run

Run from the repo root (the folder containing `files/`):

```bash
python3 -m files.main --goal "your goal here"
```

Resume a previous session:

```bash
python3 -m files.main --goal "your goal here" --resume 20260821T120000Z
```

## Notes

- Files the agent writes land under `~/storage/shared/termux` on your phone.
- Override that location with `export AGENT_SHARED_ROOT=/some/other/path`.
- Logs (audit + human-readable) are written per-session and gitignored.






# 10-Line Security Summary
1. Shell Access
run_shell gives Gemini arbitrary bash -c execution under Termux's permissions.
2. No Shell Gate
Dangerous-command detection and human confirmation are currently disabled.
3. Permission Bypass
Shell commands bypass the agent's WRITE_ROOT restriction.
4. File Destruction
Accessible shared-storage files could potentially be deleted or corrupted.
5. Agent Compromise
The agent's own source, configuration, and safety mechanisms could be modified or deleted.
6. Secret Exposure
Accessible API keys, credentials, tokens, and SSH keys could potentially be read.
7. Data Exfiltration
Accessible data could potentially be transmitted externally if network access is available.
8. Arbitrary Execution
Bash can invoke Python, scripts, binaries, package managers, and other installed utilities.
9. Blacklists Are Insufficient
Blocking specific dangerous commands cannot reliably secure unrestricted Bash.
10. Actual Security Boundary
The effective boundary is the Android/Termux sandbox, not WRITE_ROOT; unrestricted run_shell should therefore be treated as arbitrary code execution within that boundary.
