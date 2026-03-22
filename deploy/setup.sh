#!/usr/bin/env bash
# TD5 Dash — Pi OS Bookworm first-time setup
# Run as root from the repo root: sudo ./deploy/setup.sh

set -euo pipefail

# ── Paths ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

# The user who invoked sudo (falls back to 'pi' for direct root login)
SERVICE_USER="${SUDO_USER:-pi}"
SERVICE_HOME="$(eval echo "~$SERVICE_USER")"

# ── Preflight ──────────────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && { echo "ERROR: run as root:  sudo $0"; exit 1; }

echo "╔══════════════════════════════════╗"
echo "║      TD5 Dash — Setup            ║"
echo "╚══════════════════════════════════╝"
echo "  Repo : $REPO_DIR"
echo "  User : $SERVICE_USER ($SERVICE_HOME)"
echo ""

# ── System packages ────────────────────────────────────────────────────────────
echo "▸ Installing system packages..."
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3-venv \
    python3-pil \
    chromium \
    xserver-xorg-core \
    xserver-xorg-input-evdev \
    x11-xserver-utils \
    xinit \
    unclutter \
    plymouth \
    plymouth-themes \
    curl

# ── Python virtualenv ──────────────────────────────────────────────────────────
echo "▸ Setting up Python venv..."
VENV="$REPO_DIR/.venv"
if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$REPO_DIR/backend/requirements.txt"
chown -R "$SERVICE_USER:$SERVICE_USER" "$VENV"

# ── systemd service (backend) ──────────────────────────────────────────────────
echo "▸ Installing systemd service..."
sed \
    -e "s|__USER__|$SERVICE_USER|g" \
    -e "s|__REPO_DIR__|$REPO_DIR|g" \
    "$SCRIPT_DIR/td5-dash.service" > /etc/systemd/system/td5-dash.service

systemctl daemon-reload
systemctl enable td5-dash.service
echo "  td5-dash.service enabled."

# ── Kiosk: .bash_profile autostart ────────────────────────────────────────────
echo "▸ Configuring kiosk autostart in $SERVICE_HOME/.bash_profile..."
BASH_PROFILE="$SERVICE_HOME/.bash_profile"
KIOSK_MARKER="# td5-dash-kiosk-autostart"

if ! grep -q "$KIOSK_MARKER" "$BASH_PROFILE" 2>/dev/null; then
    cat >> "$BASH_PROFILE" <<EOF

$KIOSK_MARKER
if [ "\$(tty)" = "/dev/tty1" ]; then
    xinit "$SCRIPT_DIR/xinitrc" -- :0 vt1 2>/tmp/td5-kiosk.log
fi
EOF
    chown "$SERVICE_USER:$SERVICE_USER" "$BASH_PROFILE"
    echo "  Kiosk autostart added."
else
    echo "  Kiosk autostart already present, skipping."
fi

# ── Console autologin ──────────────────────────────────────────────────────────
echo "▸ Enabling console autologin for $SERVICE_USER..."
# raspi-config sets autologin for the 'pi' user; for other usernames we
# drop a systemd override directly.
if [ "$SERVICE_USER" = "pi" ]; then
    raspi-config nonint do_boot_behaviour B2
else
    mkdir -p /etc/systemd/system/getty@tty1.service.d
    cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $SERVICE_USER --noclear %I \$TERM
EOF
    systemctl daemon-reload
fi

# ── Display dtoverlay ──────────────────────────────────────────────────────────
BOOT_CONFIG="/boot/firmware/config.txt"

if [ -f "$BOOT_CONFIG" ]; then
    # vc4-kms-v3d is required for KMS display stack — add if missing
    if ! grep -q "dtoverlay=vc4-kms-v3d" "$BOOT_CONFIG"; then
        echo "dtoverlay=vc4-kms-v3d" >> "$BOOT_CONFIG"
        echo "▸ vc4-kms-v3d overlay added to $BOOT_CONFIG"
    else
        echo "▸ vc4-kms-v3d already present in $BOOT_CONFIG"
    fi

    # Waveshare panel overlay
    if ! grep -q "vc4-kms-dsi-waveshare-panel" "$BOOT_CONFIG"; then
        printf '\n# Waveshare 7.9" DSI display\n' >> "$BOOT_CONFIG"
        echo "dtoverlay=vc4-kms-dsi-waveshare-panel,7_9_inch" >> "$BOOT_CONFIG"
        echo "▸ Waveshare DSI overlay added to $BOOT_CONFIG"
    else
        echo "▸ Waveshare DSI overlay already present in $BOOT_CONFIG"
    fi

    # I2C — required for capacitive touch controller
    if ! grep -q "dtparam=i2c_arm=on" "$BOOT_CONFIG"; then
        echo "dtparam=i2c_arm=on" >> "$BOOT_CONFIG"
        echo "▸ I2C enabled in $BOOT_CONFIG"
    else
        echo "▸ I2C already enabled in $BOOT_CONFIG"
    fi
else
    echo "WARNING: $BOOT_CONFIG not found — add overlays manually."
fi

# ── Display mode + rotation (cmdline.txt) ──────────────────────────────────────
# The video= parameter sets the framebuffer mode AND rotation for the console
# and Plymouth splash.  X11/Chromium ignores this — xinitrc applies rotation
# separately via xrandr.  Both read DISPLAY_ROTATION from .env.
CMDLINE="/boot/firmware/cmdline.txt"
ENV_FILE="$REPO_DIR/.env"
DISPLAY_ROTATION=270
if [ -f "$ENV_FILE" ]; then
    _rot=$(grep '^DISPLAY_ROTATION=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2 | tr -cd '0-9')
    [ -n "$_rot" ] && DISPLAY_ROTATION="$_rot"
fi
echo "▸ Display rotation: ${DISPLAY_ROTATION}° (from .env)"
VIDEO_PARAM="video=DSI-2:400x1280e,rotate=${DISPLAY_ROTATION}"

if [ -f "$CMDLINE" ]; then
    if grep -q "video=DSI-[12]" "$CMDLINE"; then
        sed -i "s|video=DSI-[12]:[^ ]*|$VIDEO_PARAM|" "$CMDLINE"
        echo "▸ Display mode updated in $CMDLINE"
    else
        sed -i "s|^|$VIDEO_PARAM |" "$CMDLINE"
        echo "▸ Display mode added to $CMDLINE"
    fi
else
    echo "WARNING: $CMDLINE not found — add '$VIDEO_PARAM' manually."
fi

# ── Raspotify (Spotify Connect / librespot) ────────────────────────────────────
RASPOTIFY_CONF="/etc/raspotify/conf"

if systemctl is-active --quiet raspotify 2>/dev/null; then
    echo "▸ Raspotify already running — skipping install."
elif [ -f "$RASPOTIFY_CONF" ]; then
    echo "▸ Raspotify already installed — skipping install."
else
    echo "▸ Installing Raspotify..."
    curl -sL https://dtcooper.github.io/raspotify/install.sh | sh
    echo "  Raspotify installed."
fi

# Configure device name (set DEFENDER_DEVICE_NAME before running to override)
DEVICE_NAME="${DEFENDER_DEVICE_NAME:-Defender}"
echo "▸ Configuring Raspotify device name: $DEVICE_NAME"
if grep -q "^LIBRESPOT_NAME=" "$RASPOTIFY_CONF" 2>/dev/null; then
    sed -i "s|^LIBRESPOT_NAME=.*|LIBRESPOT_NAME=\"$DEVICE_NAME\"|" "$RASPOTIFY_CONF"
elif grep -q "^#LIBRESPOT_NAME=" "$RASPOTIFY_CONF" 2>/dev/null; then
    sed -i "s|^#LIBRESPOT_NAME=.*|LIBRESPOT_NAME=\"$DEVICE_NAME\"|" "$RASPOTIFY_CONF"
else
    echo "LIBRESPOT_NAME=\"$DEVICE_NAME\"" >> "$RASPOTIFY_CONF"
fi

systemctl enable raspotify
systemctl restart raspotify
echo "  Raspotify enabled and restarted as '$DEVICE_NAME'."

# ── PulseAudio loopback for visualiser ────────────────────────────────────────
# Creates a null sink (td5_sink) that Raspotify outputs to.
# A loopback routes the monitor back to the real default output so audio
# still reaches the head unit / BT device.
# The browser captures td5_sink.monitor via getUserMedia for real FFT data.
# Works with both PulseAudio and PipeWire (via pipewire-pulse).
echo "▸ Configuring PulseAudio loopback for spectrum visualiser..."

PA_DROP_DIR="/etc/pulse/default.pa.d"
PA_CONF="$PA_DROP_DIR/td5-visualiser.pa"

mkdir -p "$PA_DROP_DIR"

cat > "$PA_CONF" <<'EOF'
# TD5 Dash — virtual monitor sink for spectrum visualiser
# Raspotify outputs here (via PULSE_SINK env var).
# Loopback routes audio back to the real default output.
load-module module-null-sink sink_name=td5_sink sink_properties=device.description="TD5-Visualiser"
load-module module-loopback source=td5_sink.monitor latency_msec=20
set-default-source td5_sink.monitor
EOF

echo "  Written $PA_CONF"

# Tell Raspotify to output to the virtual sink
if grep -q "^LIBRESPOT_BACKEND=" "$RASPOTIFY_CONF" 2>/dev/null; then
    sed -i "s|^LIBRESPOT_BACKEND=.*|LIBRESPOT_BACKEND=\"pulseaudio\"|" "$RASPOTIFY_CONF"
else
    echo 'LIBRESPOT_BACKEND="pulseaudio"' >> "$RASPOTIFY_CONF"
fi

# Set PULSE_SINK so Raspotify's librespot process targets td5_sink
if grep -q "^LIBRESPOT_PA_SINK=" "$RASPOTIFY_CONF" 2>/dev/null; then
    sed -i "s|^LIBRESPOT_PA_SINK=.*|LIBRESPOT_PA_SINK=\"td5_sink\"|" "$RASPOTIFY_CONF"
else
    echo 'LIBRESPOT_PA_SINK="td5_sink"' >> "$RASPOTIFY_CONF"
fi

systemctl restart raspotify
echo "  Raspotify reconfigured to output via td5_sink."
echo "  NOTE: PulseAudio loopback takes effect on next login/reboot."

# ── Sudoers: allow service user to restart td5-dash without password ───────────
# Required for the OTA update endpoint (POST /system/update) to restart the
# service after a git pull without an interactive sudo prompt.
echo "▸ Configuring sudoers for service restart..."
SUDOERS_FILE="/etc/sudoers.d/td5-dash"
echo "$SERVICE_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart td5-dash" > "$SUDOERS_FILE"
chmod 440 "$SUDOERS_FILE"
echo "  Sudoers entry written to $SUDOERS_FILE"

# ── Plymouth splash screen ─────────────────────────────────────────────────────
echo "▸ Installing Plymouth splash screen..."

THEME_NAME="td5-dash"
THEME_SRC="$SCRIPT_DIR/plymouth/$THEME_NAME"
THEME_DEST="/usr/share/plymouth/themes/$THEME_NAME"
LOGO_SRC="$REPO_DIR/LR-Logo.png"

# Process the logo (counter-rotated for unrotated framebuffer)
python3 "$SCRIPT_DIR/plymouth/prepare_logo.py" \
    "$LOGO_SRC" \
    "$THEME_SRC/logo.png" \
    "$DISPLAY_ROTATION"

# Install theme files
mkdir -p "$THEME_DEST"
cp "$THEME_SRC/"* "$THEME_DEST/"

# Set as default and rebuild initramfs (required for Plymouth to activate)
plymouth-set-default-theme "$THEME_NAME"
update-initramfs -u
echo "  Plymouth theme '$THEME_NAME' installed."

# Enable splash in kernel cmdline — add 'quiet splash' if not already present
CMDLINE="/boot/firmware/cmdline.txt"
if [ -f "$CMDLINE" ]; then
    if ! grep -q "splash" "$CMDLINE"; then
        # Append on the same line (cmdline.txt must be a single line)
        sed -i 's/$/ quiet splash plymouth.ignore-serial-consoles/' "$CMDLINE"
        echo "  Added 'quiet splash' to $CMDLINE"
    else
        echo "  'splash' already present in $CMDLINE — skipping."
    fi
else
    echo "  WARNING: $CMDLINE not found — add 'quiet splash' manually."
fi

# ── Boot time optimisations ────────────────────────────────────────────────────
# Disable services that are not needed for this kiosk and add measurable boot delay.
echo "▸ Applying boot time optimisations..."

# avahi-daemon: mDNS/DNS-SD discovery — not needed; saves ~400ms
systemctl disable --now avahi-daemon.service 2>/dev/null || true

# triggerhappy: keyboard/input hotkey daemon — not needed in kiosk
systemctl disable --now triggerhappy.service 2>/dev/null || true

# ModemManager: mobile broadband management — no modem present
systemctl disable --now ModemManager.service 2>/dev/null || true

# Don't block boot waiting for ALL network interfaces to come up —
# '--any' means proceed as soon as at least one interface is ready.
mkdir -p /etc/systemd/system/systemd-networkd-wait-online.service.d
cat > /etc/systemd/system/systemd-networkd-wait-online.service.d/timeout.conf <<'EOF'
[Service]
ExecStart=
ExecStart=/lib/systemd/systemd-networkd-wait-online --any --timeout=10
EOF

echo "  Boot optimisations applied."
echo "  TIP: After first boot, run 'systemd-analyze blame' to identify remaining slow units."

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════╗"
echo "║  Setup complete — reboot to go.  ║"
echo "╚══════════════════════════════════╝"
echo "  sudo reboot"
