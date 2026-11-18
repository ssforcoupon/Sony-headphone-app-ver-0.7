"""Draws the widget with Pillow instead of tkinter, to look at the layout without Windows.
   python tools/preview.py out.png   (needs: pip install pillow). Glass is simulated over a photo-like gradient."""
import pathlib, sys, time, types
from unittest.mock import MagicMock
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from PIL import Image, ImageDraw, ImageFont

SC = 3


class PCanvas:
    def __init__(self, *a, **k): self.w, self.h = 330, 160; self.ops = []; self.bg = "#fff"
    def configure(self, **k):
        self.w = k.get("width", self.w); self.h = k.get("height", self.h); self.bg = k.get("bg", self.bg)
    def pack(self, *a, **k): pass
    def bind(self, *a, **k): pass
    def tag_bind(self, *a, **k): pass
    def delete(self, *a): self.ops = []
    def find_withtag(self, *a): return []
    def create_window(self, *a, **k): self.ops.append(("win", a))
    def _add(self, *a, **k): self.ops.append(a)
    def create_oval(self, *a, **k): self.ops.append(("oval", a, k))
    def create_line(self, *a, **k): self.ops.append(("line", a, k))
    def create_polygon(self, *a, **k): self.ops.append(("poly", a, k))
    def create_rectangle(self, *a, **k): self.ops.append(("rect", a, k))
    def create_arc(self, *a, **k): self.ops.append(("arc", a, k))
    def create_text(self, *a, **k): self.ops.append(("text", a, k))


tk = types.ModuleType("tkinter")
for n in ("Tk", "Scale", "Checkbutton", "Menu", "Toplevel", "Listbox", "Text"):
    setattr(tk, n, MagicMock(name=n))
tk.Canvas = PCanvas
tk.TclError = type("TclError", (Exception,), {})
class Var:
    def __init__(self, value=0): self.v = value
    def get(self): return self.v
    def set(self, v): self.v = v
tk.IntVar = tk.BooleanVar = tk.StringVar = Var
mb, ttk = types.ModuleType("tkinter.messagebox"), types.ModuleType("tkinter.ttk")
tk.messagebox, tk.ttk = mb, ttk
sys.modules.update({"tkinter": tk, "tkinter.messagebox": mb, "tkinter.ttk": ttk})
import main
from sonybridge import gui


def paint(cv, fill_bg):
    im = Image.new("RGB", (int(cv.w * SC), int(cv.h * SC)), fill_bg)
    d = ImageDraw.Draw(im)
    def col(c):
        return None if c in (None, "") else c
    for op in cv.ops:
        kind = op[0]
        if kind == "oval":
            x1, y1, x2, y2 = [v * SC for v in op[1]]; k = op[2]
            d.ellipse((x1, y1, x2, y2), fill=col(k.get("fill")), outline=col(k.get("outline")), width=int(k.get("width", 1) * SC) if k.get("outline") else 0)
        elif kind == "line":
            k = op[2]; pts = [v * SC for v in op[1]]
            d.line(pts, fill=col(k.get("fill")), width=int(k.get("width", 1) * SC))
            if k.get("capstyle") == "round":
                r = k.get("width", 1) * SC / 2
                for x, y in (pts[:2], pts[-2:]): d.ellipse((x - r, y - r, x + r, y + r), fill=col(k.get("fill")))
        elif kind == "poly":
            a = op[1][0] if len(op[1]) == 1 else op[1]; k = op[2]; pts = [v * SC for v in a]
            pts = list(zip(pts[::2], pts[1::2]))
            if k.get("fill"): d.polygon(pts, fill=k["fill"], outline=col(k.get("outline")))
            elif k.get("outline"): d.line(pts + [pts[0]], fill=k["outline"], width=int(k.get("width", 1) * SC))
        elif kind == "rect":
            k = op[2]; x1, y1, x2, y2 = [v * SC for v in op[1]]; d.rectangle((x1, y1, x2, y2), fill=col(k.get("fill")))
        elif kind == "arc":
            k = op[2]; x1, y1, x2, y2 = [v * SC for v in op[1]]
            d.arc((x1, y1, x2, y2), -(k["start"] + k["extent"]), -k["start"], fill=k.get("outline"), width=int(k.get("width", 1) * SC))
        elif kind == "text":
            k = op[2]; x, y = op[1][0] * SC, op[1][1] * SC
            f = k.get("font", ("Sans", 10)); size = int(f[1] * 1.33 * SC)
            try: font = ImageFont.truetype("DejaVuSans-Bold.ttf" if "bold" in f else "DejaVuSans.ttf", size)
            except Exception: font = ImageFont.load_default()
            anc = {"w": "lm", "e": "rm", "center": "mm"}.get(k.get("anchor", "w"), "lm")
            d.text((x, y), k.get("text", ""), fill=k.get("fill"), font=font, anchor=anc)
    return im


def main_():
    out = sys.argv[1] if len(sys.argv) > 1 else "preview.png"
    tiles = []
    for demo, dark, three_d in (("v2", False, False), ("v2", True, True), ("v1", False, True)):
        h = main.build(demo); h.start()
        end = time.time() + 8
        while time.time() < end and not (h.snap.connected and h.snap.eq is not None): time.sleep(0.05)
        h.cfg.data.update(theme="dark" if dark else "light", three_d=three_d)
        app = gui.WidgetApp(h); app.apply_effects(); h.apply_mode(gui.NcMode.AMBIENT if demo == "v2" and not dark else gui.NcMode.NOISE_CANCELLING)
        time.sleep(0.4); app.render()
        card = paint(app.canvas, app.pal["card"])
        tiles.append(card); h.stop()
    W = sum(t.width for t in tiles) + 40 * (len(tiles) + 1); H = max(t.height for t in tiles) + 80
    sheet = Image.new("RGB", (W, H), "#6d8fb5"); x = 40
    for t in tiles:
        sheet.paste(t, (x, 40)); x += t.width + 40
    sheet.save(out); print("saved", out, sheet.size)


main_()
