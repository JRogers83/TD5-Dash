#!/usr/bin/env python3
"""
Joystick-to-keyboard mapper for chocolate-doom.
Reads /dev/input/jsN and injects keyboard events via uinput (python3-evdev).

Usage: joy2key.py /dev/input/js0
"""
import sys
import struct
import evdev
from evdev import UInput, ecodes

JS_EVENT_FMT  = "IhBB"
JS_EVENT_SIZE = struct.calcsize(JS_EVENT_FMT)
JS_EVENT_BUTTON = 0x01
JS_EVENT_AXIS   = 0x02
JS_EVENT_INIT   = 0x80

AXIS_THRESHOLD = 16000

# chocolate-doom default keyboard bindings
BUTTON_KEYS = {
    1: ecodes.KEY_LEFTCTRL,    # A      -> fire
    2: ecodes.KEY_SPACE,       # B      -> use / open door
    4: ecodes.KEY_LEFTALT,     # L      -> strafe modifier
    5: ecodes.KEY_LEFTSHIFT,   # R      -> run
    3: ecodes.KEY_LEFTBRACE,   # Y      -> prev weapon  [
    0: ecodes.KEY_RIGHTBRACE,  # X      -> next weapon  ]
    9: ecodes.KEY_ESC,         # Start  -> menu
    8: ecodes.KEY_TAB,         # Select -> automap
}

AXIS_KEYS = {
    0: (ecodes.KEY_LEFT,  ecodes.KEY_RIGHT),  # turn left / right
    1: (ecodes.KEY_UP,    ecodes.KEY_DOWN),   # forward  / back
}


def main():
    js_path = sys.argv[1] if len(sys.argv) > 1 else "/dev/input/js0"

    all_keys = list(BUTTON_KEYS.values())
    for neg, pos in AXIS_KEYS.values():
        all_keys += [neg, pos]

    ui = UInput({ecodes.EV_KEY: all_keys}, name="td5-doom-controller")

    axis_state  = {}
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
                ui.write(ecodes.EV_KEY, key, 1 if pressed else 0)
                ui.syn()

            elif etype == JS_EVENT_AXIS:
                pair = AXIS_KEYS.get(number)
                if pair is None:
                    continue
                neg_key, pos_key = pair

                if value < -AXIS_THRESHOLD:
                    direction = -1
                elif value > AXIS_THRESHOLD:
                    direction = 1
                else:
                    direction = 0

                prev = axis_state.get(number, 0)
                if direction == prev:
                    continue
                axis_state[number] = direction

                if prev == -1:
                    ui.write(ecodes.EV_KEY, neg_key, 0); ui.syn()
                elif prev == 1:
                    ui.write(ecodes.EV_KEY, pos_key, 0); ui.syn()

                if direction == -1:
                    ui.write(ecodes.EV_KEY, neg_key, 1); ui.syn()
                elif direction == 1:
                    ui.write(ecodes.EV_KEY, pos_key, 1); ui.syn()


if __name__ == "__main__":
    main()
