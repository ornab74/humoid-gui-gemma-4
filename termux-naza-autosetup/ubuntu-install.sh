set -euo pipefail

APP_DIR="$HOME/humoid-gui-gemma-4"
VENV_DIR="$APP_DIR/venv"
REPO_URL="https://github.com/ornab74/humoid-gui-gemma-4.git"

echo "Updating system packages..."
sudo apt update
sudo apt upgrade -y

echo "Installing required packages..."
sudo apt install -y \
    git \
    curl \
    wget \
    nano \
    python3 \
    python3-pip \
    python3-venv \
    python3-tk \
    espeak-ng \
    alsa-utils \
    libespeak1

echo "Cloning or updating Humoid-Gui-Gemma repo..."
mkdir -p "$APP_DIR"

if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" pull --ff-only
else
    git clone "$REPO_URL" "$APP_DIR"
fi

echo "Creating Python virtual environment..."
python3 -m venv "$VENV_DIR"

echo "Activating venv and installing dependencies..."
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip

if [ -f "$APP_DIR/requirements.txt" ]; then
    pip install -r "$APP_DIR/requirements.txt"
elif [ -f "$APP_DIR/requirements.in" ]; then
    pip install -r "$APP_DIR/requirements.in"
fi

chmod +x "$APP_DIR/main.py" 2>/dev/null || true

echo
echo "--------------------------------------------------------------"
echo "Setup complete."
echo
echo "To run it manually:"
echo "cd \"$APP_DIR\""
echo "source venv/bin/activate"
echo "python -u main.py"
echo "--------------------------------------------------------------"
