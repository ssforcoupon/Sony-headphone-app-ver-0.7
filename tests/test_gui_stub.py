"""Runs the GUI code against a stub tkinter (none is installed in the build sandbox): catches typos, wrong attribute
names and logic errors in OUR code. It cannot check real tkinter behaviour or looks."""
import pathlib, sys, time, types, tempfile
from unittest.mock import MagicMock
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

tk = types.ModuleType("tkinter")
for n in ("Tk", "Canvas", "Scale", "Checkbutton", "Menu", "Toplevel", "Listbox", "Text", "IntVar", "BooleanVar", "StringVar"):
    setattr(tk, n, MagicMock(name=n))
tk.TclError = type("TclError", (Exception,), {})
mb, ttk = types.ModuleType("tkinter.messagebox"), types.ModuleType("tkinter.ttk")
mb.askyesno = lambda *a, **k: True
for n in ("Frame", "Label", "Button", "Radiobutton", "Combobox", "Notebook", "Checkbutton", "Scale", "Separator"):
    setattr(ttk, n, MagicMock(name=n))
tk.messagebox, tk.ttk = mb, ttk
sys.modules.update({"tkinter": tk, "tkinter.messagebox": mb, "tkinter.ttk": ttk})
# IntVar/BooleanVar need real get/set behaviour for the logic under test
class Var:
    def __init__(self, value=0): self.v = value
    def get(self): return self.v
    def set(self, v): self.v = v
tk.IntVar = tk.BooleanVar = tk.StringVar = Var

import main
from sonybridge import gui

fails = 0
def check(name, ok):
    global fails
    fails += not ok
    print(("ok   " if ok else "FAIL ") + name)

for demo in ("v2", "v1"):
    h = main.build(demo)
    h.start()
    end = time.time() + 8
    while time.time() < end and not (h.snap.connected and h.snap.eq is not None):
        time.sleep(0.05)
    tk.Canvas.return_value.reset_mock()
    app = gui.WidgetApp(h)
    app.render()
    check(f"{demo}: widget renders", True)
    items = [str(c) for c in app.canvas.create_text.call_args_list]
    labels = " ".join(items)
    check(f"{demo}: shows device name", ("WH-1000XM4" if demo == "v1" else "WF-1000XM6") in labels)
    check(f"{demo}: Connected label drawn", "Connected" in labels)
    keys = [k for k, *_ in app._hits]
    check(f"{demo}: {len(keys)} buttons (Auto ambient {'hidden' if demo == 'v1' else 'shown'})",
          ("auto" in keys) == (demo == "v2") and len(keys) == (3 if demo == "v1" else 4))
    for tr, bl in ((0, "none"), (60, "acrylic"), (100, "classic")):
        app.set_look("transparency", tr); app.set_look("blur", bl); app.render()
    app.set_look("three_d", True); app.set_look("outline", True); app.set_look("frost", 0); app.render()
    check(f"{demo}: appearance options persist", h.cfg.get("blur") == "classic" and h.cfg.get("three_d") is True)
    # click handling (hit-testing, no per-item bindings; the "..." glyph was removed, right-click opens the menu)
    ev = MagicMock(); ev.x, ev.y = app.W - 10, 10
    real_menu, real_btn = app._menu, app._on_button
    app._menu = MagicMock(); app._click(ev)
    check(f"{demo}: no hidden click zone in the corner", not app._menu.called)
    check(f"{demo}: no '...' glyph drawn", "⋯" not in labels)
    key, cx, cy, rad = app._hits[-1]
    ev.x, ev.y = cx, cy
    app._on_button = MagicMock(); app._click(ev)
    check(f"{demo}: clicking a button circle fires it", app._on_button.call_args[0][0] == key)
    app._menu, app._on_button = real_menu, real_btn
    app.hide()
    check(f"{demo}: no hide without a tray icon", app.hidden is False)
    app._on_button(P_NC := gui.NcMode.AMBIENT)
    time.sleep(0.5)
    check(f"{demo}: ambient button sends command", h.snap.mode == gui.NcMode.AMBIENT)
    app.render()
    app._on_level(7)
    app._on_voice()
    st = gui.SettingsWindow(app)
    st.refresh()
    check(f"{demo}: settings window builds and refreshes", True)
    app._menu(MagicMock())
    h.stop()
sys.exit(1 if fails else 0)
