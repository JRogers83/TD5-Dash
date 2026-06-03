#!/bin/sh
# Doom Mode launcher — orchestrates matchbox + chocolate-doom + overlay.
# Env in: MODE (single|coop|deathmatch), WAD (full path), SKILL (1-5)
# Caller invokes with start_new_session=True so this becomes a session
# leader; PID == PGID, and os.killpg(pid, SIG) reaches all children.
#
# Exit codes (mapped to user-facing messages by game_service):
#   0  clean exit (game quit normally)
#   2  controllers missing for a 2P mode
#   3  matchbox-window-manager failed to start
#   4  chocolate-doom failed to launch

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# chocolate-doom installs to /usr/games which is not in the systemd service PATH
CHOCOLATE_DOOM=/usr/games/chocolate-doom
CHOCOLATE_DOOM_SERVER=/usr/games/chocolate-doom-server

SINK_P1_MOD=""
SINK_P2_MOD=""

cleanup() {
    # Unload PulseAudio remap sinks first (before killing anything that holds them)
    [ -n "$SINK_P1_MOD" ] && pactl unload-module "$SINK_P1_MOD" 2>/dev/null || true
    [ -n "$SINK_P2_MOD" ] && pactl unload-module "$SINK_P2_MOD" 2>/dev/null || true
    # Kill everything in this process group except the shell itself
    pkill -P $$ 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ── Controller validation (2P only) ───────────────────────────────────
if [ "$MODE" != "single" ]; then
    JS_COUNT=$(ls /dev/input/js* 2>/dev/null | wc -l)
    if [ "$JS_COUNT" -lt 2 ]; then
        echo "ERROR: 2P mode needs 2 controllers, found $JS_COUNT" >&2
        exit 2
    fi
fi

# ── Window manager ────────────────────────────────────────────────────
matchbox-window-manager -use_titlebar no -use_cursor no &
MATCHBOX_PID=$!
sleep 0.2
if ! kill -0 "$MATCHBOX_PID" 2>/dev/null; then
    echo "ERROR: matchbox-window-manager failed to start" >&2
    exit 3
fi

# Paint background black either side of single-player window
xsetroot -solid black 2>/dev/null || true

# ── Audio: per-player L/R remap sinks for 2P modes ────────────────────
P1_PULSE_PREFIX=""
P2_PULSE_PREFIX=""
if [ "$MODE" != "single" ]; then
    DEFAULT_SINK=$(pactl get-default-sink 2>/dev/null || echo "")
    if [ -n "$DEFAULT_SINK" ]; then
        SINK_P1_MOD=$(pactl load-module module-remap-sink \
            sink_name=doom_p1 \
            master="$DEFAULT_SINK" \
            channels=2 \
            master_channel_map=front-left,front-left \
            channel_map=front-left,front-right \
            remix=no 2>/dev/null) || SINK_P1_MOD=""

        SINK_P2_MOD=$(pactl load-module module-remap-sink \
            sink_name=doom_p2 \
            master="$DEFAULT_SINK" \
            channels=2 \
            master_channel_map=front-right,front-right \
            channel_map=front-left,front-right \
            remix=no 2>/dev/null) || SINK_P2_MOD=""

        if [ -n "$SINK_P1_MOD" ] && [ -n "$SINK_P2_MOD" ]; then
            P1_PULSE_PREFIX="env PULSE_SINK=doom_p1"
            P2_PULSE_PREFIX="env PULSE_SINK=doom_p2"
        else
            echo "WARN: per-player remap sinks failed; falling back to default sink" >&2
        fi
    fi
fi

# ── joy2key: map joystick input to keyboard events ────────────────────
# Bypasses SDL joystick support which is unreliable with generic controllers.
python3 "$SCRIPT_DIR/joy2key.py" /dev/input/js0 &
if [ "$MODE" != "single" ]; then
    python3 "$SCRIPT_DIR/joy2key.py" /dev/input/js1 &
fi
sleep 0.2  # let uinput devices register before chocolate-doom opens

# ── Launch chocolate-doom ─────────────────────────────────────────────
# -nojoy: disable SDL joystick (joy2key handles input via keyboard events)
COMMON_OPTS="-iwad $WAD -nojoy -nograbmouse -skill $SKILL"

case "$MODE" in
    single)
        # shellcheck disable=SC2086
        $P1_PULSE_PREFIX "$CHOCOLATE_DOOM" $COMMON_OPTS \
            -window -geometry 640x400+320+0 &
        ;;
    coop)
        "$CHOCOLATE_DOOM_SERVER" -deathmatch 0 -nodes 2 -port 5029 &
        sleep 0.3
        # shellcheck disable=SC2086
        $P1_PULSE_PREFIX "$CHOCOLATE_DOOM" $COMMON_OPTS -connect 127.0.0.1:5029 \
            -window -geometry 640x400+0+0 &
        # shellcheck disable=SC2086
        $P2_PULSE_PREFIX "$CHOCOLATE_DOOM" $COMMON_OPTS -connect 127.0.0.1:5029 \
            -window -geometry 640x400+640+0 &
        ;;
    deathmatch)
        "$CHOCOLATE_DOOM_SERVER" -deathmatch 1 -nodes 2 -port 5029 &
        sleep 0.3
        # shellcheck disable=SC2086
        $P1_PULSE_PREFIX "$CHOCOLATE_DOOM" $COMMON_OPTS -connect 127.0.0.1:5029 \
            -window -geometry 640x400+0+0 &
        # shellcheck disable=SC2086
        $P2_PULSE_PREFIX "$CHOCOLATE_DOOM" $COMMON_OPTS -connect 127.0.0.1:5029 \
            -window -geometry 640x400+640+0 &
        ;;
esac

# Detect immediate chocolate-doom failure (within ~0.5 s of launch)
sleep 0.5
if ! pgrep -f chocolate-doom >/dev/null 2>&1; then
    echo "ERROR: chocolate-doom failed to launch" >&2
    exit 4
fi

# ── Overlay ────────────────────────────────────────────────────────────
MODE="$MODE" python3 "$SCRIPT_DIR/overlay.py" &

# ── Wait for all chocolate-doom clients to exit ───────────────────────
while pgrep -f chocolate-doom >/dev/null 2>&1; do
    sleep 0.5
done

exit 0
