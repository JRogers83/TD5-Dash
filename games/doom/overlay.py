#!/usr/bin/env python3
"""TD5 Dash Doom mode — touch overlay for in-game menu access.

Displays an always-on-top GTK window with a ≡ icon in the top-right corner.
Touch input is read directly from the touchscreen evdev device (bypassing
X11/SDL2 event routing which SDL2 intercepts before GTK can see it).
"""
import os
import subprocess
import threading
import urllib.request
from urllib.error import URLError

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

MODE = os.environ.get("MODE", "single")
IS_SINGLE = (MODE == "single")
DISPLAY_ROTATION = int(os.environ.get("DISPLAY_ROTATION", "270"))
SCREEN_W, SCREEN_H = 1280, 400


def _raw_to_screen(tx, ty, max_x, max_y):
    """Map raw touch coords to screen coords accounting for display rotation."""
    if DISPLAY_ROTATION == 270:
        return int((1.0 - ty / max_y) * SCREEN_W), int((tx / max_x) * SCREEN_H)
    if DISPLAY_ROTATION == 90:
        return int((ty / max_y) * SCREEN_W), int((1.0 - tx / max_x) * SCREEN_H)
    if DISPLAY_ROTATION == 180:
        return int((1.0 - tx / max_x) * SCREEN_W), int((1.0 - ty / max_y) * SCREEN_H)
    return int((tx / max_x) * SCREEN_W), int((ty / max_y) * SCREEN_H)


def _find_touchscreen():
    try:
        import evdev
        from evdev import ecodes
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
                name = dev.name.lower()
                if ('goodix' in name or 'touchscreen' in name or 'touch' in name):
                    caps = dev.capabilities()
                    if ecodes.EV_ABS in caps:
                        abs_caps = dict(caps[ecodes.EV_ABS])
                        if ecodes.ABS_MT_POSITION_X in abs_caps:
                            return dev
            except Exception:
                continue
    except ImportError:
        pass
    return None


class Overlay(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_accept_focus(False)
        self.set_skip_taskbar_hint(True)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        self.menu_open = False
        self._build_compact()
        threading.Thread(target=self._touch_thread, daemon=True).start()

    # ── Touch detection via evdev ──────────────────────────────────────

    def _touch_thread(self):
        try:
            import evdev
            from evdev import ecodes
        except ImportError:
            return

        dev = _find_touchscreen()
        if dev is None:
            return

        abs_caps = dict(dev.capabilities().get(ecodes.EV_ABS, []))
        xi = abs_caps.get(ecodes.ABS_MT_POSITION_X)
        yi = abs_caps.get(ecodes.ABS_MT_POSITION_Y)
        if xi is None or yi is None:
            return
        max_x, max_y = xi.max, yi.max

        cur_x = cur_y = 0
        for event in dev.read_loop():
            if event.type == ecodes.EV_ABS:
                if event.code == ecodes.ABS_MT_POSITION_X:
                    cur_x = event.value
                elif event.code == ecodes.ABS_MT_POSITION_Y:
                    cur_y = event.value
            elif (event.type == ecodes.EV_KEY
                  and event.code == ecodes.BTN_TOUCH
                  and event.value == 1):
                sx, sy = _raw_to_screen(cur_x, cur_y, max_x, max_y)
                GLib.idle_add(self._handle_touch, sx, sy)

    def _handle_touch(self, sx, sy):
        if not self.menu_open:
            if sx >= 1210 and sy <= 70:
                self._toggle_menu()
        else:
            if sx >= 1030:
                buttons = [self._resume]
                if IS_SINGLE:
                    buttons += [self._save, self._load]
                buttons.append(self._quit)
                btn_idx = (sy - 18) // 72
                if 0 <= btn_idx < len(buttons):
                    buttons[btn_idx]()
        return False

    # ── GTK display ───────────────────────────────────────────────────

    def _toggle_menu(self):
        self.menu_open = not self.menu_open
        if self.menu_open:
            self._build_expanded()
        else:
            self._build_compact()

    def _build_compact(self):
        self.move(1210, 10)
        self.resize(60, 60)
        label = Gtk.Label()
        label.set_markup('<span size="36000" color="#e8e8e8">≡</span>')
        if self.get_child():
            self.remove(self.get_child())
        self.add(label)
        self.show_all()

    def _build_expanded(self):
        buttons = [("Resume", self._resume)]
        if IS_SINGLE:
            buttons += [("Save", self._save), ("Load", self._load)]
        buttons.append(("Quit", self._quit))

        panel_h = 24 + len(buttons) * 72
        self.move(1030, 10)
        self.resize(240, panel_h)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        for lbl, action in buttons:
            btn = Gtk.Button(label=lbl)
            btn.set_size_request(-1, 64)
            btn.connect("clicked", lambda _w, fn=action: fn())
            box.pack_start(btn, True, True, 0)
        if self.get_child():
            self.remove(self.get_child())
        self.add(box)
        self.show_all()

    # ── Actions ───────────────────────────────────────────────────────

    def _resume(self):
        self.menu_open = False
        self._build_compact()

    def _save(self):
        self._send_key("F2")
        self._resume()

    def _load(self):
        self._send_key("F3")
        self._resume()

    def _quit(self):
        def _stop():
            try:
                urllib.request.urlopen(
                    "http://localhost:8000/system/game-mode/stop",
                    data=b"", timeout=2,
                )
            except Exception:
                pass
        threading.Thread(target=_stop, daemon=True).start()
        Gtk.main_quit()

    def _send_key(self, key: str):
        subprocess.run(
            ["xdotool", "search", "--name", "Freedoom\\|LZDoom\\|Doom",
             "windowactivate", "--sync", "key", key],
            check=False,
        )


def main():
    win = Overlay()
    win.connect("destroy", Gtk.main_quit)
    Gtk.main()


if __name__ == "__main__":
    main()
