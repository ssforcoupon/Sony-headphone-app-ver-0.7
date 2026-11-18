"""'Connected' stays on screen through short control-link drops while Windows still has the headphones connected."""
import pathlib, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from sonybridge.controller import Snap

fails = 0
def check(n, ok):
    global fails
    fails += not ok
    print(("ok   " if ok else "FAIL ") + n)

s = Snap(connected=True)
check("connected shows Connected", s.connection_label() == "Connected")
s.connected, s.lost_at, s.os_connected = False, time.monotonic(), True
check("just lost + Windows still connected: still Connected", s.connection_label() == "Connected")
s.lost_at = time.monotonic() - 40
check("lost 40 s ago: Disconnected", s.connection_label() == "Disconnected")
s.lost_at, s.os_connected = time.monotonic(), False
check("lost and Windows says not connected: Disconnected", s.connection_label() == "Disconnected")
s.os_connected, s.paused = True, True
check("paused: Disconnected", s.connection_label() == "Disconnected")
check("only two labels exist", {Snap(connected=c).connection_label() for c in (True, False)} == {"Connected", "Disconnected"})
sys.exit(1 if fails else 0)
