#!/usr/bin/env bash
# Usage: curl -fsSL https://raw.githubusercontent.com/ctrlpi/pico-gpio-api/main/install.sh | bash
#
# Downloads the release files (no git required), then deploys main.py, config.json,
# wifi.txt and a seeded scripts/cpu-temp.py to every Pico on USB via mpremote.
# Runs on your Mac/PC - MicroPython has no shell to run this on the device itself.
# Downloads only when piped into a shell or run with --upgrade; a plain ./install.sh
# just redeploys what is already on disk. Prompts only for what the device lacks.
#
#   ./install.sh              # full deploy: main.py + config.json + wifi.txt + scripts/cpu-temp.py
#   ./install.sh --config     # re-ask for API key/agent name even if the device already has config.json
#   ./install.sh --wifi       # re-ask for WiFi SSID/password even if the device already has wifi.txt
set -euo pipefail

BASE_URL="https://raw.githubusercontent.com/ctrlpi/pico-gpio-api/main"



FILES="main.py pico.sh install.sh LICENSE README.md"
DIR="pico-gpio-api"
SHELLS=(bash sh dash zsh ksh ash)

RUN_AS="$(basename "$0")"
DOWNLOAD=false
for s in "${SHELLS[@]}"; do
  [ "$RUN_AS" = "$s" ] && DOWNLOAD=true
done
FORCE_WIFI=false
FORCE_CONFIG=false
for arg in "$@"; do
  [ "$arg" = "--upgrade" ] && DOWNLOAD=true
  [ "$arg" = "--wifi" ] && FORCE_WIFI=true
  [ "$arg" = "--config" ] && FORCE_CONFIG=true
done

if [ "$DOWNLOAD" = true ] && ! command -v curl >/dev/null 2>&1; then
  echo "Error: curl is required. Install it first (e.g. brew install curl)." >&2
  exit 1
fi

# Update in place if already inside the project folder.
if [ "$(basename "$PWD")" = "$DIR" ] || { [ -f main.py ] && [ -f install.sh ]; }; then
  DIR="."
else
  mkdir -p "$DIR"
fi

if [ "$DOWNLOAD" = true ]; then
  for f in $FILES; do
    echo "Downloading $f..."
    curl -fsSL "$BASE_URL/$f" -o "$DIR/$f.tmp"
    mv "$DIR/$f.tmp" "$DIR/$f"   # only replace the old copy once the download completed
  done
  chmod +x "$DIR/install.sh" "$DIR/pico.sh"
fi

# --- venv + mpremote ---
VENV_DIR="$DIR/venv"
# Recreate the venv if missing or broken.
if [ ! -x "$VENV_DIR/bin/python" ] || ! "$VENV_DIR/bin/python" -c '' 2>/dev/null; then
  [ -d "$VENV_DIR" ] && echo "Rebuilding venv and installing mpremote..." || echo "Creating venv and installing mpremote..."
  rm -rf "$VENV_DIR"
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/pip" install --quiet --upgrade pip mpremote
fi
MPREMOTE="$VENV_DIR/bin/mpremote"

ask() {
  # Prompt for input with default fallback.
  local ans=""
  if { : < /dev/tty; } 2>/dev/null; then
    read -r -p "$1 [$2]: " ans < /dev/tty || ans=""
  fi
  printf '%s' "${ans:-$2}"
}

ask_optional() {
  # Prompt for optional input.
  local ans=""
  if { : < /dev/tty; } 2>/dev/null; then
    read -r -p "$1: " ans < /dev/tty || ans=""
  fi
  printf '%s' "$ans"
}

ask_required() {
  # Prompt for required input.
  local label="$1" ans=""
  if ! { : < /dev/tty; } 2>/dev/null; then
    echo "ERROR: no terminal available to ask for $label, and the device has no wifi.txt." >&2
    echo "Create $DIR/wifi.txt by hand (see wifi.txt.example), push it with" >&2
    echo "./pico.sh put wifi.txt, then re-run." >&2
    exit 1
  fi
  while [ -z "$ans" ]; do
    read -r -p "$label: " ans < /dev/tty || ans=""
  done
  printf '%s' "$ans"
}

# Find connected Picos.
DEVICES=(/dev/cu.usbmodem*)
if [ ! -e "${DEVICES[0]}" ]; then
  echo "ERROR: no Pico found (no /dev/cu.usbmodem* device)." >&2
  exit 1
fi

# Check and prompt for config.json if missing from device.
CONFIG_FILE="$DIR/config.json"
NEED_CONFIG=false
if [ "$FORCE_CONFIG" = true ]; then
  NEED_CONFIG=true
elif ! "$MPREMOTE" connect "${DEVICES[0]}" fs cat :config.json >/dev/null 2>&1; then
  NEED_CONFIG=true
fi
if [ "$NEED_CONFIG" = true ]; then
  echo "Agent config not found on the device."
  API_KEY="$(ask "API key" "your-secret-key")"
  AGENT_NAME="$(ask_optional "Agent name [blank = device default, pico-<serial4>]")"
  if [ -n "$AGENT_NAME" ]; then
    printf '{\n  "api_key": "%s",\n  "name": "%s"\n}\n' "$API_KEY" "$AGENT_NAME" > "$CONFIG_FILE"
  else
    printf '{\n  "api_key": "%s"\n}\n' "$API_KEY" > "$CONFIG_FILE"
  fi
  echo "Saved $CONFIG_FILE"
fi

# Check and prompt for wifi.txt if missing from device.
WIFI_FILE="$DIR/wifi.txt"
NEED_WIFI=false
if [ "$FORCE_WIFI" = true ]; then
  NEED_WIFI=true
elif ! "$MPREMOTE" connect "${DEVICES[0]}" fs cat :wifi.txt >/dev/null 2>&1; then
  NEED_WIFI=true
fi
if [ "$NEED_WIFI" = true ]; then
  echo "WiFi credentials not found on the device."
  SSID="$(ask_required "WiFi SSID")"
  PASSWORD="$(ask_required "WiFi password")"
  printf 'SSID = %s\nPASSWORD = %s\n' "$SSID" "$PASSWORD" > "$WIFI_FILE"
  echo "Saved $WIFI_FILE"
fi

# Create scripts folder and seed one example if missing.
SCRIPT_FILE="$DIR/scripts/cpu-temp.py"
NEED_SCRIPT=false
if ! "$MPREMOTE" connect "${DEVICES[0]}" fs cat :scripts/cpu-temp.py >/dev/null 2>&1; then
  NEED_SCRIPT=true
  mkdir -p "$DIR/scripts"
  cat > "$SCRIPT_FILE" <<'SCRIPT'
# CPU temperature in degrees Celsius, as a bare number (e.g. 23.4), not JSON.
# Use it as a sensor: POST /sensor/config/cpu_temp {"script": "cpu-temp.py"}
import machine

def read():
    v = machine.ADC(4).read_u16() * 3.3 / 65535
    return round(27 - (v - 0.706) / 0.001721, 1)
SCRIPT
  echo "Saved $SCRIPT_FILE - configure it with {\"script\": \"cpu-temp.py\"}"
fi

# --- deploy to every connected Pico ---
FAILED=0
for DEV in "${DEVICES[@]}"; do
  echo "Deploying to $DEV..."
  # mkdir :scripts errors once the folder exists, so it's tolerated either way.
  [ "$NEED_SCRIPT" = false ] || "$MPREMOTE" connect "$DEV" fs mkdir :scripts >/dev/null 2>&1 || true
  if ! {
    "$MPREMOTE" connect "$DEV" cp "$DIR/main.py" :main.py &&
    { [ "$NEED_CONFIG" = false ] || "$MPREMOTE" connect "$DEV" cp "$CONFIG_FILE" :config.json; } &&
    { [ "$NEED_WIFI" = false ] || "$MPREMOTE" connect "$DEV" cp "$WIFI_FILE" :wifi.txt; } &&
    { [ "$NEED_SCRIPT" = false ] || "$MPREMOTE" connect "$DEV" cp "$SCRIPT_FILE" :scripts/cpu-temp.py; } &&
    "$MPREMOTE" connect "$DEV" reset
  }; then
    echo "  FAILED: $DEV - continuing with remaining devices" >&2
    FAILED=1
  fi
done

[ "$FAILED" = 0 ] && echo "Done." || exit 1

# Clean up local staging credentials.
rm -f "$CONFIG_FILE" "$WIFI_FILE"
echo ""
