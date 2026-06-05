#!/usr/bin/env python3
"""
Per-player joystick → xdotool window-targeted key injection.

Reads /dev/input/jsN and sends key events to a specific X11 window ID,
bypassing SDL2's focus handling for true 2P controller isolation.

Usage: joy2window.py <js-device> <window-id>
  e.g. joy2window.py /dev/input/js0 12345678
"""
import sys
import struct
import subprocess

JS_EVENT_FMT  = "IhBB"
JS_EVENT_SIZE = struct.calcsize(JS_EVENT_FMT)
JS_EVENT_BUTTON = 0x01
JS_EVENT_AXIS   = 0x02
JS_EVENT_INIT   = 0x80
AXIS_THRESHOLD  = 16000

# Doom default key bindings (X keysym names for xdotool)
BUTTON_KEYS = {
    1: "ctrl",           # A      → fire
    2: "space",          # B      → use / open door
    4: "alt",            # L      → strafe modifier
    5: "shift",          # R      → run
    3: "bracketleft",    # Y      → prev weapon
    0: "bracketright",   # X      → next weapon
    9: "Escape",         # Start  → menu
    8: "Tab",            # Select → automap
}

AXIS_KEYS = {
    0: ("Left",  "Right"),  # D-pad left/right → turn
    1: ("Up",    "Down"),   # D-pad up/down    → forward/back
}


def xkey(window_id, key, press):
    subprocess.run(
        ["xdotool", "keydown" if press else "keyup",
         "--window", str(window_id), key],
        check=False, capture_output=True,
    )


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} /dev/input/jsN window-id", file=sys.stderr)
        sys.exit(1)

    js_path   = sys.argv[1]
    window_id = sys.argv[2]

    axis_state   = {}
    button_state = {}

    with open(js_path, "rb") as js:
        while True:
            data = js.read(JS_EVENT_SIZE)
            if not data:
                break
            _, value, etype, number = struct.unpack(JS_EVENT_FMT, data)
            etype &= ~JS_EVENT_INIT

            if etype == JS_EVENT_BUTTON:
                key = BUTTON_KEYS.get(number)
                if key is None:
                    continue
                pressed = bool(value)
                if button_state.get(number) == pressed:
                    continue
                button_state[number] = pressed
                xkey(window_id, key, pressed)

            elif etype == JS_EVENT_AXIS:
                pair = AXIS_KEYS.get(number)
                if pair is None:
                    continue
                neg_key, pos_key = pair
                direction = (-1 if value < -AXIS_THRESHOLD
                             else 1 if value > AXIS_THRESHOLD
                             else 0)
                prev = axis_state.get(number, 0)
                if direction == prev:
                    continue
                axis_state[number] = direction
                if prev == -1:
                    xkey(window_id, neg_key, False)
                elif prev == 1:
                    xkey(window_id, pos_key, False)
                if direction == -1:
                    xkey(window_id, neg_key, True)
                elif direction == 1:
                    xkey(window_id, pos_key, True)


if __name__ == "__main__":
    main()
