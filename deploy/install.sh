#!/usr/bin/env bash
# One-command setup of the Stock Finder bot on an always-on server: Ubuntu/Debian (apt) or Oracle Linux/RHEL (dnf).
#
#   curl -fsSL https://raw.githubusercontent.com/unloan83/Stock_picker/main/deploy/install.sh | bash
#   curl -fsSL .../install.sh | ENV_SRC=~/stock_finder.env bash     # use an existing .env copied to the server
#
# Installs Python and git, clones (or updates) the repo, creates .env (from ENV_SRC, or asks for the bot token and
# chat ids without echoing them), and registers a systemd service that starts on boot and restarts within 10 s if it
# stops. Safe to run again: it updates the code and restarts the service, keeping .env and the recommendation log.
set -euo pipefail

REPO_URL="https://github.com/unloan83/Stock_picker.git"
DIR="${STOCK_FINDER_DIR:-$HOME/stock_picker}"
SERVICE=stock-finder

say() { printf '\n== %s\n' "$*"; }

say "Installing system packages"
if command -v apt-get >/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq git python3 python3-venv python3-pip >/dev/null
    PY=python3
elif command -v dnf >/dev/null; then
    sudo dnf install -y -q git python3.11 python3.11-pip policycoreutils-python-utils >/dev/null
    PY=python3.11
else
    echo "Unsupported system: needs apt (Ubuntu/Debian) or dnf (Oracle Linux/RHEL)." >&2
    exit 1
fi
"$PY" - <<'PY' || { echo "Python 3.10+ is required." >&2; exit 1; }
import sys
sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY

say "Getting the code into $DIR"
if [ -d "$DIR/.git" ]; then
    git -C "$DIR" pull --ff-only
else
    git clone -q "$REPO_URL" "$DIR"
fi
cd "$DIR"

say "Installing Python dependencies"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e .

if [ ! -f .env ] && [ -n "${ENV_SRC:-}" ]; then
    say "Using settings from $ENV_SRC"
    install -m 600 "$ENV_SRC" .env
    rm -f "$ENV_SRC"
fi
if [ ! -f .env ]; then
    say "Creating .env (input is hidden)"
    cp .env.example .env
    chmod 600 .env
    read -rsp "Telegram bot token from @BotFather: " token </dev/tty; echo
    read -rp "Allowed Telegram chat id(s), comma-separated (leave empty to find it later): " chats </dev/tty
    sed -i "s|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=${token}|; s|^ALLOWED_CHAT_IDS=.*|ALLOWED_CHAT_IDS=${chats}|" .env
    unset token
else
    say "Keeping existing .env"
fi

SELINUX_PRE=""
if command -v getenforce >/dev/null && [ "$(getenforce)" = "Enforcing" ]; then
    say "Labelling the venv so systemd may run it under SELinux"
    sudo semanage fcontext -a -t bin_t "$DIR/.venv/bin(/.*)?" 2>/dev/null \
        || sudo semanage fcontext -m -t bin_t "$DIR/.venv/bin(/.*)?"
    sudo restorecon -R "$DIR/.venv/bin"
    SELINUX_PRE="ExecStartPre=-+/usr/sbin/restorecon -R $DIR/.venv/bin"
fi

say "Registering the $SERVICE service"
sudo tee "/etc/systemd/system/$SERVICE.service" >/dev/null <<UNIT
[Unit]
Description=Stock Finder Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$DIR
EnvironmentFile=$DIR/.env
$SELINUX_PRE
ExecStart=$DIR/.venv/bin/python -m stock_finder bot
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE" >/dev/null
sudo systemctl restart "$SERVICE"
sleep 8

if systemctl is-active --quiet "$SERVICE"; then
    say "Running. It starts on boot and restarts itself if it stops."
    echo "Logs:    journalctl -u $SERVICE -n 50"
    echo "Restart: sudo systemctl restart $SERVICE"
    echo "Update:  bash $DIR/deploy/install.sh"
else
    say "The service did not start. Last log lines:"
    sudo journalctl -u "$SERVICE" -n 30 --no-pager | grep -v "TELEGRAM_BOT_TOKEN" || true
    exit 1
fi
