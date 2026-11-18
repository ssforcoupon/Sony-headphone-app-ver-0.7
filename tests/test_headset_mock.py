"""End-to-end test of the controller against the simulated headsets (no Bluetooth needed)."""
import pathlib, sys, tempfile, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from sonybridge import protocol as P
from sonybridge.controller import Config, Headset
from sonybridge.transport import DeviceInfo, MockHeadset

fails = 0
def check(name, ok):
    global fails
    fails += not ok
    print(("ok   " if ok else "FAIL ") + name)

def wait(cond, t=8.0):
    end = time.time() + t
    while time.time() < end:
        if cond(): return True
        time.sleep(0.05)
    return False

def make(devname, mock_factory, devs=None):
    cfg = Config(pathlib.Path(tempfile.mkdtemp()) / "c.json")
    mocks = []
    def opener(dev, proto):
        if proto != mock_factory().proto:      # the headset only offers its own service
            from sonybridge.transport import TransportError
            raise TransportError("service not offered")
        m = mock_factory(); mocks.append(m); return m
    devices = devs or [DeviceInfo("id-" + devname, devname, True)]
    h = Headset(cfg, lambda: devices, opener, poll_seconds=0.2)
    h.start()
    return h, cfg, mocks

# --- WF-1000XM6-like earbuds (v2, auto ambient, ten-band eq, bgm, conn quality)
h, cfg, mocks = make("WF-1000XM6", lambda: MockHeadset(P.Proto.V2, eq_kind=P.EqKind.TEN_V2))
check("v2 connects and syncs", wait(lambda: h.snap.connected and h.snap.mode is not None and h.snap.eq is not None and h.snap.conn is not None))
m = mocks[0]
check("v2 reads headphone's ambient level (12), not default 10", h.snap.level == 12)
check("v2 battery text", "L 80%" in h.snap.battery_text() and "Case 60%" in h.snap.battery_text())
check("v2 auto ambient supported", h.snap.auto_supported and not h.auto_unsupported)
check("v2 bgm probed", h.snap.listen is not None and not h.snap.listen.bgm)
check("v2 conn modes include low latency", h.snap.conn.modes == [0, 1, 2])
h.apply_mode(P.NcMode.AMBIENT)
check("v2 apply ambient", m.mode == P.NcMode.AMBIENT and h.snap.mode == P.NcMode.AMBIENT and m.level == 12)
h.set_ambient(level=17)
check("v2 live ambient slider", m.level == 17 and h.snap.level == 17)
h.apply_mode(P.NcMode.NOISE_CANCELLING)
check("v2 NC keeps level on the headphones", m.mode == P.NcMode.NOISE_CANCELLING and m.level == 17)
h.set_auto(True, 1)
check("v2 auto ambient set", m.adapt_on and m.adapt_sens == 1 and h.snap.auto_on)
h.set_conn_mode(2)
check("v2 low latency applied", m.conn_mode == 2 and h.snap.conn.mode == 2 and h.snap.codec == m.codec)
h.set_listening(True, 2)
check("v2 background music + large room", m.bgm_on and m.bgm_room == 2 and h.snap.listen.bgm and h.snap.listen.room == 2)
h.set_eq_custom(0, [1, 2, 3, 4, 5, 6, -1, -2, -3, -4])
check("v2 ten-band custom eq", m.eq_bands == [1, 2, 3, 4, 5, 6, -1, -2, -3, -4] and h.snap.eq.preset == P.EQ_MANUAL)
h.set_eq_preset(0x10)
check("v2 eq preset bright", m.eq_preset == 0x10 and h.snap.eq.preset == 0x10)
m.push(bytes([P.NCASM_NTFY, 0x17, 1, 1, 0, 0, 17]))          # headphones button press -> notification
check("v2 notification updates app", wait(lambda: h.snap.mode == P.NcMode.NOISE_CANCELLING))
h.stop()

# --- WH-1000XM4-like (v1: no auto ambient, no bgm, six-band eq, connection quality 2 modes)
h, cfg, mocks = make("WH-1000XM4", lambda: MockHeadset(P.Proto.V1, earbuds=False))
check("v1 connects (falls back to the service it offers)", wait(lambda: h.snap.connected and h.snap.mode is not None and h.snap.eq is not None))
m = mocks[0]
check("v1 identified", h.snap.proto == P.Proto.V1 and h.auto_unsupported and h.listening_unsupported)
check("v1 battery single", h.snap.battery_text() == "55%")
h.apply_mode(P.NcMode.AMBIENT)
check("v1 ambient uses remembered level", m.mode == P.NcMode.AMBIENT and m.level == 10)
h.set_ambient(level=4)
check("v1 live ambient level", m.level == 4)
h.apply_mode(P.NcMode.NOISE_CANCELLING)
check("v1 NC sent", m.mode == P.NcMode.NOISE_CANCELLING and h.snap.mode == P.NcMode.NOISE_CANCELLING)
check("v1 level kept locally after NC", h.snap.level == 4)
check("v1 auto ambient refused", "isn't available" in h.set_auto(True))
h.set_conn_mode(1)
check("v1 stable connection", m.conn_mode == 1)
h.set_eq_custom(2, [1, 0, -1, 2, 0])
check("v1 six-band custom eq", m.eq_clear == 2 and m.eq_bands == [1, 0, -1, 2, 0])
check("v1 never received a forbidden opcode", all(not P.forbidden_on_v1(p) for p in m.sent))
h.stop()

# --- follow: two Sony devices, the connected one wins and per-device level is remembered
d1, d2 = DeviceInfo("a", "WF-1000XM6", False), DeviceInfo("b", "WH-1000XM4", True)
h, cfg, mocks = make("x", lambda: MockHeadset(P.Proto.V1, earbuds=False), devs=[d1, d2])
check("follow picks the connected device", wait(lambda: h.snap.connected and h.snap.name == "WH-1000XM4"))
check("widget label name", h.snap.title == "WH-1000XM4" and h.snap.connection_label() == "Connected")
h.stop()
sys.exit(1 if fails else 0)
