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
    curl \
    chocolate-doom \
    freedoom \
    python3-evdev \
    xdotool \
    python3-gi \
    gir1.2-gtk-3.0 \
    matchbox-window-manager

# ── uinput access for joy2key joystick mapper ──────────────────────────────────
echo "▸ Configuring uinput access for joystick mapper..."
echo 'KERNEL=="uinput", GROUP="input", MODE="0660"' > /etc/udev/rules.d/99-td5-uinput.rules
modprobe uinput 2>/dev/null || true
udevadm control --reload-rules && udevadm trigger 2>/dev/null || true
# Set permissions directly in case the udev rule hasn't taken effect yet
chown root:input /dev/uinput 2>/dev/null || true
chmod 660 /dev/uinput 2>/dev/null || true

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

# ── Suppress login messages on tty1 ──────────────────────────────────────────
# .hushlogin suppresses MOTD and "Last login" banner that appear before
# .bash_profile has a chance to run setterm.
touch "$SERVICE_HOME/.hushlogin"
chown "$SERVICE_USER:$SERVICE_USER" "$SERVICE_HOME/.hushlogin"

# ── Kiosk: .bash_profile autostart ────────────────────────────────────────────
echo "▸ Configuring kiosk autostart in $SERVICE_HOME/.bash_profile..."
BASH_PROFILE="$SERVICE_HOME/.bash_profile"
KIOSK_MARKER="# td5-dash-kiosk-autostart"

# Remove old kiosk block if present (allows re-generation with latest content)
if grep -q "$KIOSK_MARKER" "$BASH_PROFILE" 2>/dev/null; then
    sed -i "/$KIOSK_MARKER/,/^fi$/d" "$BASH_PROFILE"
fi

cat >> "$BASH_PROFILE" <<EOF

$KIOSK_MARKER
if [ "\$(tty)" = "/dev/tty1" ]; then
    setterm --foreground black --clear all 2>/dev/null
    xinit "$SCRIPT_DIR/xinitrc" -- :0 vt1 2>/tmp/td5-kiosk.log
fi
EOF
chown "$SERVICE_USER:$SERVICE_USER" "$BASH_PROFILE"
echo "  Kiosk autostart configured."

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

# ── Touch rotation (xorg.conf.d) ────────────────────────────────────────────
# The Goodix touchscreen reports coordinates in the panel's native portrait
# orientation.  After xrandr rotation the axes no longer match.  This
# xorg.conf.d snippet applies a coordinate transformation matrix at the
# driver level so touch input aligns with the rotated display.
echo "▸ Configuring touch rotation for ${DISPLAY_ROTATION}°..."
case "$DISPLAY_ROTATION" in
    90)  TOUCH_MATRIX="0 1 0 -1 0 1 0 0 1"    ;;
    180) TOUCH_MATRIX="-1 0 1 0 -1 1 0 0 1"   ;;
    270) TOUCH_MATRIX="0 -1 1 1 0 0 0 0 1"    ;;
    *)   TOUCH_MATRIX="1 0 0 0 1 0 0 0 1"     ;;
esac

mkdir -p /etc/X11/xorg.conf.d
cat > /etc/X11/xorg.conf.d/40-touch-rotation.conf <<EOF
# Generated by TD5 Dash setup.sh — DISPLAY_ROTATION=${DISPLAY_ROTATION}
Section "InputClass"
    Identifier "Goodix touch rotation"
    MatchProduct "Goodix Capacitive TouchScreen"
    Option "TransformationMatrix" "$TOUCH_MATRIX"
EndSection
EOF
echo "  Touch transform written to /etc/X11/xorg.conf.d/40-touch-rotation.conf"

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

# ── Service user must be in 'input' group to read /dev/input/js* (controllers) ──
echo "▸ Adding $SERVICE_USER to 'input' group..."
usermod -aG input "$SERVICE_USER"
echo "  Done. NOTE: on existing installs, log out and back in (or reboot)"
echo "  for this to take effect — 'systemctl restart td5-dash' alone is NOT sufficient."

# ── Sudoers: passwordless commands for the service user ───────────────────────
# restart td5-dash:           required for OTA update endpoint (POST /system/update)
# shutdown -h now:            required for shutdown endpoint (POST /system/shutdown)
# apt-get install freedoom:   required for OTA update to install/upgrade freedoom
echo "▸ Configuring sudoers for service restart, shutdown, and apt..."
SUDOERS_FILE="/etc/sudoers.d/td5-dash"
cat > "$SUDOERS_FILE" <<EOF
$SERVICE_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart td5-dash
$SERVICE_USER ALL=(ALL) NOPASSWD: /usr/sbin/shutdown -h now
$SERVICE_USER ALL=(ALL) NOPASSWD: /usr/bin/apt-get install -y freedoom python3-evdev
EOF
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
plymouth-set-default-theme -R "$THEME_NAME"

# The -R flag rebuilds /boot/initrd.img-* but doesn't always copy it to
# the firmware partition.  The Pi boots from /boot/firmware/, so we must
# ensure the firmware copy is up to date.
INITRD="/boot/initrd.img-$(uname -r)"
FIRMWARE_INITRD="/boot/firmware/initramfs_2712"
if [ -f "$INITRD" ] && [ -f "$FIRMWARE_INITRD" ]; then
    cp "$INITRD" "$FIRMWARE_INITRD"
    echo "  Copied initramfs to firmware partition."
fi
echo "  Plymouth theme '$THEME_NAME' installed."

# ── Keep Plymouth splash visible through boot → kiosk transition ─────────────
# Override plymouth-quit to use --retain-splash: Plymouth exits normally
# (releasing the DRM device so X can start) but the splash image stays
# painted on the framebuffer as static pixels until X takes over.
# Combined with setterm black console in .bash_profile, this gives a
# seamless logo → black → Chromium transition with no visible console text.
echo "▸ Configuring Plymouth retain-splash..."
systemctl unmask plymouth-quit.service 2>/dev/null || true
systemctl unmask plymouth-quit-wait.service 2>/dev/null || true
mkdir -p /etc/systemd/system/plymouth-quit.service.d
cat > /etc/systemd/system/plymouth-quit.service.d/retain.conf <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/bin/plymouth quit --retain-splash
EOF
systemctl daemon-reload
echo "  Plymouth will retain splash on quit."

# Enable splash + hide console text in kernel cmdline
CMDLINE="/boot/firmware/cmdline.txt"
if [ -f "$CMDLINE" ]; then
    if ! grep -q "splash" "$CMDLINE"; then
        sed -i 's/$/ quiet splash plymouth.ignore-serial-consoles/' "$CMDLINE"
        echo "  Added 'quiet splash' to $CMDLINE"
    else
        echo "  'splash' already present in $CMDLINE — skipping."
    fi
    # Hide kernel messages, Pi logo, and blinking cursor during the
    # Plymouth → console → X transition so the screen stays black.
    for param in "loglevel=0" "logo.nologo" "vt.global_cursor_default=0"; do
        if ! grep -q "$param" "$CMDLINE"; then
            sed -i "s/$/ $param/" "$CMDLINE"
            echo "  Added '$param' to $CMDLINE"
        fi
    done
    # Set ALL 16 VT palette colours to black.  This makes every text colour
    # invisible (black on black) so nothing is readable on the console —
    # catches any text that appears before setterm runs in .bash_profile.
    # SSH is unaffected (it uses its own terminal, not the VT).
    VT_BLACK="0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0"
    for channel in "vt.default_red" "vt.default_grn" "vt.default_blu"; do
        if ! grep -q "$channel" "$CMDLINE"; then
            sed -i "s|\$| ${channel}=${VT_BLACK}|" "$CMDLINE"
            echo "  Added '$channel' to $CMDLINE"
        fi
    done
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

# ── Clear Chromium cache ───────────────────────────────────────────────────────
# Ensures a clean browser state after deploy — prevents stale frontend files
# from persisting across updates.
echo "▸ Clearing Chromium cache..."
CHROMIUM_CACHE="$SERVICE_HOME/.cache/chromium"
CHROMIUM_CONFIG="$SERVICE_HOME/.config/chromium"
rm -rf "$CHROMIUM_CACHE" "$CHROMIUM_CONFIG"
echo "  Chromium cache cleared."

# ── Cloudflare Tunnel (optional) ──────────────────────────────────────────────
echo ""
read -r -p "▸ Set up Cloudflare Tunnel for remote access? [y/N] " _CF_ANSWER
_CF_ANSWER="${_CF_ANSWER:-N}"

if [[ "$_CF_ANSWER" =~ ^[Yy]$ ]]; then

    # Install cloudflared via Cloudflare's official signed APT repository
    if command -v cloudflared &>/dev/null; then
        echo "  cloudflared already installed — skipping install."
    else
        echo "▸ Installing cloudflared..."
        mkdir -p --mode=0755 /usr/share/keyrings
        curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
            | tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
        echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
            | tee /etc/apt/sources.list.d/cloudflared.list
        apt-get update -qq
        apt-get install -y cloudflared
        echo "  cloudflared installed."
    fi

    # Authenticate — opens a browser URL the user must visit
    echo ""
    echo "  ┌─────────────────────────────────────────────────────────────┐"
    echo "  │  A URL will appear below. Open it in a browser and          │"
    echo "  │  authorise this device with your Cloudflare account.        │"
    echo "  │  The script will continue automatically once complete.       │"
    echo "  └─────────────────────────────────────────────────────────────┘"
    echo ""
    sudo -u "$SERVICE_USER" cloudflared tunnel login

    # Create the named tunnel (credentials stored in ~/.cloudflared/)
    echo "▸ Creating tunnel 'td5-dash'..."
    _CF_CREATE=$(sudo -u "$SERVICE_USER" cloudflared tunnel create td5-dash 2>&1)
    echo "$_CF_CREATE"
    _TUNNEL_ID=$(echo "$_CF_CREATE" | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)

    if [ -z "$_TUNNEL_ID" ]; then
        echo "  ERROR: Could not extract tunnel ID from the output above."
        echo "  Skipping Cloudflare tunnel configuration — run setup again to retry."
    else
        echo "  Tunnel ID: $_TUNNEL_ID"

        # Prompt for hostnames
        echo ""
        read -r -p "  Dashboard hostname (e.g. td5dash.yourdomain.com): " _CF_DASH_HOST
        read -r -p "  SSH hostname (e.g. ssh.td5dash.yourdomain.com, Enter to skip): " _CF_SSH_HOST

        # Copy credentials to /etc/cloudflared so the service can always read them
        CF_DIR="/etc/cloudflared"
        mkdir -p "$CF_DIR"
        _CREDS_SRC="$SERVICE_HOME/.cloudflared/${_TUNNEL_ID}.json"
        _CREDS_DEST="$CF_DIR/${_TUNNEL_ID}.json"
        cp "$_CREDS_SRC" "$_CREDS_DEST"

        # Write config.yml
        {
            echo "tunnel: ${_TUNNEL_ID}"
            echo "credentials-file: ${_CREDS_DEST}"
            echo ""
            echo "ingress:"
            echo "  - hostname: ${_CF_DASH_HOST}"
            echo "    service: http://localhost:8000"
            if [ -n "$_CF_SSH_HOST" ]; then
                echo "  - hostname: ${_CF_SSH_HOST}"
                echo "    service: ssh://localhost:22"
            fi
            echo "  - service: http_status:404"
        } > "$CF_DIR/config.yml"
        echo "  Config written to $CF_DIR/config.yml"

        # Create DNS CNAME records in Cloudflare
        echo "▸ Creating DNS records..."
        sudo -u "$SERVICE_USER" cloudflared tunnel route dns td5-dash "$_CF_DASH_HOST"
        if [ -n "$_CF_SSH_HOST" ]; then
            sudo -u "$SERVICE_USER" cloudflared tunnel route dns td5-dash "$_CF_SSH_HOST"
        fi

        # Install and start the cloudflared systemd service
        echo "▸ Installing cloudflared service..."
        cloudflared service install
        systemctl enable cloudflared
        systemctl start cloudflared
        echo "  cloudflared service enabled and started."

        echo ""
        echo "  ✓ Cloudflare Tunnel active."
        echo "    Dashboard : https://${_CF_DASH_HOST}"
        if [ -n "$_CF_SSH_HOST" ]; then
            echo "    SSH       : cloudflared access ssh --hostname ${_CF_SSH_HOST}"
            echo "    (Requires cloudflared installed on your local machine)"
        fi
    fi
fi

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════╗"
echo "║  Setup complete — reboot to go.  ║"
echo "╚══════════════════════════════════╝"
echo "  sudo reboot"
