# agent

Autonomous coding agent that runs on Android via Termux, backed by the Gemini API.
No third-party Python dependencies — stdlib only.

## Setup

```bash
pkg install -y python git
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO
bash install.sh
```

Get a free Gemini API key at https://aistudio.google.com/apikey, then:

```bash
export GEMINI_API_KEY=your_key_here
export GEMINI_MODEL=gemini-2.5-flash-lite
```

Add those two lines to `~/.bashrc` so you don't retype them every session.

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
