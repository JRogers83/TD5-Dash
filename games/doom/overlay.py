#!/usr/bin/env python3
"""TD5 Dash Doom mode — touch overlay for in-game menu access.

Lives as an always-on-top GTK window. Shows a translucent "≡" icon in the
top-right corner; tap to open Resume/Save/Load/Quit (Save and Load are
hidden in 2P modes because LZDoom does not support save/load in netplay
sessions).

Reads MODE from the environment; openbox (running in the same Doom-mode
process group) honours _NET_WM_STATE_ABOVE so the icon stays on top of the
LZDoom windows reliably.
"""
import os
import subprocess
import urllib.request

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk

MODE = os.environ.get("MODE", "single")
IS_SINGLE = (MODE == "single")


class Overlay(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_accept_focus(False)
        self.set_skip_taskbar_hint(True)
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.TOUCH_MASK
        )
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        self.menu_open = False
        self._build_compact()
        self.connect("button-press-event", self._on_click)
        self.connect("touch-event", self._on_click)

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
            buttons.append(("Save", self._save))
            buttons.append(("Load", self._load))
        buttons.append(("Quit", self._quit))

        panel_h = 24 + len(buttons) * 72
        self.move(1030, 10)
        self.resize(240, panel_h)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(8); box.set_margin_bottom(8)
        box.set_margin_start(8); box.set_margin_end(8)
        for label, action in buttons:
            btn = Gtk.Button(label=label)
            btn.set_size_request(-1, 64)
            btn.connect("clicked", lambda _w, fn=action: fn())
            box.pack_start(btn, True, True, 0)
        if self.get_child():
            self.remove(self.get_child())
        self.add(box)
        self.show_all()

    def _on_click(self, _w, _evt):
        self.menu_open = not self.menu_open
        if self.menu_open:
            self._build_expanded()
        else:
            self._build_compact()

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
        try:
            urllib.request.urlopen(
                "http://localhost:8000/system/game-mode/stop",
                data=b"",
                timeout=2,
            )
        except Exception:
            pass
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
