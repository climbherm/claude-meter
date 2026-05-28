#!/usr/bin/env bash
#
# End-to-end installer for claude-meter.
#
# Wipes any previous install, creates a clean venv outside macOS
# TCC-protected folders (~/Documents, ~/Desktop, ~/Downloads), installs
# the package, prompts for the clock's IP, and starts the background
# service.
#
# Usage:
#   ./install.sh                    # interactive — prompts for clock IP
#   ./install.sh 192.168.1.50       # non-interactive
#
# Env overrides:
#   CLAUDE_METER_VENV=~/.venvs/claude-meter
#   PYTHON=python3.12
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${CLAUDE_METER_VENV:-$HOME/.venvs/claude-meter}"
BIN_DIR="$HOME/.local/bin"
PYTHON="${PYTHON:-python3}"
DEVICE_HOST_ARG="${1:-}"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!>\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31mxx>\033[0m %s\n' "$*" >&2; exit 1; }

command -v "$PYTHON" >/dev/null 2>&1 || fail "$PYTHON not found. Install Python 3.9+."

case "$VENV_DIR" in
  "$HOME/Documents"/*|"$HOME/Desktop"/*|"$HOME/Downloads"/*)
    fail "VENV_DIR ($VENV_DIR) is under a macOS TCC-protected folder. Pick another location."
    ;;
esac

# 1. Tear down any previous install.
info "Removing previous install artifacts..."
if command -v claude-meter >/dev/null 2>&1; then
  claude-meter uninstall-service >/dev/null 2>&1 || true
fi
rm -rf "$REPO_DIR/.venv"
rm -f  "$BIN_DIR/claude-meter"
rm -rf "$VENV_DIR"

# 2. Fresh venv + install.
info "Creating venv at $VENV_DIR"
"$PYTHON" -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip >/dev/null

info "Installing claude-meter from $REPO_DIR"
"$VENV_DIR/bin/pip" install "$REPO_DIR"

# 3. Symlink into ~/.local/bin and ensure PATH.
mkdir -p "$BIN_DIR"
ln -sf "$VENV_DIR/bin/claude-meter" "$BIN_DIR/claude-meter"

PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
ZRC="$HOME/.zshrc"
if [[ -f "$ZRC" ]] && ! grep -Fq "$PATH_LINE" "$ZRC"; then
  printf '\n# added by claude-meter installer\n%s\n' "$PATH_LINE" >> "$ZRC"
  info "Added $BIN_DIR to PATH in $ZRC (open a new terminal to pick it up)"
fi
export PATH="$BIN_DIR:$PATH"

# 4. Sanity-check the resolved binary.
RESOLVED="$(command -v claude-meter || true)"
[[ -n "$RESOLVED" ]] || fail "claude-meter not on PATH after install."
case "$RESOLVED" in
  "$HOME/Documents"/*|"$HOME/Desktop"/*|"$HOME/Downloads"/*)
    fail "claude-meter resolves to a TCC-protected path: $RESOLVED"
    ;;
esac
info "Using $RESOLVED"

# 5. Get the clock's IP — arg, env, existing config, or interactive prompt.
DEVICE_HOST="$DEVICE_HOST_ARG"
EXISTING="$(claude-meter show 2>/dev/null | awk -F'"' '/"host"/ {print $4; exit}')"

if [[ -z "$DEVICE_HOST" ]]; then
  prompt="Enter the clock's IP address"
  [[ -n "$EXISTING" ]] && prompt="$prompt [$EXISTING]"
  prompt="$prompt: "
  while [[ -z "$DEVICE_HOST" ]]; do
    read -r -p "$prompt" REPLY </dev/tty
    DEVICE_HOST="${REPLY:-$EXISTING}"
    [[ -z "$DEVICE_HOST" ]] && warn "IP is required. Try again."
  done
fi

info "Configuring device_host=$DEVICE_HOST"
claude-meter configure --device-host "$DEVICE_HOST" >/dev/null

# Non-fatal reachability hint.
if ping -c 1 -W 1000 "$DEVICE_HOST" >/dev/null 2>&1 \
   || ping -c 1 -t 2 "$DEVICE_HOST" >/dev/null 2>&1; then
  info "Clock responded to ping."
else
  warn "Clock $DEVICE_HOST did not respond to ping. Continuing — the service will retry."
fi

# 6. Verify auth + API (non-fatal; failure here means Claude Code isn't signed in).
info "Running claude-meter check..."
claude-meter check || warn "check failed — sign in with 'claude' and re-run if auth is the issue."

# 7. Install and start the service.
info "Installing background service..."
claude-meter install-service
info "Service status:"
claude-meter service-status || true

cat <<EOF

Done.
  Logs:  ~/Library/Logs/claude-meter/claude-meter.out.log
  Tail:  tail -f ~/Library/Logs/claude-meter/claude-meter.out.log

Re-run this script after pulling new code, or when you change the clock's IP.
EOF
