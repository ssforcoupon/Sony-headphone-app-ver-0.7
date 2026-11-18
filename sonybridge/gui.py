"""The desktop widget (a small always-on-top, draggable, rounded glass card styled like the Android widget) and the
settings window. tkinter only; Windows effects live in winfx.py, the look in look.py, the tray icon in tray.py."""
from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import messagebox, ttk
from typing import Callable, List, Optional

from . import look, tray as traymod, winfx
from . import protocol as P
from .controller import Headset
from .protocol import NcMode, Proto

KEY = "#010203"  # colour made see-through (shows the blurred glass); nothing else is ever drawn in it
LIGHT, DARK = look.LIGHT, look.DARK
BLUR_MODES = (("none", "None"), ("acrylic", "Acrylic (frosted glass)"), ("classic", "Classic blur"))


def system_is_dark() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


def autostart_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            winreg.QueryValueEx(k, "SonyBridgeWindows")
            return True
    except Exception:
        return False


def set_autostart(on: bool) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                        winreg.KEY_SET_VALUE) as k:
        if on:
            if getattr(sys, "frozen", False):
                cmd = '"%s"' % sys.executable
            else:
                import os
                exe = sys.executable.replace("python.exe", "pythonw.exe")
                main = os.path.abspath(sys.argv[0])
                cmd = '"%s" "%s"' % (exe, main)
            winreg.SetValueEx(k, "SonyBridgeWindows", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, "SonyBridgeWindows")
            except FileNotFoundError:
                pass


class WidgetApp:
    def __init__(self, headset: Headset):
        self.h = headset
        self.cfg = headset.cfg
        winfx.set_dpi_aware()
        self.root = tk.Tk()
        self.root.title("Sony headphones")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", bool(self.cfg.get("always_on_top", True)))
        try:
            self.k = max(1.0, float(self.root.winfo_fpixels("1i")) / 96.0)
        except Exception:
            self.k = 1.0
        self.W = int(look.CARD_W * self.k)
        self.pad = look.PAD_X * self.k
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cmd")
        self.ui_q: "queue.Queue[Callable]" = queue.Queue()
        self.dirty = True
        self.settings: Optional["SettingsWindow"] = None
        self.tray: Optional[traymod.Tray] = None
        self.hidden = False
        self.glass = False          # True when Windows accepted the see-through/blur effect
        self.dwm_round = False      # True when Windows 11 rounds the window corners itself (small fixed radius)
        self.radius = look.CARD_R * self.k
        self.hwnd = 0
        self._shape = (0, 0)
        self._height = 0
        self._drag = (0, 0)
        self._slider_job = None
        self._slider_user_until = 0.0
        self._rendering = False
        self._hits: List = []
        self.pal = self._palette()

        self.canvas = tk.Canvas(self.root, width=self.W, height=int(look.CARD_H_BASE * self.k), bg=self.pal["card"],
                                highlightthickness=0, bd=0)
        self.canvas.pack()
        self.level_var = tk.IntVar(value=10)
        self.scale = tk.Scale(self.canvas, from_=1, to=20, orient="horizontal", showvalue=False,
                              length=int(self.W - 2 * self.pad - 8), variable=self.level_var, command=self._on_level, bd=0,
                              highlightthickness=0, sliderlength=int(22 * self.k), width=int(14 * self.k), relief="flat")
        self.voice_var = tk.BooleanVar(value=False)
        self.voice_chk = tk.Checkbutton(self.canvas, text="Focus on voice", variable=self.voice_var, command=self._on_voice,
                                        bd=0, highlightthickness=0, anchor="w")
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-3>", self._menu)

        x, y = self.cfg.get("x"), self.cfg.get("y")
        if x is None or y is None:
            x = self.root.winfo_screenwidth() - self.W - 40
            y = 80
        self.root.geometry("+%d+%d" % (max(0, int(x)), max(0, int(y))))
        self.h.listeners.append(self._mark_dirty)
        self.root.after(30, self._startup)
        self._tick()

    def _startup(self) -> None:
        self.hwnd = winfx.toplevel_hwnd(self.root)
        self._no_taskbar()
        self.dwm_round = winfx.round_corners_dwm(self.hwnd)
        self.radius = (8 if self.dwm_round else look.CARD_R) * self.k
        from .controller import log
        log("corners: windows build %s, dwm rounding=%s, radius=%.0fpx" % (winfx.windows_build(), self.dwm_round, self.radius))
        self.apply_effects()
        if self.cfg.get("tray") or self.cfg.get("start_hidden"):
            self.set_tray(True)
        if self.cfg.get("start_hidden") and self.tray is not None and self.tray.icon is not None:
            self.hide()

    def _no_taskbar(self) -> None:
        """No taskbar button / Alt+Tab entry (the tray icon is the way back to the widget)."""
        self.root.withdraw()
        winfx.hide_from_taskbar(self.hwnd)
        if not self.hidden:
            self.root.deiconify()

    # ------------------------------------------------------------------ plumbing
    def _mark_dirty(self) -> None:
        self.dirty = True          # any thread; the Tk thread picks it up in _tick

    def _tick(self) -> None:
        try:
            while True:
                self.ui_q.get_nowait()()
        except queue.Empty:
            pass
        # never rebuild the canvas while the slider is being dragged (it would drop the drag)
        if self.dirty and self._now() >= self._slider_user_until:
            self.dirty = False
            if not self.hidden:
                self.render()
            if self.settings and self.settings.alive():
                self.settings.refresh()
            self._update_tray()
        if winfx.ensure_toolwindow(self.hwnd):
            from .controller import log
            log("taskbar button removed (Tk had reset the window style)")
        self.root.after(250, self._tick)

    def on_ui(self, fn: Callable) -> Callable:
        """Wraps a callback so it runs on the Tk thread (used by the tray icon's thread)."""
        return lambda *a: self.ui_q.put(fn)

    def run_cmd(self, fn: Callable, *args) -> None:
        def job():
            try:
                fn(*args)
            finally:
                self.dirty = True
        self.pool.submit(job)

    # ------------------------------------------------------------------ look / effects
    def _palette(self):
        theme = self.cfg.get("theme", "auto")
        dark = system_is_dark() if theme == "auto" else theme == "dark"
        return DARK if dark else LIGHT

    def set_look(self, key: str, value, save: bool = True) -> None:
        with self.cfg.lock:
            self.cfg.data[key] = value
        if save:
            self.cfg.save()
        self.apply_effects()
        self.dirty = True

    def apply_effects(self) -> None:
        """Transparency + blur. See-through areas are painted in KEY, which Windows makes transparent so the blurred
        'glass' underneath shows; everything else (buttons, text) stays opaque. If Windows refuses the effect, the card is
        painted solid and the transparency slider fades the whole widget instead."""
        cfg = self.cfg
        self.pal = self._palette()
        transp = max(0, min(100, int(cfg.get("transparency", 40))))
        blur = cfg.get("blur", "acrylic")
        want_glass = blur != "none" or transp > 0
        ok = self._blur(want_glass)
        self.glass = ok
        try:
            self.root.attributes("-transparentcolor", KEY if ok else "")
            self.root.attributes("-alpha", 1.0 if (ok or not want_glass) else max(0.4, 1.0 - transp * 0.006))
        except tk.TclError:
            pass
        self.canvas.configure(bg=KEY if ok else self.pal["card"])
        self._shape = (0, 0)   # force the rounded shape to be re-applied
        self.dirty = True      # ...which happens in the next render

    def _blur(self, want_glass: bool) -> bool:
        cfg = self.cfg
        transp = max(0, min(100, int(cfg.get("transparency", 40))))
        blur = cfg.get("blur", "acrylic")
        if not self.hwnd:
            return False
        if want_glass:
            return winfx.apply_blur(self.hwnd, blur if blur != "none" else "tint", look.hex_to_rgb(self.pal["card"]),
                                    round((100 - transp) * 2.55))
        winfx.apply_blur(self.hwnd, "off")
        return False

    def _apply_shape(self, height: int) -> None:
        """Clips the window to the rounded card. The glass is re-applied afterwards: Windows computes the blurred area when
        the effect is set, so setting the rounded region first and the effect second keeps the blur inside the corners."""
        if self._shape != (self.W, height) and self.hwnd:
            self._shape = (self.W, height)
            from .controller import log
            if self.dwm_round:
                winfx.clear_region(self.hwnd)       # Windows rounds it (and the glass) itself
                ok = True
            else:
                ok = winfx.round_window(self.hwnd, self.W, height, int(self.radius))
            if self.glass or self.cfg.get("blur", "acrylic") != "none" or int(self.cfg.get("transparency", 40)) > 0:
                self.glass = self._blur(True) or self.glass
            log("window shape %dx%d radius %d: region=%s glass=%s blur=%s" % (
                self.W, height, int(self.radius), ok, self.glass, self.cfg.get("blur", "acrylic")))

    # ------------------------------------------------------------------ drawing
    def render(self) -> None:
        s, pal, cv, k = self.h.snap, self.pal, self.canvas, self.k
        cfg = self.cfg
        cv.delete("all")
        self._hits = []
        auto_vis = not self.h.auto_unsupported
        buttons = [("nc", NcMode.NOISE_CANCELLING), ("ambient", NcMode.AMBIENT)]
        if auto_vis:
            buttons.append(("auto", "auto"))
        buttons.append(("off", NcMode.OFF))
        ambient_on = s.mode == NcMode.AMBIENT and s.shown_connected()
        extra = (70 if ambient_on else 0)
        H = int((look.CARD_H_BASE + extra) * k)
        self._height = H
        cv.configure(height=H, width=self.W)
        self.root.geometry("%dx%d" % (self.W, H))
        self._apply_shape(H)
        W, r, f = self.W, self.radius, max(0, min(100, int(cfg.get("frost", 40)))) / 100.0
        up = s.shown_connected()

        # frosted edge / highlight (Android: bg_frost)
        if f > 0:
            top = "#FFFFFF"
            if not self.glass:   # solid card: a soft highlight band fading down from the top edge
                n = int(20 * k)
                for i in range(n):
                    amt = 0.30 * f * (1 - i / n)
                    if amt < 0.01:
                        break
                    ins = look.corner_inset(i + 1, r)
                    cv.create_line(ins + 2, i + 1, W - ins - 2, i + 1, fill=look.blend(pal["card"], top, amt))
            rim = look.blend(pal["card"], top, 0.2 + 0.7 * f)
            cv.create_polygon(look.rounded_poly(1, 1, W - 1, H - 1, r), smooth=False, fill="", outline=rim, width=max(1, int(k)))
        if not self.glass:   # solid card: a thin border so the rounded edge is visible on any background
            cv.create_polygon(look.rounded_poly(1, 1, W - 1, H - 1, r), smooth=False, fill="", outline=pal["line"],
                              width=max(1, int(k)))
        if cfg.get("outline"):
            cv.create_polygon(look.rounded_poly(1, 1, W - 1, H - 1, r), smooth=False, fill="", outline="#000000", width=max(1, int(k)))

        # header
        px = self.pad
        cv.create_text(px, 26 * k, text=s.title if s.name else "Sony headphones", anchor="w", fill=pal["text"],
                       font=("Segoe UI", 13, "bold"))
        batt = s.battery_text() if up else ""
        cv.create_text(px, 48 * k, text=batt or " ", anchor="w", fill=pal["text"], font=("Segoe UI", 10))

        # buttons joined by lines (Android: hit areas of 60dp with 52dp circles, 4dp lines between)
        spans = look.button_row(len(buttons))
        cy = (look.ROW_TOP + look.HIT / 2) * k
        for i in range(len(buttons) - 1):
            cv.create_line(spans[i][1] * k, cy, spans[i + 1][0] * k, cy, fill=pal["line"], width=max(2, int(look.LINE_H * k)),
                           capstyle="round")
        three_d = bool(cfg.get("three_d"))
        rad = look.CIRCLE / 2 * k
        for i, (icon, key) in enumerate(buttons):
            cx = (spans[i][0] + spans[i][1]) / 2 * k
            active = (s.auto_on and s.mode is not None) if key == "auto" else (s.mode == key)
            state = "disabled" if not up else "active" if active else "off"
            tag = "btn%d" % i
            look.draw_button(cv, cx, cy, rad, state, three_d, pal, k, tag)
            ic = pal["icon"] if state != "disabled" else look.blend(pal["disabled"], pal["icon"], 0.45)
            look.draw_icon(cv, icon, cx, cy - (1.5 * k if three_d and state != "disabled" else 0),
                           (look.CIRCLE - 2 * look.ICON_PAD) * k, ic, tag)
            self._hits.append((key, cx, cy, rad))

        # ambient controls
        y0 = (look.ROW_TOP + look.HIT + 8) * k
        if ambient_on:
            cv.create_text(px, y0 + 8 * k, text="Ambient level  %d" % self.level_var.get(), anchor="w", fill=pal["text"],
                           font=("Segoe UI", 10, "bold"), tags="ambctl")
            self.scale.configure(bg=pal["card"], troughcolor=pal["circle"], activebackground=pal["accent"], fg=pal["text"])
            self.voice_chk.configure(bg=pal["card"], fg=pal["text"], activebackground=pal["card"],
                                     activeforeground=pal["text"], selectcolor=pal["circle"], font=("Segoe UI", 10))
            self._rendering = True
            try:
                if self._now() > self._slider_user_until:
                    self.level_var.set(s.level)
                self.voice_var.set(s.voice)
            finally:
                self._rendering = False
            cv.create_window(px - 2 * k, y0 + 36 * k, window=self.scale, anchor="w", tags="ambctl")
            if s.level > P.MIN_VOICE_FOCUS_LEVEL:
                cv.create_window(px - 2 * k, y0 + 62 * k, window=self.voice_chk, anchor="w", tags="ambctl")

        # centred connection line: only ever "Connected" or "Disconnected" (details are in Settings > Status and the log)
        cv.create_text(W / 2, H - 20 * k, text=s.connection_label(), fill=pal["text"] if up else pal["bad"],
                       font=("Segoe UI", 10, "bold"), anchor="center")

    @staticmethod
    def _status_line(s) -> str:
        text = s.status or ""
        if not s.connected and s.error and not s.paused:
            text = s.error if s.error.startswith("Couldn't") else "Can't open control link: " + s.error
        label = s.connection_label()
        return label if (not text or text == label) else label + " - " + text

    @staticmethod
    def _clip(t: str, n: int) -> str:
        t = t.replace("\n", " ")
        return t if len(t) <= n else t[: n - 1] + "…"

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    # ------------------------------------------------------------------ interaction
    def _on_button(self, key) -> None:
        s = self.h.snap
        if not s.connected:
            self.h.reconnect_now()      # nothing to send to; try to get the control link back right away
            return
        if key == "auto":
            self.run_cmd(self.h.set_auto, None, None)
        else:
            self.run_cmd(self.h.apply_mode, key)

    def _on_level(self, value) -> None:
        if self._rendering:
            return
        self._slider_user_until = self._now() + 1.2
        lvl = int(float(value))
        if self._slider_job:
            self.root.after_cancel(self._slider_job)
        # redraw the label immediately, send after a short pause so dragging doesn't flood the headphones
        self.render_label_only(lvl)
        self._slider_job = self.root.after(150, lambda: self.run_cmd(self.h.set_ambient, lvl, None))

    def render_label_only(self, lvl: int) -> None:
        for item in self.canvas.find_withtag("ambctl"):
            if self.canvas.type(item) == "text":
                self.canvas.itemconfigure(item, text="Ambient level  %d" % lvl)
                break

    def _on_voice(self) -> None:
        self.run_cmd(self.h.set_ambient, None, bool(self.voice_var.get()))

    def _press(self, e) -> None:
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())
        self._down = (e.x_root, e.y_root)

    def _motion(self, e) -> None:
        self.root.geometry("+%d+%d" % (e.x_root - self._drag[0], e.y_root - self._drag[1]))

    def _release(self, e) -> None:
        moved = abs(e.x_root - self._down[0]) + abs(e.y_root - self._down[1]) if getattr(self, "_down", None) else 99
        if moved < 6:
            self._click(e)             # a click, not a drag
        else:
            self.cfg.set("x", self.root.winfo_x())
            self.cfg.set("y", self.root.winfo_y())

    def _click(self, e) -> None:
        k = self.k
        for key, cx, cy, rad in self._hits:
            if (e.x - cx) ** 2 + (e.y - cy) ** 2 <= (rad * 1.15) ** 2:
                self._on_button(key)
                return

    # ------------------------------------------------------------------ tray
    def set_tray(self, on: bool) -> bool:
        """Shows/removes the tray icon. Returns False when the tray packages aren't installed."""
        if not on:
            if self.tray:
                self.tray.stop()
                self.tray = None
            if self.hidden:
                self.show()
            return True
        if not traymod.AVAILABLE:
            messagebox.showinfo("Tray icon", "The tray icon needs the 'pystray' and 'pillow' packages.\n"
                                "Run:  pip install pystray pillow")
            return False
        if self.tray is None:
            self.tray = traymod.Tray(self.on_ui(self.toggle_hidden), self.on_ui(self.open_settings),
                                     self.on_ui(lambda: self.h.set_paused(not bool(self.cfg.get("paused")))),
                                     self.on_ui(self.quit), lambda: not self.hidden, lambda: bool(self.cfg.get("paused")))
            s = self.h.snap
            self.tray.start(self._tray_title(), s.shown_connected())
        return self.tray.icon is not None

    def _tray_title(self) -> str:
        s = self.h.snap
        return ("%s - %s" % (s.title if s.name else "Sony headphones", s.connection_label()))

    def _update_tray(self) -> None:
        if self.tray is not None:
            self.tray.update(self._tray_title(), self.h.snap.shown_connected())

    def hide(self) -> None:
        if self.tray is None or self.tray.icon is None:
            return          # never hide without a tray icon: there would be no way to bring it back
        self.hidden = True
        self.root.withdraw()

    def show(self) -> None:
        self.hidden = False
        self.root.withdraw()
        winfx.hide_from_taskbar(self.hwnd)
        self.root.deiconify()
        self.root.attributes("-topmost", bool(self.cfg.get("always_on_top", True)))
        self.root.lift()
        self.dirty = True

    def toggle_hidden(self) -> None:
        self.show() if self.hidden else self.hide()

    # ------------------------------------------------------------------ menu
    def _menu(self, e) -> None:
        h, m = self.h, tk.Menu(self.root, tearoff=0)
        m.add_command(label="Settings...", command=self.open_settings)
        m.add_command(label="Appearance...", command=lambda: self.open_settings("appearance"))
        m.add_command(label="Refresh", command=lambda: self.run_cmd(h.refresh))
        m.add_command(label="Open log file", command=self.open_log)
        m.add_separator()
        m.add_command(label="Choose headphones...", command=self.choose_device)
        follow = tk.BooleanVar(value=bool(self.cfg.get("follow", True)))
        m.add_checkbutton(label="Follow connected headphones", variable=follow, command=lambda: h.set_follow(follow.get()))
        paused = bool(self.cfg.get("paused"))
        m.add_command(label="Resume connection" if paused else "Pause (free the link for the Sony app)",
                      command=lambda: h.set_paused(not paused))
        m.add_separator()
        if self.tray is not None and self.tray.icon is not None:
            m.add_command(label="Minimise to tray", command=self.hide)
        else:
            m.add_command(label="Minimise to tray (turns on the tray icon)", command=lambda: (self.set_tray(True) and self.hide()))
        top = tk.BooleanVar(value=bool(self.cfg.get("always_on_top", True)))
        m.add_checkbutton(label="Always on top", variable=top, command=lambda: self._set_top(top.get()))
        if sys.platform == "win32":
            auto = tk.BooleanVar(value=autostart_enabled())
            m.add_checkbutton(label="Start with Windows", variable=auto, command=lambda: set_autostart(auto.get()))
        m.add_separator()
        m.add_command(label="Quit", command=self.quit)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def open_log(self) -> None:
        import os
        from .controller import data_dir, log
        log("log opened by user")
        try:
            os.startfile(str(data_dir() / "log.txt"))      # type: ignore[attr-defined]
        except Exception as ex:
            messagebox.showinfo("Log file", "Log is at: %s\n(%s)" % (data_dir() / "log.txt", ex))

    def _set_top(self, on: bool) -> None:
        self.cfg.set("always_on_top", on)
        self.root.attributes("-topmost", on)

    def open_settings(self, tab: Optional[str] = None) -> None:
        if self.hidden:
            self.show()
        if self.settings and self.settings.alive():
            self.settings.win.lift()
        else:
            self.settings = SettingsWindow(self)
        if tab == "appearance":
            self.settings.select_appearance()

    def choose_device(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Choose headphones")
        win.attributes("-topmost", True)
        win.geometry("340x260")
        ttk.Label(win, text="Paired Sony headphones (double-click to use)").pack(anchor="w", padx=10, pady=(10, 4))
        box = tk.Listbox(win, height=8, activestyle="none")
        box.pack(fill="both", expand=True, padx=10)
        info = ttk.Label(win, text="Looking...")
        info.pack(anchor="w", padx=10, pady=6)
        devs: List = []

        def load():
            d = self.h.devices()
            def show():
                devs.extend(d)
                for x in d:
                    box.insert("end", "%s%s" % (x.name or x.id, "   (connected)" if x.connected else ""))
                info.configure(text="" if d else "Nothing found. Pair the headphones in Windows Bluetooth settings.")
            win.after(0, show)
        threading.Thread(target=load, daemon=True).start()

        def pick(_e=None):
            sel = box.curselection()
            if sel:
                self.h.select_device(devs[sel[0]])
                win.destroy()
        box.bind("<Double-Button-1>", pick)
        ttk.Button(win, text="Use selected", command=pick).pack(pady=(0, 10))

    def quit(self) -> None:
        if self.tray:
            self.tray.stop()
        self.h.stop()
        self.root.destroy()

    def run(self) -> None:
        self.h.start()
        self.root.mainloop()


class SettingsWindow:
    """Listening mode, sound quality (codec / low latency) and equalizer."""

    def __init__(self, app: WidgetApp):
        self.app, self.h = app, app.h
        self.win = tk.Toplevel(app.root)
        self.win.title("Sony headphones - settings")
        self.win.attributes("-topmost", True)
        self.win.geometry("520x470")
        self.loading = False
        self._eq_job = None
        self._eq_user_until = 0.0
        self._suppress = False
        nb = ttk.Notebook(self.win)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.nb = nb
        self.tab_look, self.tab_listen, self.tab_conn, self.tab_eq, self.tab_diag = (ttk.Frame(nb, padding=12) for _ in range(5))
        nb.add(self.tab_look, text="Appearance")
        nb.add(self.tab_listen, text="Listening mode")
        nb.add(self.tab_conn, text="Sound quality")
        nb.add(self.tab_eq, text="Equalizer")
        nb.add(self.tab_diag, text="Status")

        self._build_appearance()

        # listening mode
        self.listen_msg = ttk.Label(self.tab_listen, wraplength=460, justify="left")
        self.listen_msg.pack(anchor="w", pady=(0, 8))
        self.listen_var = tk.StringVar(value="std")
        self.listen_radios = [
            ttk.Radiobutton(self.tab_listen, text="Standard", value="std", variable=self.listen_var, command=self._listen_changed),
            ttk.Radiobutton(self.tab_listen, text="Background music", value="bgm", variable=self.listen_var, command=self._listen_changed),
        ]
        for r in self.listen_radios:
            r.pack(anchor="w", pady=2)
        ttk.Label(self.tab_listen, text="Room size (Background music)").pack(anchor="w", pady=(14, 2))
        self.room_var = tk.IntVar(value=1)
        self.room_radios = []
        for r in P.RoomSize:
            b = ttk.Radiobutton(self.tab_listen, text=r.title, value=r.id, variable=self.room_var, command=self._room_changed)
            b.pack(anchor="w", padx=12)
            self.room_radios.append(b)

        # sound quality
        self.codec_lbl = ttk.Label(self.tab_conn, font=("Segoe UI", 11, "bold"))
        self.codec_lbl.pack(anchor="w")
        ttk.Button(self.tab_conn, text="Refresh codec", command=lambda: app.run_cmd(self.h.refresh)).pack(anchor="w", pady=(4, 10))
        self.conn_msg = ttk.Label(self.tab_conn, wraplength=460, justify="left")
        self.conn_msg.pack(anchor="w")
        self.conn_var = tk.IntVar(value=-1)
        self.conn_host = ttk.Frame(self.tab_conn)
        self.conn_host.pack(anchor="w", fill="x", pady=6)
        ttk.Label(self.tab_conn, wraplength=460, justify="left",
                  text="This is a setting on the headphones (the same one as Sony's own app). Changing it can make them "
                       "disconnect for a moment and reconnect.").pack(anchor="w", pady=(8, 0))

        # equalizer
        top = ttk.Frame(self.tab_eq)
        top.pack(fill="x")
        ttk.Label(top, text="Preset").pack(side="left")
        self.eq_combo = ttk.Combobox(top, state="readonly", width=18, values=[n for _, n in P.EQ_PRESETS])
        self.eq_combo.pack(side="left", padx=8)
        self.eq_combo.bind("<<ComboboxSelected>>", self._eq_preset)
        ttk.Button(top, text="Reset to flat", command=self._eq_flat).pack(side="left")
        self.eq_msg = ttk.Label(self.tab_eq, wraplength=460, justify="left")
        self.eq_msg.pack(anchor="w", pady=6)
        self.eq_frame = ttk.Frame(self.tab_eq)
        self.eq_frame.pack(fill="both", expand=True)
        self.eq_scales: List[tk.Scale] = []
        self.eq_labels: List[ttk.Label] = []
        self._eq_kind: Optional[P.EqKind] = None

        # status
        self.diag = tk.Text(self.tab_diag, height=14, wrap="word", state="disabled")
        self.diag.pack(fill="both", expand=True)
        btns = ttk.Frame(self.tab_diag)
        btns.pack(anchor="w", pady=6)
        ttk.Button(btns, text="Retry feature detection", command=self.h.retry_probes).pack(side="left")
        ttk.Button(btns, text="Open log file", command=self.app.open_log).pack(side="left", padx=8)
        self.refresh()

    # ---------------------------------------------------------------- appearance + tray
    def select_appearance(self) -> None:
        self.nb.select(self.tab_look)

    def _build_appearance(self) -> None:
        app, cfg, t = self.app, self.app.cfg, self.tab_look
        self._look_vars = {}

        def slider(label: str, key: str, default: int) -> None:
            row = ttk.Frame(t)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, width=14).pack(side="left")
            var = tk.IntVar(value=int(cfg.get(key, default)))
            val = ttk.Label(row, text="%d %%" % var.get(), width=6)

            def moved(v, key=key, var=var, val=val):
                n = int(float(v))
                val.configure(text="%d %%" % n)
                app.set_look(key, n, save=False)       # live preview on the widget

            sc = ttk.Scale(row, from_=0, to=100, orient="horizontal", command=moved)
            sc.set(var.get())
            sc.pack(side="left", fill="x", expand=True, padx=6)
            sc.bind("<ButtonRelease-1>", lambda e: cfg.save())
            val.pack(side="left")

        slider("Transparency", "transparency", 40)
        slider("Frosted edge", "frost", 40)

        ttk.Label(t, text="Blur behind the widget").pack(anchor="w", pady=(10, 2))
        self.blur_var = tk.StringVar(value=cfg.get("blur", "acrylic"))
        for key, title in BLUR_MODES:
            ttk.Radiobutton(t, text=title, value=key, variable=self.blur_var,
                            command=lambda: app.set_look("blur", self.blur_var.get())).pack(anchor="w", padx=12)
        ttk.Label(t, wraplength=470, justify="left",
                  text="Blur uses a Windows feature that isn't officially documented. If the widget turns black or shows no "
                       "effect, choose None (the card is then drawn solid and Transparency fades the whole widget).").pack(
            anchor="w", pady=(4, 8))

        def check(label: str, key: str, default: bool, after=None):
            var = tk.BooleanVar(value=bool(cfg.get(key, default)))

            def changed():
                app.set_look(key, bool(var.get()))
                if after:
                    after(bool(var.get()))
            ttk.Checkbutton(t, text=label, variable=var, command=changed).pack(anchor="w")
            return var

        check("3D buttons", "three_d", False)
        check("Thin black outline", "outline", False)
        row = ttk.Frame(t)
        row.pack(fill="x", pady=(8, 8))
        ttk.Label(row, text="Colours").pack(side="left")
        self.theme_combo = ttk.Combobox(row, state="readonly", width=14, values=["Follow Windows", "Light", "Dark"])
        self.theme_combo.set({"auto": "Follow Windows", "light": "Light", "dark": "Dark"}.get(cfg.get("theme", "auto"), "Follow Windows"))
        self.theme_combo.pack(side="left", padx=8)
        self.theme_combo.bind("<<ComboboxSelected>>", lambda e: app.set_look(
            "theme", {"Follow Windows": "auto", "Light": "light", "Dark": "dark"}[self.theme_combo.get()]))

        ttk.Separator(t).pack(fill="x", pady=6)
        check("Always on top", "always_on_top", True, after=app._set_top)
        if sys.platform == "win32":
            auto = tk.BooleanVar(value=autostart_enabled())
            ttk.Checkbutton(t, text="Start with Windows", variable=auto, command=lambda: set_autostart(auto.get())).pack(anchor="w")
        self.tray_var = check("Show tray icon", "tray", False, after=self._tray_toggled)
        self.hidden_var = check("Start minimised in the tray", "start_hidden", False, after=self._hidden_toggled)
        if not traymod.AVAILABLE:
            ttk.Label(t, foreground="#B00020", text="Tray needs:  pip install pystray pillow").pack(anchor="w")

    def _tray_toggled(self, on: bool) -> None:
        if not self.app.set_tray(on):
            self.tray_var.set(False)
            self.app.cfg.set("tray", False)
        elif not on and self.hidden_var.get():
            self.hidden_var.set(False)
            self.app.cfg.set("start_hidden", False)

    def _hidden_toggled(self, on: bool) -> None:
        if on and not self.tray_var.get():       # starting minimised needs the tray icon, otherwise you couldn't reopen it
            self.tray_var.set(True)
            self.app.cfg.set("tray", True)
            if not self.app.set_tray(True):
                self.tray_var.set(False)
                self.hidden_var.set(False)
                self.app.cfg.set("tray", False)
                self.app.cfg.set("start_hidden", False)

    def alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    # -- listening mode
    def _listen_changed(self) -> None:
        self.app.run_cmd(self.h.set_listening, self.listen_var.get() == "bgm", self.room_var.get())

    def _room_changed(self) -> None:
        self.app.run_cmd(self.h.set_listening, True, self.room_var.get())

    # -- sound quality
    def _conn_changed(self) -> None:
        mode = self.conn_var.get()
        m = P.ConnMode.of(mode)
        if m and messagebox.askyesno(m.title, "The headphones may disconnect for a moment while this is applied.\nChange now?",
                                     parent=self.win):
            self.app.run_cmd(self.h.set_conn_mode, mode)
        else:
            self.refresh()

    # -- equalizer
    def _build_eq(self, kind: P.EqKind) -> None:
        for w in self.eq_frame.winfo_children():
            w.destroy()
        self.eq_scales, self.eq_labels = [], []
        labels = (["Clear bass"] if kind.has_clear_bass else []) + list(kind.labels)
        for i, name in enumerate(labels):
            col = ttk.Frame(self.eq_frame)
            col.pack(side="left", fill="y", expand=True)
            v = ttk.Label(col, text="0", width=3, anchor="center")
            v.pack()
            sc = tk.Scale(col, from_=kind.max, to=kind.min, orient="vertical", showvalue=False, length=210, width=16,
                          command=lambda val, idx=i: self._eq_moved(idx, val))
            sc.pack(expand=True)
            ttk.Label(col, text=name, anchor="center").pack()
            self.eq_scales.append(sc)
            self.eq_labels.append(v)
        self._eq_kind = kind

    def _eq_moved(self, idx: int, val) -> None:
        self.eq_labels[idx].configure(text=str(int(float(val))))
        if self._suppress:
            return
        import time
        self._eq_user_until = time.monotonic() + 1.0
        if self._eq_job:
            self.win.after_cancel(self._eq_job)
        self._eq_job = self.win.after(200, self._eq_send)

    def _eq_values(self):
        vals = [int(s.get()) for s in self.eq_scales]
        kind = self._eq_kind
        return (vals[0], vals[1:]) if kind and kind.has_clear_bass else (0, vals)

    def _eq_send(self) -> None:
        cb, bands = self._eq_values()
        self.app.run_cmd(self.h.set_eq_custom, cb, bands)

    def _eq_flat(self) -> None:
        for s in self.eq_scales:
            s.set(0)
        self._eq_send()

    def _eq_preset(self, _e=None) -> None:
        idx = self.eq_combo.current()
        if idx >= 0:
            self.app.run_cmd(self.h.set_eq_preset, P.EQ_PRESETS[idx][0])

    # -- refresh from the snapshot
    def refresh(self) -> None:
        s, h = self.h.snap, self.h
        online = s.connected
        # listening mode
        if h.listening_unsupported or (s.listen is None and not online):
            self.listen_msg.configure(
                text=("These headphones don't have a Standard / Background music setting." if h.listening_unsupported
                      else "Connect the headphones to read this setting."))
            for w in self.listen_radios + self.room_radios:
                w.state(["disabled"])
        else:
            l = s.listen
            if l is None:
                self.listen_msg.configure(text="Reading...")
            else:
                self.listen_msg.configure(text="" if l.available else "Background music isn't available right now "
                                                                      "(another feature is using the audio).")
                self.listen_var.set("bgm" if l.bgm else "std")
                self.room_var.set(l.room)
            for w in self.listen_radios:
                w.state(["!disabled"] if online and l else ["disabled"])
            for w in self.room_radios:
                w.state(["!disabled"] if online and l and l.bgm else ["disabled"])
        # sound quality
        self.codec_lbl.configure(text="Codec in use: " + (P.codec_name(s.codec) if s.codec >= 0 else "unknown"))
        for w in self.conn_host.winfo_children():
            w.destroy()
        if s.conn and s.conn.modes:
            self.conn_msg.configure(text="Bluetooth connection quality")
            self.conn_var.set(s.conn.mode)
            for mid in sorted(s.conn.modes):
                m = P.ConnMode.of(mid)
                if not m:
                    continue
                f = ttk.Frame(self.conn_host)
                f.pack(anchor="w", fill="x", pady=3)
                rb = ttk.Radiobutton(f, text=m.title, value=mid, variable=self.conn_var, command=self._conn_changed)
                rb.pack(anchor="w")
                if not online:
                    rb.state(["disabled"])
                ttk.Label(f, text=m.detail, wraplength=440, justify="left", foreground="#555555").pack(anchor="w", padx=22)
        else:
            self.conn_msg.configure(text="Reading..." if online else "Connect the headphones to read this setting.")
        # equalizer
        import time
        if s.eq is None:
            self.eq_msg.configure(text="This headphone didn't report an equalizer." if h.eq_unsupported else
                                  ("Reading..." if online else "Connect the headphones to read this setting."))
        else:
            self.eq_msg.configure(text="")
            if self._eq_kind != s.eq.kind:
                self._build_eq(s.eq.kind)
            if time.monotonic() > self._eq_user_until:
                self._suppress = True
                vals = ([s.eq.clear_bass] if s.eq.kind.has_clear_bass else []) + list(s.eq.bands)
                for sc, v in zip(self.eq_scales, vals):
                    sc.set(v)
                for lb, v in zip(self.eq_labels, vals):
                    lb.configure(text=str(v))
                self._suppress = False
            try:
                self.eq_combo.current([i for i, _ in P.EQ_PRESETS].index(s.eq.preset))
            except ValueError:
                self.eq_combo.set(P.eq_preset_name(s.eq.preset))
        # status
        lines = ["Headphones: %s" % (s.name or "-"), "Connection: %s" % s.connection_label(),
                 "Protocol: %s" % (s.proto.name if s.proto else "unknown"), "Status: %s" % (s.status or "-"),
                 "Last error: %s" % (s.error or "-"), "",
                 "If the headphones stay disconnected: close Sony's own app (they allow one control connection) and "
                 "use 'Pause' in the widget menu if you need to hand the link back to it."]
        self.diag.configure(state="normal")
        self.diag.delete("1.0", "end")
        self.diag.insert("1.0", "\n".join(lines))
        self.diag.configure(state="disabled")
