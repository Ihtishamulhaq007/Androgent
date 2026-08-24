#!/data/data/com.termux/files/usr/bin/bash
set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "== 1/5: Installing packages =="
pkg install -y python git curl

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

# --- API key ---
if grep -q "^export GEMINI_API_KEY=" "$BASHRC"; then
    GEMINI_API_KEY="$(grep "^export GEMINI_API_KEY=" "$BASHRC" | tail -1 | cut -d= -f2-)"
    echo "Using existing GEMINI_API_KEY from ~/.bashrc."
else
    echo -n "Paste your Gemini API key (from https://aistudio.google.com/apikey): "
    read -r GEMINI_API_KEY
    echo "export GEMINI_API_KEY=$GEMINI_API_KEY" >> "$BASHRC"
fi

# --- Model selection ---
if grep -q "^export GEMINI_MODEL=" "$BASHRC"; then
    echo "GEMINI_MODEL already set in ~/.bashrc, leaving it as-is."
else
    echo "Fetching available Gemini models..."
    MODEL_JSON="$(curl -s "https://generativelanguage.googleapis.com/v1beta/models?key=${GEMINI_API_KEY}")"
    MODEL_LIST="$(echo "$MODEL_JSON" | grep -o '"name": *"models/[a-zA-Z0-9.\-]*"' | sed -E 's/.*models\///; s/"//')"
    MODEL_LIST="$(echo "$MODEL_LIST" | grep -E 'flash|pro' | grep -vE 'embedding|aqa|image|imagen|video|veo|tts|vision|live|robotics' | sort -u)"

    if [ -z "$MODEL_LIST" ]; then
        echo "Couldn't fetch the model list (check your key/network). Using a safe default."
        GEMINI_MODEL="gemini-3.5-flash-lite"
    else
        echo ""
        echo "Available models:"
        i=1
        declare -A MODEL_MAP
        DEFAULT_NUM=1
        while IFS= read -r m; do
            MODEL_MAP[$i]="$m"
            if [ "$m" = "gemini-3.5-flash-lite" ]; then
                echo "  $i) $m   <-- recommended: fast and reliable"
                DEFAULT_NUM=$i
            else
                echo "  $i) $m"
            fi
            i=$((i+1))
        done <<< "$MODEL_LIST"
        echo ""
        echo -n "Pick a number [default: $DEFAULT_NUM]: "
        read -r CHOICE
        CHOICE="${CHOICE:-$DEFAULT_NUM}"
        GEMINI_MODEL="${MODEL_MAP[$CHOICE]:-gemini-3.5-flash-lite}"
    fi

    echo "export GEMINI_MODEL=$GEMINI_MODEL" >> "$BASHRC"
    echo "Selected: $GEMINI_MODEL"
fi

echo ""
echo "== 5/5: Done =="
echo "Reload your shell config, then run the agent:"
echo ""
echo "  source ~/.bashrc"
echo "  cd $REPO_DIR"
echo "  python3 -m files.main --goal \"your goal here\""

source ~/.bashrc
