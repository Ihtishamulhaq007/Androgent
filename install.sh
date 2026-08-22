#!/data/data/com.termux/files/usr/bin/bash
set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "== 1/5: Installing packages =="
pkg install -y python git

echo ""
echo "== 2/5: Storage access =="
if [ ! -d "$HOME/storage/shared" ]; then
    echo "Requesting storage access (allow the popup)..."
    termux-setup-storage
    sleep 2
fi
if [ ! -d "$HOME/storage/shared" ]; then
    echo "storage/shared still not found. Grant the permission and re-run install.sh."
    exit 1
fi
mkdir -p "$HOME/storage/shared/termux"

echo ""
echo "== 3/5: Git safe.directory =="
git config --global --add safe.directory "$REPO_DIR"

echo ""
echo "== 4/5: Gemini config =="
BASHRC="$HOME/.bashrc"
touch "$BASHRC"

if grep -q "^export GEMINI_API_KEY=" "$BASHRC"; then
    echo "GEMINI_API_KEY already set in ~/.bashrc, leaving it as-is."
else
    echo -n "Paste your Gemini API key (from https://aistudio.google.com/apikey): "
    read -r GEMINI_KEY_INPUT
    echo "export GEMINI_API_KEY=$GEMINI_KEY_INPUT" >> "$BASHRC"
fi

if grep -q "^export GEMINI_MODEL=" "$BASHRC"; then
    echo "GEMINI_MODEL already set in ~/.bashrc, leaving it as-is."
else
    echo -n "Gemini model [default: gemini-2.5-flash-lite]: "
    read -r GEMINI_MODEL_INPUT
    GEMINI_MODEL_INPUT="${GEMINI_MODEL_INPUT:-gemini-2.5-flash-lite}"
    echo "export GEMINI_MODEL=$GEMINI_MODEL_INPUT" >> "$BASHRC"
fi

echo ""
echo "== 5/5: Done =="
echo "Reload your shell config, then run the agent:"
echo ""
echo "  source ~/.bashrc"
echo "  cd $REPO_DIR"
echo "  python3 -m files.main --goal \"your goal here\""
