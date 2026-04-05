set -e

echo "Updating Termux packages..."
pkg update -y && pkg upgrade -y
pkg install -y bash bzip2 coreutils curl file findutils gawk gzip ncurses-utils proot sed tar util-linux xz-utils git wget

echo "Removing any old proot-distro..."
proot-distro remove ubuntu 2>/dev/null || true
rm -rf "$HOME/proot-distro" 2>/dev/null || true

echo "Cloning OLD working proot-distro commit (ca53fee – full TTY support)..."
cd "$HOME"
git clone https://github.com/termux/proot-distro.git
cd proot-distro
git checkout ca53fee288be8f46ee0e4fc8ee23934023472054

echo "Installing proot-distro from this commit..."
chmod +x install.sh
./install.sh

echo "Installing Ubuntu (24.04 rootfs)..."
proot-distro install ubuntu

echo "Creating TMP dir..."
export PROOT_TMP_DIR="$HOME/tmp"
mkdir -p "$PROOT_TMP_DIR"

echo "Setting up sudouser + Python + Humoid-Gui-Gemma repo..."
proot-distro login ubuntu -- <<'EOF'
apt update && apt upgrade -y
apt install -y sudo python3 python3-pip python3-venv git nano curl

# Create sudouser (no password)
adduser --disabled-password --gecos "" sudouser
usermod -aG sudo sudouser
echo "sudouser ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# Clone repo
su - sudouser -c "
    mkdir -p ~/humoid-gui-gemma-4 && cd ~/humoid-gui-gemma-4
    git clone https://github.com/ornab74/humoid-gui-gemma-4.git . || git pull
    python3 -m venv venv
    . venv/bin/activate
    pip install --upgrade pip
    [ -f requirements.txt ] && pip install -r requirements.txt || true
    chmod +x main.py
"

echo "Setup complete inside Ubuntu"
EOF

cat > ~/.bashrc <<'BASHRC'
# === AUTO-START HUMOID-GUI-GEMMA IN UBUNTU PROOT ===
if [ -z "$HUMOID_GUI_GEMMA_STARTED" ] && [ "$PWD" = "$HOME" ] && [ -z "$SSH_CLIENT" ] && [ -z "$TMUX" ]; then
    export HUMOID_GUI_GEMMA_STARTED=1

    echo ""
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║      Starting Humoid-Gui-Gemma (humoid-gui-gemma-4/main.py)    ║"
    echo "║      Ubuntu proot → /home/sudouser/humoid-gui-gemma-4          ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo "   Type 'exit' twice to return to Termux"
    echo ""

    proot-distro login ubuntu --user sudouser --shared-tmp -- bash -c "
        cd /home/sudouser/humoid-gui-gemma-4 || exit 1
        . venv/bin/activate || exit 1
        export TERM=xterm-256color
        export LANG=C.UTF-8
        export PYTHONUNBUFFERED=1
        clear
        echo 'Starting main.py in venv...'
        exec python -u main.py
    "

    clear
    echo "Returned to Termux."
fi
BASHRC

echo "alias humoid-gui-gemma='proot-distro login ubuntu --user sudouser -- bash -c \"cd ~/humoid-gui-gemma-4 && . venv/bin/activate && python -u main.py\"'" >> ~/.bashrc

echo "--------------------------------------------------------------"
echo "ALL DONE!"
echo "Close and reopen Termux (or run: bash)"
echo "Humoid-Gui-Gemma will now auto-start with full colors & interactivity"
echo "--------------------------------------------------------------"