#!/usr/bin/env bash
# deploy_led_matrix.sh
# Deploys LED matrix files to the Raspberry Pi and optionally installs dependencies.
#
# Usage:
#   ./deploy_led_matrix.sh              # sync files only
#   ./deploy_led_matrix.sh --install    # sync + install CLI deps on the Pi
#   ./deploy_led_matrix.sh --web        # sync + install web server deps (Flask etc.)
#   ./deploy_led_matrix.sh --service    # install + enable the systemd web service
#   ./deploy_led_matrix.sh --help

set -euo pipefail

PI_HOST="10.0.0.81"
PI_USER="renanfernandes"
PI_DEST="/home/${PI_USER}/led_matrix"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FILES=(
    "led_matrix_display.py"
    "led_matrix_web.py"
    "led_matrix_display_config.yaml.example"
    "requirements_led_matrix.txt"
    "requirements_led_matrix_web.txt"
)

# Colour helpers
_bold=$'\e[1m'; _reset=$'\e[0m'; _green=$'\e[32m'; _yellow=$'\e[33m'; _red=$'\e[31m'
info()    { echo "${_bold}${_green}▶${_reset} $*"; }
warn()    { echo "${_yellow}⚠${_reset}  $*"; }
die()     { echo "${_red}✗${_reset}  $*" >&2; exit 1; }

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  --install    After syncing, install CLI Python dependencies on the Pi
               (runs apt-get + pip install -r requirements_led_matrix.txt)
  --web        After syncing, install web server dependencies on the Pi
               (runs apt-get + pip install -r requirements_led_matrix_web.txt)
  --service    Install and enable the systemd web service on the Pi
               (copies led-matrix-web.service, enables + starts it)
  --help       Show this help message
EOF
}

INSTALL=false
INSTALL_WEB=false
INSTALL_SERVICE=false
for arg in "$@"; do
    case "$arg" in
        --install)  INSTALL=true ;;
        --web)      INSTALL_WEB=true ;;
        --service)  INSTALL_SERVICE=true ;;
        --help)     usage; exit 0 ;;
        *) die "Unknown option: $arg  (try --help)" ;;
    esac
done

# ── Verify source files exist ─────────────────────────────────────────────────
info "Checking source files in ${SCRIPT_DIR} …"
missing=()
for f in "${FILES[@]}"; do
    [[ -f "${SCRIPT_DIR}/${f}" ]] || missing+=("$f")
done
if (( ${#missing[@]} > 0 )); then
    die "Missing files: ${missing[*]}"
fi

# Include the real config if it exists (never overwrite an existing one on the Pi)
CONFIG="${SCRIPT_DIR}/led_matrix_display_config.yaml"
if [[ -f "$CONFIG" ]]; then
    warn "Local config found — it will be synced to the Pi."
    warn "If a config already exists on the Pi it will NOT be overwritten (--ignore-existing)."
fi

# ── Create destination directory ──────────────────────────────────────────────
info "Creating ${PI_USER}@${PI_HOST}:${PI_DEST} …"
ssh "${PI_USER}@${PI_HOST}" "mkdir -p '${PI_DEST}/service_files'"

# ── Sync core files ───────────────────────────────────────────────────────────
info "Syncing files …"
rsync -avz --progress \
    "${FILES[@]/#/${SCRIPT_DIR}/}" \
    "${PI_USER}@${PI_HOST}:${PI_DEST}/"

# Sync config only if it exists locally, without overwriting an existing one on the Pi
if [[ -f "$CONFIG" ]]; then
    rsync -avz --ignore-existing \
        "${CONFIG}" \
        "${PI_USER}@${PI_HOST}:${PI_DEST}/"
fi

# Sync service_files/ directory (preserves subdirectory structure)
rsync -avz --progress \
    "${SCRIPT_DIR}/service_files/" \
    "${PI_USER}@${PI_HOST}:${PI_DEST}/service_files/"

# ── Bootstrap config from example if no config exists on the Pi ───────────────
info "Checking for config on the Pi …"
ssh "${PI_USER}@${PI_HOST}" bash <<'REMOTE'
DEST="${HOME}/led_matrix"
CONFIG="${DEST}/led_matrix_display_config.yaml"
EXAMPLE="${DEST}/led_matrix_display_config.yaml.example"
if [[ ! -f "${CONFIG}" && -f "${EXAMPLE}" ]]; then
    cp "${EXAMPLE}" "${CONFIG}"
    echo "  ✓ Created config from example — edit ${CONFIG} before first run."
else
    echo "  ✓ Config already present — skipped."
fi
REMOTE

# ── Optional: install CLI dependencies ───────────────────────────────────────
if [[ "$INSTALL" == true ]]; then
    info "Installing apt packages on the Pi …"
    ssh "${PI_USER}@${PI_HOST}" \
        sudo apt-get install -y python3-tk python3-pil.imagetk 2>&1 | grep -v '^debconf'

    info "Installing Python dependencies on the Pi …"
    ssh "${PI_USER}@${PI_HOST}" bash <<REMOTE
        set -e
        cd "${PI_DEST}"
        pip3 install --quiet -r requirements_led_matrix.txt
        echo "  ✓ CLI packages installed."
REMOTE
fi

# ── Optional: install web server dependencies ─────────────────────────────────
if [[ "$INSTALL_WEB" == true ]]; then
    info "Installing apt packages on the Pi …"
    ssh "${PI_USER}@${PI_HOST}" \
        sudo apt-get install -y python3-tk python3-pil.imagetk 2>&1 | grep -v '^debconf'

    info "Installing web server dependencies on the Pi …"
    ssh "${PI_USER}@${PI_HOST}" bash <<REMOTE
        set -e
        cd "${PI_DEST}"
        pip3 install --quiet -r requirements_led_matrix_web.txt
        echo "  ✓ Web server packages installed."
REMOTE
fi

# ── Optional: install systemd service ────────────────────────────────────────
if [[ "$INSTALL_SERVICE" == true ]]; then
    info "Installing systemd service on the Pi …"
    ssh -t "${PI_USER}@${PI_HOST}" "sudo cp '${PI_DEST}/service_files/led-matrix-web.service' /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable led-matrix-web && sudo systemctl restart led-matrix-web && echo '  ✓ Service installed and started.' && sudo systemctl status led-matrix-web --no-pager -l | head -20"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
info "Deploy complete!"
echo ""
echo "  SSH into the Pi:  ssh ${PI_USER}@${PI_HOST}"
echo "  Working dir:      ${PI_DEST}"
echo ""
echo "  SSH into the Pi:     ssh ${PI_USER}@${PI_HOST}"
echo "  Working dir:         ${PI_DEST}"
echo ""
echo "  Web server (browser UI + API):"
echo "    sudo python3 ${PI_DEST}/led_matrix_web.py"
echo "    open http://${PI_HOST}:5000  in your browser"
echo ""
echo "  Service management:"
echo "    sudo systemctl start   led-matrix-web"
echo "    sudo systemctl stop    led-matrix-web"
echo "    sudo systemctl restart led-matrix-web"
echo "    journalctl -u led-matrix-web -f"
echo ""
echo "  CLI — quick test (simulation, no hardware):"
echo "    python3 ${PI_DEST}/led_matrix_display.py --simulate image <photo.png>"
echo ""
echo "  CLI — hardware run (requires sudo for GPIO):"
echo "    sudo python3 ${PI_DEST}/led_matrix_display.py spotify"
echo ""
echo "  First time?  Edit the config before running:"
echo "    nano ${PI_DEST}/led_matrix_display_config.yaml"
