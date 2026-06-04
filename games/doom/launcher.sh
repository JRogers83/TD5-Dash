#!/bin/sh
# Doom Mode launcher — orchestrates openbox + LZDoom instance(s) + overlay.
# Env in: MODE (single|coop|deathmatch), WAD (full path), SKILL (1-5), LZDOOM (binary path)
# Caller invokes with start_new_session=True so this becomes session leader.
#
# Exit codes:
#   0  clean exit
#   1  LZDoom failed to start
#   2  controllers missing for 2P mode

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ZMusic shared library lives alongside the binary — add to library path
export LD_LIBRARY_PATH="$SCRIPT_DIR:${LD_LIBRARY_PATH:-}"

SINK_P1_MOD=""
SINK_P2_MOD=""

cleanup() {
    [ -n "$SINK_P1_MOD" ] && pactl unload-module "$SINK_P1_MOD" 2>/dev/null || true
    [ -n "$SINK_P2_MOD" ] && pactl unload-module "$SINK_P2_MOD" 2>/dev/null || true
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
openbox --sm-disable --config-file "$SCRIPT_DIR/openbox-rc.xml" &
sleep 0.3
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

# ── Launch LZDoom ─────────────────────────────────────────────────────
# shellcheck disable=SC2086
COMMON_OPTS="-iwad $WAD -skill $SKILL -config $SCRIPT_DIR/lzdoom-p1.ini"

case "$MODE" in
    single)
        # shellcheck disable=SC2086
        SDL_VIDEO_WINDOW_POS="0,0" $P1_PULSE_PREFIX "$LZDOOM" $COMMON_OPTS \
            +vid_defwidth 1280 +vid_defheight 400 &
        ;;
    coop)
        # shellcheck disable=SC2086
        SDL_VIDEO_WINDOW_POS="0,0" $P1_PULSE_PREFIX "$LZDOOM" $COMMON_OPTS \
            +vid_defwidth 640 +vid_defheight 400 \
            -host 2 -port 5029 &
        sleep 2.5
        # shellcheck disable=SC2086
        SDL_VIDEO_WINDOW_POS="640,0" $P2_PULSE_PREFIX "$LZDOOM" \
            -iwad $WAD -skill $SKILL -config $SCRIPT_DIR/lzdoom-p2.ini \
            +vid_defwidth 640 +vid_defheight 400 \
            -connect 127.0.0.1:5029 &
        ;;
    deathmatch)
        # shellcheck disable=SC2086
        SDL_VIDEO_WINDOW_POS="0,0" $P1_PULSE_PREFIX "$LZDOOM" $COMMON_OPTS \
            +vid_defwidth 640 +vid_defheight 400 \
            -deathmatch -host 2 -port 5029 &
        sleep 2.5
        # shellcheck disable=SC2086
        SDL_VIDEO_WINDOW_POS="640,0" $P2_PULSE_PREFIX "$LZDOOM" \
            -iwad $WAD -skill $SKILL -config $SCRIPT_DIR/lzdoom-p2.ini \
            +vid_defwidth 640 +vid_defheight 400 \
            -connect 127.0.0.1:5029 &
        ;;
esac

# Detect immediate LZDoom failure (within 1s of launch)
sleep 1.0
if ! pgrep -f lzdoom >/dev/null 2>&1; then
    echo "ERROR: lzdoom failed to launch" >&2
    exit 1
fi

# ── Overlay ────────────────────────────────────────────────────────────
MODE="$MODE" python3 "$SCRIPT_DIR/overlay.py" &

# ── Wait for all LZDoom instances to exit ─────────────────────────────
while pgrep -f lzdoom >/dev/null 2>&1; do
    sleep 0.5
done

exit 0
