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
# systemd reads its own copy of the settings from /etc: under SELinux it may not read files in a home directory.
SERVICE_ENV="/etc/$SERVICE.env"

say() { printf '\n== %s\n' "$*"; }

say "Installing system packages"
if command -v apt-get >/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq git python3 python3-venv python3-pip curl xz-utils unzip >/dev/null
    PY=python3
elif command -v dnf >/dev/null; then
    sudo dnf install -y -q git python3.11 python3.11-pip policycoreutils-python-utils curl xz unzip >/dev/null
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

say "Installing the service's copy of the settings ($SERVICE_ENV)"
sudo install -m 600 -o root -g root .env "$SERVICE_ENV"
command -v restorecon >/dev/null && sudo restorecon "$SERVICE_ENV" || true

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
EnvironmentFile=$SERVICE_ENV
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

# --- Morning TV shortlist: only when a Groq key is configured ------------------------------------------------------
TV_STATUS="off (add GROQ_API_KEY to $DIR/.env and run this installer again to enable it)"
if grep -q '^GROQ_API_KEY=.' .env; then
    say "Setting up the morning TV shortlist (weekdays 08:29 IST)"
    .venv/bin/pip install -q -e ".[tv]"
    mkdir -p bin
    case "$(uname -m)" in
        x86_64) FF_ARCH=linux64; DENO_ARCH=x86_64 ;;
        aarch64|arm64) FF_ARCH=linuxarm64; DENO_ARCH=aarch64 ;;
        *) echo "Unsupported CPU $(uname -m) for the TV shortlist" >&2; exit 1 ;;
    esac
    if ! command -v ffmpeg >/dev/null && [ ! -x bin/ffmpeg ]; then
        say "Downloading a static ffmpeg build into $DIR/bin"
        tmp=$(mktemp -d)
        curl -fsSL "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-${FF_ARCH}-gpl.tar.xz" \
            -o "$tmp/ff.tar.xz"
        tar -xJf "$tmp/ff.tar.xz" -C "$tmp"
        cp "$tmp/ffmpeg-master-latest-${FF_ARCH}-gpl/bin/ffmpeg" bin/ffmpeg
        rm -rf "$tmp"
    fi
    # yt-dlp needs a JavaScript runtime for YouTube.
    if ! command -v deno >/dev/null && ! command -v node >/dev/null && [ ! -x bin/deno ]; then
        say "Downloading deno (JavaScript runtime used by yt-dlp for YouTube) into $DIR/bin"
        tmp=$(mktemp -d)
        curl -fsSL "https://github.com/denoland/deno/releases/latest/download/deno-${DENO_ARCH}-unknown-linux-gnu.zip" \
            -o "$tmp/deno.zip"
        unzip -q -o "$tmp/deno.zip" -d bin
        rm -rf "$tmp"
    fi
    if [ -n "$SELINUX_PRE" ]; then
        sudo semanage fcontext -a -t bin_t "$DIR/bin(/.*)?" 2>/dev/null \
            || sudo semanage fcontext -m -t bin_t "$DIR/bin(/.*)?"
        sudo restorecon -R "$DIR/bin"
    fi
    sudo tee /etc/systemd/system/tv-shortlist.service >/dev/null <<UNIT
[Unit]
Description=Stock Finder morning TV shortlist
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USER
WorkingDirectory=$DIR
EnvironmentFile=$SERVICE_ENV
Environment=PATH=$DIR/bin:$DIR/.venv/bin:/usr/local/bin:/usr/bin:/bin
${SELINUX_PRE:+ExecStartPre=-+/usr/sbin/restorecon -R $DIR/.venv/bin $DIR/bin}
ExecStart=$DIR/.venv/bin/python -m stock_finder tv
TimeoutStartSec=2h
Nice=5
UNIT
    sudo cp deploy/tv-shortlist.timer /etc/systemd/system/tv-shortlist.timer
    sudo systemctl daemon-reload
    sudo systemctl enable --now tv-shortlist.timer >/dev/null
    TV_STATUS="on: next run $(systemctl show tv-shortlist.timer -p NextElapseUSecRealtime --value)"
fi

if systemctl is-active --quiet "$SERVICE"; then
    say "Running. It starts on boot and restarts itself if it stops."
    echo "Logs:    journalctl -u $SERVICE -n 50"
    echo "Restart: sudo systemctl restart $SERVICE"
    echo "Update:  bash $DIR/deploy/install.sh   (also re-run after editing $DIR/.env)"
    echo "TV shortlist: $TV_STATUS"
else
    say "The service did not start. Last log lines:"
    sudo journalctl -u "$SERVICE" -n 30 --no-pager | grep -v "TELEGRAM_BOT_TOKEN" || true
    exit 1
fi
