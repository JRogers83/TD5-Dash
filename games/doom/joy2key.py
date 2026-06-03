#!/usr/bin/env python3
"""
Joystick-to-keyboard mapper for chocolate-doom.
Reads a /dev/input/jsN device and injects keyboard events via uinput,
bypassing SDL joystick support entirely.

Usage: joy2key.py <js-device>  e.g. joy2key.py /dev/input/js0
"""
import sys
import os
import struct
import time
import fcntl

# JS event struct: time(u32), value(s16), type(u8), number(u8)
JS_EVENT_FMT = "IhBB"
JS_EVENT_SIZE = struct.calcsize(JS_EVENT_FMT)
JS_EVENT_BUTTON = 0x01
JS_EVENT_AXIS   = 0x02
JS_EVENT_INIT   = 0x80

AXIS_THRESHOLD = 16000

# Chocolate-doom default keyboard bindings (Linux keycodes)
# Button index -> keycode
BUTTON_KEYS = {
    1: 29,   # A      -> Left Ctrl  (fire)
    2: 57,   # B      -> Space      (use/open)
    4: 56,   # L      -> Left Alt   (strafe modifier)
    5: 42,   # R      -> Left Shift (run)
    3: 26,   # Y      -> [          (prev weapon)
    0: 27,   # X      -> ]          (next weapon)
    9: 1,    # Start  -> Escape     (menu)
    8: 15,   # Select -> Tab        (map)
}

# Axis index -> (negative keycode, positive keycode)
AXIS_KEYS = {
    0: (105, 106),  # Axis 0 left/right -> Left arrow / Right arrow (turn)
    1: (103, 108),  # Axis 1 up/down    -> Up arrow / Down arrow (forward/back)
}

# uinput constants
UINPUT_PATH = "/dev/uinput"
UI_SET_EVBIT  = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
EV_SYN = 0x00
EV_KEY = 0x01
KEY_MAX = 0x2ff

INPUT_EVENT_FMT = "llHHI"
INPUT_EVENT_SIZE = struct.calcsize(INPUT_EVENT_FMT)


def open_uinput(all_keys):
    fd = open(UINPUT_PATH, "wb")
    # Enable EV_KEY
    fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
    for key in all_keys:
        fcntl.ioctl(fd, UI_SET_KEYBIT, key)
    # Also enable SYN
    fcntl.ioctl(fd, UI_SET_EVBIT, EV_SYN)

    # uinput_user_dev struct: name(80s), id(4H), ff_effects_max(I), absmax(64I),
    # absmin(64I), absfuzz(64I), absflat(64I)
    uinput_dev = struct.pack("80sHHHHI" + "64I" * 4,
                             b"td5-doom-joy2key",
                             3, 1, 1, 1, 0,
                             *([0] * 256))
    fd.write(uinput_dev)
    fd.flush()
    fcntl.ioctl(fd, UI_DEV_CREATE)
    return fd


def send_key(fd, keycode, value):
    # value: 1=press, 0=release
    t = time.time()
    sec = int(t)
    usec = int((t - sec) * 1_000_000)
    event = struct.pack(INPUT_EVENT_FMT, sec, usec, EV_KEY, keycode, value)
    syn   = struct.pack(INPUT_EVENT_FMT, sec, usec, EV_SYN, 0, 0)
    fd.write(event)
    fd.write(syn)
    fd.flush()


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} /dev/input/jsN", file=sys.stderr)
        sys.exit(1)

    js_path = sys.argv[1]

    all_keys = list(BUTTON_KEYS.values())
    for neg, pos in AXIS_KEYS.values():
        all_keys += [neg, pos]

    uinput_fd = open_uinput(all_keys)
    time.sleep(0.1)  # let uinput device settle

    # Track state to avoid repeat events
    axis_state  = {}   # axis_index -> current direction (-1, 0, 1)
    button_state = {}  # button_index -> pressed bool

    try:
        with open(js_path, "rb") as js:
            while True:
                data = js.read(JS_EVENT_SIZE)
                if not data:
                    break
                _, value, etype, number = struct.unpack(JS_EVENT_FMT, data)
                etype &= ~JS_EVENT_INIT  # strip init flag

                if etype == JS_EVENT_BUTTON:
                    key = BUTTON_KEYS.get(number)
                    if key is None:
                        continue
                    pressed = bool(value)
                    if button_state.get(number) == pressed:
                        continue
                    button_state[number] = pressed
                    send_key(uinput_fd, key, 1 if pressed else 0)

                elif etype == JS_EVENT_AXIS:
                    keys = AXIS_KEYS.get(number)
                    if keys is None:
                        continue
                    neg_key, pos_key = keys
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

                    # Release whichever key was held
                    if prev == -1:
                        send_key(uinput_fd, neg_key, 0)
                    elif prev == 1:
                        send_key(uinput_fd, pos_key, 0)
                    # Press new direction
                    if direction == -1:
                        send_key(uinput_fd, neg_key, 1)
                    elif direction == 1:
                        send_key(uinput_fd, pos_key, 1)

    finally:
        fcntl.ioctl(uinput_fd, UI_DEV_DESTROY)
        uinput_fd.close()


if __name__ == "__main__":
    main()
