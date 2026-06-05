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

# Wait for a Doom window to appear, excluding a given window ID.
# Uses broad name match: P1 shows "LZDoom..." title while hosting, "Freedoom" after game starts.
_wait_doom_win() {
    _excl="$1"
    for _i in $(seq 1 20); do
        for _w in $(xdotool search --name "Freedoom\|LZDoom" 2>/dev/null); do
            [ "$_w" != "$_excl" ] && echo "$_w" && return 0
        done
        sleep 0.5
    done
    return 1
}

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

# ── Copy INI files to /tmp so LZDoom's config writeback doesn't dirty the repo ──
cp "$SCRIPT_DIR/lzdoom-p1.ini" /tmp/lzdoom-p1.ini
cp "$SCRIPT_DIR/lzdoom-p2.ini" /tmp/lzdoom-p2.ini 2>/dev/null || true

# ── Launch LZDoom ─────────────────────────────────────────────────────
# shellcheck disable=SC2086
COMMON_OPTS="-iwad $WAD -skill $SKILL -config /tmp/lzdoom-p1.ini +mouse_capturemode 0"

case "$MODE" in
    single)
        # shellcheck disable=SC2086
        SDL_VIDEO_WINDOW_POS="0,0" $P1_PULSE_PREFIX "$LZDOOM" $COMMON_OPTS \
            +vid_defwidth 1280 +vid_defheight 400 +win_w 1280 +win_h 400 +win_x 0 +win_y 0 &
        ;;
    coop)
        # Launch P1 first, wait for its window, position it, then launch P2.
        # openbox ignores win_x=0 (treats it as default/unset); sequential
        # xdotool capture is the only reliable way to position P1.
        # shellcheck disable=SC2086
        $P1_PULSE_PREFIX "$LZDOOM" $COMMON_OPTS \
            +vid_defwidth 640 +vid_defheight 400 \
            -host 2 -port 5029 &
        p1_win=$(_wait_doom_win "")
        if [ -z "$p1_win" ]; then
            echo "ERROR: P1 window did not appear" >&2; exit 1
        fi
        xdotool windowmove "$p1_win" 0 0
        xdotool windowsize "$p1_win" 640 400

        # shellcheck disable=SC2086
        $P2_PULSE_PREFIX "$LZDOOM" \
            -iwad $WAD -skill $SKILL -config /tmp/lzdoom-p2.ini +mouse_capturemode 0 \
            +vid_defwidth 640 +vid_defheight 400 \
            -join 127.0.0.1:5029 &
        p2_win=$(_wait_doom_win "$p1_win")
        if [ -n "$p2_win" ]; then
            xdotool windowmove "$p2_win" 640 0
            xdotool windowsize "$p2_win" 640 400
        fi
        ;;
    deathmatch)
        # shellcheck disable=SC2086
        $P1_PULSE_PREFIX "$LZDOOM" $COMMON_OPTS \
            +vid_defwidth 640 +vid_defheight 400 \
            -deathmatch -host 2 -port 5029 &
        p1_win=$(_wait_doom_win "")
        if [ -z "$p1_win" ]; then
            echo "ERROR: P1 window did not appear" >&2; exit 1
        fi
        xdotool windowmove "$p1_win" 0 0
        xdotool windowsize "$p1_win" 640 400

        # shellcheck disable=SC2086
        $P2_PULSE_PREFIX "$LZDOOM" \
            -iwad $WAD -skill $SKILL -config /tmp/lzdoom-p2.ini +mouse_capturemode 0 \
            +vid_defwidth 640 +vid_defheight 400 \
            -join 127.0.0.1:5029 &
        p2_win=$(_wait_doom_win "$p1_win")
        if [ -n "$p2_win" ]; then
            xdotool windowmove "$p2_win" 640 0
            xdotool windowsize "$p2_win" 640 400
        fi
        ;;
esac

# Detect immediate LZDoom failure
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
