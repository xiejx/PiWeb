#!/bin/bash
# PiWeb uninstaller — stops and removes the systemd service, Nginx site,
# sudoers drop-in and (optionally) the application directory.
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARNING]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

PIWEB_DIR="${PIWEB_DIR:-/opt/piweb}"
PIWEB_USER="${PIWEB_USER:-piweb}"
SERVICE=piweb

echo
echo "======================================"
echo "       PiWeb Uninstaller"
echo "======================================"
echo

if [ "$(id -u)" -eq 0 ]; then
    error "Do not run as root. The script calls sudo where needed."
    exit 1
fi

echo "This will remove:"
echo "  - systemd service   /etc/systemd/system/$SERVICE.service"
echo "  - Nginx site        /etc/nginx/sites-available/piweb"
echo "  - sudoers drop-in   /etc/sudoers.d/piweb"
echo "  - cron jobs         $PIWEB_USER's crontab (asks first)"
echo "  - application files $PIWEB_DIR"
echo
echo "This will NOT remove Nginx itself, Git, Python or WiringPi."
echo

read -r -p "Are you sure you want to uninstall PiWeb? [y/N] " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { info "Uninstall cancelled."; exit 0; }

KEEP_DATA=true
read -r -p "Keep config.toml, data, buttons, command_logs and user_scripts? [Y/n] " keep
[[ "$keep" =~ ^[Nn]$ ]] && KEEP_DATA=false

BACKUP_DIR=""
if [ "$KEEP_DATA" = true ] && [ -d "$PIWEB_DIR" ]; then
    BACKUP_DIR="$HOME/piweb_backup_$(date +%Y%m%d_%H%M%S)"
    info "Backing up user data to $BACKUP_DIR ..."
    mkdir -p "$BACKUP_DIR"
    for item in config.toml data buttons command_logs user_scripts; do
        [ -e "$PIWEB_DIR/$item" ] && sudo cp -a "$PIWEB_DIR/$item" "$BACKUP_DIR/"
    done
    sudo chown -R "$(id -u):$(id -g)" "$BACKUP_DIR"
fi

info "Stopping and removing the service..."
sudo systemctl disable --now "$SERVICE" >/dev/null 2>&1 || true
sudo rm -f "/etc/systemd/system/$SERVICE.service"
sudo systemctl daemon-reload

info "Removing the Nginx site..."
sudo rm -f /etc/nginx/sites-enabled/piweb /etc/nginx/sites-available/piweb

# installer.sh removes the distribution default site, which would leave port 80
# without any server block. Put it back when PiWeb was the only enabled site.
if [ -f /etc/nginx/sites-available/default ] \
    && [ ! -e /etc/nginx/sites-enabled/default ] \
    && [ -d /etc/nginx/sites-enabled ] \
    && [ -z "$(find /etc/nginx/sites-enabled -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
    sudo ln -sf ../sites-available/default /etc/nginx/sites-enabled/default
    info "Restored the default Nginx site (it was disabled by installer.sh)."
fi
sudo nginx -t >/dev/null 2>&1 && sudo systemctl reload nginx || true

info "Removing the sudoers drop-in..."
sudo rm -f /etc/sudoers.d/piweb

# Scheduled jobs added from the panel live in the service user's crontab.
if id "$PIWEB_USER" >/dev/null 2>&1; then
    cron_list=$(sudo crontab -u "$PIWEB_USER" -l 2>/dev/null || true)
    cron_count=$(printf '%s\n' "$cron_list" | grep -Ev '^[[:space:]]*(#|$)' | wc -l || true)
    if [ "${cron_count:-0}" -gt 0 ]; then
        read -r -p "Remove $cron_count scheduled job(s) from $PIWEB_USER's crontab? [Y/n] " dropcron
        if [[ ! "$dropcron" =~ ^[Nn]$ ]]; then
            sudo crontab -u "$PIWEB_USER" -r 2>/dev/null || true
            info "Removed the $PIWEB_USER crontab."
        else
            warn "Kept the $PIWEB_USER crontab — those jobs keep running outside PiWeb."
            warn "Clear it later with: sudo crontab -u $PIWEB_USER -r"
        fi
    fi
fi

if [ "$KEEP_DATA" = true ]; then
    warn "Application files kept in $PIWEB_DIR (only the service config was removed)."
else
    info "Removing $PIWEB_DIR ..."
    sudo rm -rf "$PIWEB_DIR"
    if id "$PIWEB_USER" >/dev/null 2>&1; then
        read -r -p "Also remove the system user $PIWEB_USER? [y/N] " dropuser
        if [[ "$dropuser" =~ ^[Yy]$ ]]; then
            sudo userdel "$PIWEB_USER" 2>/dev/null || true
        fi
    fi
fi

echo
info "Uninstall complete."
[ -n "$BACKUP_DIR" ] && info "Your data is saved at: $BACKUP_DIR"
echo
