import pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from sonybridge.controller import Config, data_dir

fails = 0
def check(name, ok):
    global fails
    fails += not ok
    print(("ok   " if ok else "FAIL ") + name)

d = pathlib.Path(tempfile.mkdtemp())
check("normal mode uses the per-user settings folder", "SonyBridgeWindows" in str(data_dir(d)) and d not in data_dir(d).parents)
(d / "portable.txt").write_text("x")
check("portable.txt switches to a data folder next to the exe", data_dir(d) == d / "data")
cfg = Config(data_dir(d) / "config.json")
cfg.set("x", 12)
check("portable settings are written next to the exe", (d / "data" / "config.json").exists() and Config(d / "data" / "config.json").get("x") == 12)
sys.exit(1 if fails else 0)
