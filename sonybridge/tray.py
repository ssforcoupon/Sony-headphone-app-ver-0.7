"""System-tray icon (pystray + Pillow). Both are optional: if they aren't installed the widget runs without a tray."""
from __future__ import annotations

from typing import Callable, Optional

from . import look

import importlib.util

pystray = None  # type: ignore   # imported only when the tray is started, so a problem there can't affect anything else
Image = ImageDraw = None  # type: ignore
# True when both packages are installed (checked without importing them)
AVAILABLE = all(importlib.util.find_spec(n) is not None for n in ("pystray", "PIL"))


def _load() -> bool:
    global pystray, Image, ImageDraw
    if pystray is not None:
        return True
    try:  # pragma: no cover - depends on the machine
        import pystray as _p
        from PIL import Image as _I, ImageDraw as _D
        pystray, Image, ImageDraw = _p, _I, _D
        return True
    except Exception:
        return False


def make_icon_image(connected: bool, size: int = 64):
    """The 'person with ring' icon on a blue (connected) or grey (not connected) disc, anti-aliased by drawing 4x big."""
    _load()
    big = size * 4
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, big - 3, big - 3), fill=look.hex_to_rgb("#3E91FF" if connected else "#8A8A8A") + (255,))
    s = big * 0.74 / 48
    ox = oy = big * 0.13
    white = (255, 255, 255, 255)
    for op in look.icon_ops("nc"):
        if op[0] == "circle":
            _, x, y, r, filled, w = op
            box = (ox + (x - r) * s, oy + (y - r) * s, ox + (x + r) * s, oy + (y + r) * s)
            if filled:
                d.ellipse(box, fill=white)
            else:
                d.ellipse(box, outline=white, width=max(2, int(w * s)))
        elif op[0] == "poly":
            d.polygon([(ox + x * s, oy + y * s) for x, y in op[1]], fill=white)
    return img.resize((size, size), Image.LANCZOS)


class Tray:
    """Runs pystray on its own thread. Callbacks run on that thread: the caller must hand them to the UI thread."""

    def __init__(self, on_toggle: Callable[[], None], on_settings: Callable[[], None], on_pause: Callable[[], None],
                 on_quit: Callable[[], None], is_visible: Callable[[], bool], is_paused: Callable[[], bool]):
        self.icon: Optional["pystray.Icon"] = None
        self._cb = (on_toggle, on_settings, on_pause, on_quit)
        self._vis, self._paused = is_visible, is_paused
        self._last = None

    def start(self, title: str, connected: bool) -> bool:
        if self.icon is not None:
            return True
        if not AVAILABLE or not _load():
            return False
        toggle, settings, pause, quit_ = self._cb
        menu = pystray.Menu(
            pystray.MenuItem(lambda item: "Hide widget" if self._vis() else "Show widget", lambda i, m=None: toggle(), default=True),
            pystray.MenuItem("Settings...", lambda i, m=None: settings()),
            pystray.MenuItem("Pause (free the link for the Sony app)", lambda i, m=None: pause(), checked=lambda item: self._paused()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda i, m=None: quit_()),
        )
        try:
            self.icon = pystray.Icon("SonyBridgeWindows", make_icon_image(connected), title, menu)
            self.icon.run_detached()
            self._last = (title, connected)
            return True
        except Exception:
            self.icon = None
            return False

    def update(self, title: str, connected: bool) -> None:
        if self.icon is None or self._last == (title, connected):
            return
        try:
            if self._last is None or self._last[1] != connected:
                self.icon.icon = make_icon_image(connected)
            self.icon.title = title[:120]
            self._last = (title, connected)
        except Exception:
            pass

    def stop(self) -> None:
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
            self.icon = None
