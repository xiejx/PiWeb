#!/bin/bash
# PiWeb installer — FastAPI + Uvicorn + Nginx (systemd).
# Run as a normal user with sudo rights on Debian / Raspberry Pi OS.
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARNING]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

PIWEB_DIR="${PIWEB_DIR:-/opt/piweb}"
PIWEB_USER="${PIWEB_USER:-piweb}"
PIWEB_REPO="${PIWEB_REPO:-https://github.com/xiejx/PiWeb.git}"
# First-run admin account. The username defaults to "admin" (override with
# PIWEB_ADMIN_USER). The password is prompted for interactively during install;
# if left blank or when running non-interactively, a random one is generated.
PIWEB_ADMIN_USER="${PIWEB_ADMIN_USER:-admin}"
SERVICE=piweb

echo
echo "======================================"
echo "       PiWeb Installer v2.0"
echo "======================================"
echo

if [ "$(id -u)" -eq 0 ]; then
    error "Do not run as root. The script calls sudo where needed."
    exit 1
fi
if ! command -v apt-get >/dev/null 2>&1; then
    error "This installer requires apt-get (Debian/Raspberry Pi OS)."
    exit 1
fi

PI_MODEL=$(cat /proc/device-tree/model 2>/dev/null | tr -d '\0' || echo "Unknown")
info "Detected hardware: $PI_MODEL"
IS_PI5=false
if echo "$PI_MODEL" | grep -q "Raspberry Pi 5"; then
    IS_PI5=true
    info "Raspberry Pi 5 detected — raspi-gpio is used instead of WiringPi."
fi

# ── packages ──────────────────────────────────────────────────────────────────
info "Installing system packages..."
sudo apt-get update -y
sudo apt-get install -y git rsync python3 python3-venv python3-pip nginx openssl

# ── GPIO tooling ──────────────────────────────────────────────────────────────
if [ "$IS_PI5" = false ]; then
    WIRING_DIR="$HOME/wiringPi"
    if [ ! -f /usr/local/bin/gpio ]; then
        info "Installing WiringPi (community fork)..."
        rm -rf "$WIRING_DIR"
        git clone --depth 1 https://github.com/WiringPi/WiringPi.git "$WIRING_DIR"
        (cd "$WIRING_DIR" && ./build)
    else
        info "WiringPi already installed."
    fi
else
    sudo apt-get install -y raspi-gpio || warn "raspi-gpio not available in this repo."
fi

# ── service user ──────────────────────────────────────────────────────────────
if ! id "$PIWEB_USER" >/dev/null 2>&1; then
    info "Creating service user $PIWEB_USER..."
    sudo useradd --system --no-create-home --home-dir "$PIWEB_DIR" --shell /usr/sbin/nologin "$PIWEB_USER"
fi
sudo mkdir -p "$PIWEB_DIR"
sudo chown "$PIWEB_USER:$PIWEB_USER" "$PIWEB_DIR"
for grp in gpio video i2c spi docker adm systemd-journal; do
    if getent group "$grp" >/dev/null 2>&1; then
        sudo usermod -aG "$grp" "$PIWEB_USER" || true
    fi
done
# Group membership (docker socket for the Docker module, adm/systemd-journal for
# reading the systemd journal on the Logs page) only applies to processes started
# afterwards, so the service is (re)started below — re-run
# `sudo systemctl restart piweb` if you add a group to an existing installation.
if getent group docker >/dev/null 2>&1; then
    info "Docker detected — $PIWEB_USER can use the Docker module."
else
    warn "Docker is not installed; the Docker module will report it as unavailable."
fi

# ── source ────────────────────────────────────────────────────────────────────
if [ -f "$(dirname "$0")/app/main.py" ] && [ "$(cd "$(dirname "$0")" && pwd)" != "$PIWEB_DIR" ]; then
    info "Installing from local checkout..."
    sudo mkdir -p "$PIWEB_DIR"
    sudo rsync -a --delete \
        --exclude .git --exclude .venv --exclude config.toml \
        --exclude data --exclude buttons --exclude command_logs --exclude user_scripts \
        --exclude __pycache__ --exclude .frontend-build/node_modules \
        "$(dirname "$0")/" "$PIWEB_DIR/"
elif [ ! -d "$PIWEB_DIR/.git" ]; then
    info "Cloning PiWeb into $PIWEB_DIR..."
    sudo git clone "$PIWEB_REPO" "$PIWEB_DIR"
else
    info "Existing git checkout found — source code is left untouched."
    info "To update the code use the /update page, or:"
    info "  sudo -u $PIWEB_USER git -C $PIWEB_DIR pull --ff-only"
    info "(or re-run this script from a newer local checkout)."
fi

sudo mkdir -p "$PIWEB_DIR/data" "$PIWEB_DIR/buttons" \
    "$PIWEB_DIR/command_logs" "$PIWEB_DIR/user_scripts"
sudo chown -R "$PIWEB_USER:$PIWEB_USER" "$PIWEB_DIR"

# ── virtualenv ────────────────────────────────────────────────────────────────
info "Creating virtualenv and installing Python dependencies..."
sudo -u "$PIWEB_USER" python3 -m venv "$PIWEB_DIR/.venv"
sudo -u "$PIWEB_USER" "$PIWEB_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$PIWEB_USER" "$PIWEB_DIR/.venv/bin/pip" install -r "$PIWEB_DIR/requirements.txt"

# ── configuration ─────────────────────────────────────────────────────────────
GENERATED_PASSWORD=""
CREDS_WRITTEN=0
ADMIN_PASSWORD=""
if [ ! -f "$PIWEB_DIR/config.toml" ]; then
    # Ask for the password on a real terminal. Leaving it blank generates a
    # random one; without a TTY (piped/CI) we always generate to avoid hanging.
    if [ -t 0 ]; then
        echo
        info "Set the password for admin user '$PIWEB_ADMIN_USER'."
        info "At least 8 characters; leave blank to generate a random one."
        while true; do
            read -r -s -p "  Password: " ADMIN_PASSWORD; echo
            if [ -z "$ADMIN_PASSWORD" ]; then
                break
            fi
            if [ "${#ADMIN_PASSWORD}" -lt 8 ]; then
                warn "Password must be at least 8 characters — try again."
                continue
            fi
            case "${ADMIN_PASSWORD,,}" in
                raspberry|password|secret|piweb|admin)
                    warn "That is a well-known default password — choose another."
                    continue
                    ;;
            esac
            read -r -s -p "  Repeat password: " ADMIN_PASSWORD2; echo
            if [ "$ADMIN_PASSWORD" != "$ADMIN_PASSWORD2" ]; then
                warn "Passwords did not match — try again."
                continue
            fi
            break
        done
        unset ADMIN_PASSWORD2
    fi

    sudo cp "$PIWEB_DIR/config.example.toml" "$PIWEB_DIR/config.toml"

    if [ -n "$ADMIN_PASSWORD" ]; then
        info "Creating config.toml with the password you entered..."
    else
        info "Creating config.toml with a random admin password..."
        GENERATED_PASSWORD=$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | cut -c1-16)
        ADMIN_PASSWORD="$GENERATED_PASSWORD"
    fi

    # Validate the username, hash the password and write it back. The helper
    # script lives in a file so stdin carries only the password — never argv
    # (visible in ps) and never a heredoc (which would consume stdin).
    SETUP_SCRIPT="$PIWEB_DIR/.setup_credentials.py"
    cat > "$SETUP_SCRIPT" <<'PY'
import re, sys, pathlib
root = pathlib.Path(sys.argv[1])
user = sys.argv[2]
password = sys.stdin.read()
sys.path.insert(0, str(root))
from app.config import save_auth_credentials
from app.security import hash_password, password_policy_errors

if not re.fullmatch(r"[A-Za-z0-9._@-]{1,50}", user):
    raise SystemExit("PIWEB_ADMIN_USER must be 1-50 chars: letters, digits, . _ @ -")
problems = password_policy_errors(password)
if problems:
    raise SystemExit("Invalid admin password: " + " ".join(problems))

save_auth_credentials(root / "config.toml", user, hash_password(password))
PY
    sudo chown "$PIWEB_USER:$PIWEB_USER" "$SETUP_SCRIPT"
    if ! printf '%s' "$ADMIN_PASSWORD" | sudo -u "$PIWEB_USER" \
        "$PIWEB_DIR/.venv/bin/python" "$SETUP_SCRIPT" "$PIWEB_DIR" "$PIWEB_ADMIN_USER"; then
        rm -f "$SETUP_SCRIPT"
        error "Could not write admin credentials to config.toml — installation aborted."
        exit 1
    fi
    rm -f "$SETUP_SCRIPT"

    sudo chown "$PIWEB_USER:$PIWEB_USER" "$PIWEB_DIR/config.toml"
    sudo chmod 600 "$PIWEB_DIR/config.toml"

    # Verify the written hash is actually recognised as a configured login.
    if ! sudo -u "$PIWEB_USER" "$PIWEB_DIR/.venv/bin/python" - "$PIWEB_DIR" <<'PY'
import sys, pathlib
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root))
from app.config import load_settings
from app.security import auth_configured
if not auth_configured(load_settings(root / "config.toml")):
    raise SystemExit("credentials written to config.toml did not verify")
PY
    then
        error "Credential self-check failed — installation aborted."
        exit 1
    fi
    CREDS_WRITTEN=1
else
    info "config.toml already exists — keeping your settings."
fi

# ── sudoers ───────────────────────────────────────────────────────────────────
info "Installing sudoers drop-in..."
sed "s/\bpiweb\b/$PIWEB_USER/g" "$PIWEB_DIR/deploy/piweb-sudoers" \
    | sudo tee /tmp/piweb-sudoers >/dev/null
sudo visudo -cf /tmp/piweb-sudoers >/dev/null
sudo install -m 0440 /tmp/piweb-sudoers /etc/sudoers.d/piweb
sudo rm -f /tmp/piweb-sudoers

# ── systemd ───────────────────────────────────────────────────────────────────
info "Installing systemd service..."
sed -e "s|@PIWEB_DIR@|$PIWEB_DIR|g" \
    -e "s/^User=piweb$/User=$PIWEB_USER/" \
    -e "s/^Group=piweb$/Group=$PIWEB_USER/" \
    "$PIWEB_DIR/deploy/piweb.service" \
    | sudo tee /etc/systemd/system/$SERVICE.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE"
sudo systemctl restart "$SERVICE"

# ── nginx ─────────────────────────────────────────────────────────────────────
info "Installing Nginx site..."
sed "s|@PIWEB_DIR@|$PIWEB_DIR|g" "$PIWEB_DIR/deploy/piweb-nginx.conf" \
    | sudo tee /etc/nginx/sites-available/piweb >/dev/null
sudo ln -sf /etc/nginx/sites-available/piweb /etc/nginx/sites-enabled/piweb
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl restart nginx

# ── done ──────────────────────────────────────────────────────────────────────
IP=$(hostname -I | awk '{print $1}')
echo
echo "======================================"
echo "   Installation complete"
echo "======================================"
echo
info "Open:  http://${IP}/"
if [ "$CREDS_WRITTEN" -eq 1 ]; then
    echo "  Username: $PIWEB_ADMIN_USER"
    if [ -n "$GENERATED_PASSWORD" ]; then
        echo "  Password: $GENERATED_PASSWORD"
        warn "Store this password now — it is not shown again (it is stored hashed)."
    else
        echo "  Password: (the value you entered during installation)"
    fi
else
    echo "  Credentials: unchanged from your existing config.toml"
fi
echo
echo "Logs:      sudo journalctl -u $SERVICE -f"
echo "Restart:   sudo systemctl restart $SERVICE"
echo "Config:    sudoedit $PIWEB_DIR/config.toml"
echo
warn "HTTPS is not configured. Run 'sudo certbot --nginx' and set"
warn "session.cookie_secure = true in config.toml before exposing this panel."
echo
