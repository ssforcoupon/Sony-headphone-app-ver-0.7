"""Everything between the UI and the headphones: picks the device, keeps one live link, mirrors the headphones' state
and offers the operations. Port of SonyController / LiveService / Prefs from the Android app."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import models
from . import protocol as P
from .connection import LinkClosed, LinkTimeout, SonyConnection
from .protocol import NcMode, Proto
from .transport import DeviceInfo, Transport, TransportError

UUIDS = {Proto.V1: P.SERVICE_UUID_V1, Proto.V2: P.SERVICE_UUID_V2}


# ------------------------------------------------------------------------------------------------------------------
# persistent settings
# ------------------------------------------------------------------------------------------------------------------
def data_dir(exe_dir: Optional[Path] = None) -> Path:
    """Where settings live: next to the program in portable mode (a file named portable.txt beside it), else %APPDATA%."""
    if exe_dir is None:
        exe_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(sys.argv[0]).resolve().parent
    if (exe_dir / "portable.txt").exists():
        return exe_dir / "data"
    return Path(os.environ.get("APPDATA") or Path.home() / ".config") / "SonyBridgeWindows"


_LAST_LOGGED: Dict[str, str] = {}


def log_once(key: str, msg: str) -> None:
    """Like log(), but writes only when the message for this key changed (a failing 5-second loop must not fill the file)."""
    if _LAST_LOGGED.get(key) != msg:
        _LAST_LOGGED[key] = msg
        log(msg)


def log(msg: str) -> None:
    """Appends to log.txt in the data folder (the exe has no console, so this is where errors can be read)."""
    try:
        path = data_dir() / "log.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 200_000:
            path.write_text("", "utf-8")
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


class Config:
    """JSON file (see data_dir). Global window settings plus a small dict per headphone (its id)."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or data_dir() / "config.json"
        self.lock = threading.RLock()
        self.data: Dict = {"follow": True, "always_on_top": True, "device": "", "x": None, "y": None, "paused": False,
                           "transparency": 40, "frost": 40, "blur": "acrylic", "three_d": False, "outline": False,
                           "tray": True, "start_hidden": False, "close_to_tray": True, "devices": {}}
        try:
            self.data.update(json.loads(self.path.read_text("utf-8")))
        except Exception:
            pass

    def save(self) -> None:
        with self.lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.data, indent=1), "utf-8")
            except OSError:
                pass

    def get(self, key, default=None):
        with self.lock:
            return self.data.get(key, default)

    def set(self, key, value) -> None:
        with self.lock:
            self.data[key] = value
        self.save()

    def dev(self, dev_id: str) -> Dict:
        with self.lock:
            return self.data["devices"].setdefault(dev_id, {})

    def set_dev(self, dev_id: str, **kv) -> None:
        with self.lock:
            self.data["devices"].setdefault(dev_id, {}).update(kv)
        self.save()


# ------------------------------------------------------------------------------------------------------------------
# what the UI sees
# ------------------------------------------------------------------------------------------------------------------
@dataclass
class Snap:
    device_id: str = ""
    name: str = ""
    connected: bool = False
    connecting: bool = False
    proto: Optional[Proto] = None
    mode: Optional[NcMode] = None
    level: int = 10
    voice: bool = False
    auto_supported: bool = True
    auto_on: bool = False
    sensitivity: int = 0
    battery: Optional[P.Battery] = None
    codec: int = -1
    conn: Optional[P.ConnState] = None
    listen: Optional[P.ListenState] = None
    eq: Optional[P.EqState] = None
    status: str = ""
    error: str = ""
    paused: bool = False
    os_connected: bool = False    # what Windows says about the Bluetooth link (the control link can drop and come back)
    lost_at: float = 0.0          # when the control link was last lost (time.monotonic), 0 = never / not lost

    GRACE = 30.0                  # keep showing "Connected" this long while the control link reconnects

    def shown_connected(self) -> bool:
        """What the widget shows: connected, or briefly lost while Windows still has the headphones connected (the
        phone's Sony app and this app take turns on the single control link, so short drops are normal)."""
        if self.connected:
            return True
        if self.paused:
            return False
        return bool(self.lost_at and self.os_connected and time.monotonic() - self.lost_at < self.GRACE)

    @property
    def title(self) -> str:
        return models.display(self.name)

    def connection_label(self) -> str:
        return "Connected" if self.shown_connected() else "Disconnected"

    def battery_text(self) -> str:
        b = self.battery
        if b is None:
            return ""
        if b.single > 0:
            return "%d%%%s" % (b.single, "+" if b.single_charging else "")
        parts = []
        if b.left > 0:
            parts.append("L %d%%" % b.left)
        if b.right > 0:
            parts.append("R %d%%" % b.right)
        if b.case > 0:
            parts.append("Case %d%%" % b.case)
        return "  ".join(parts)


OpenFn = Callable[[DeviceInfo, Proto], Transport]
ListFn = Callable[[], List[DeviceInfo]]


class Headset:
    """Owns the live link. UI calls the operation methods from a worker thread (they block for up to a few seconds)."""

    FILLER = {Proto.V1: 0, Proto.V2: 1}

    def __init__(self, config: Config, list_devices: ListFn, open_transport: OpenFn,
                 poll_seconds: float = 5.0):
        self.cfg = config
        self._list = list_devices
        self._open = open_transport
        self.poll = poll_seconds
        self.snap = Snap(paused=bool(config.get("paused")))
        self.listeners: List[Callable[[], None]] = []
        self._conn: Optional[SonyConnection] = None
        self._cmd = threading.RLock()
        self._wake = threading.Event()
        self._stop = False
        self._dirty = set()
        self._thread: Optional[threading.Thread] = None

    # ---------------------------------------------------------------- life cycle
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="headset", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()
        c = self._conn
        if c:
            c.close()

    def changed(self) -> None:
        for fn in list(self.listeners):
            try:
                fn()
            except Exception:
                pass

    def set_paused(self, paused: bool) -> None:
        self.cfg.set("paused", paused)
        self.snap.paused = paused
        if paused and self._conn:
            self._conn.close()
        self._wake.set()
        self.changed()

    def select_device(self, dev: DeviceInfo) -> None:
        """Manual pick: stop following and move to [dev]."""
        self.cfg.set("follow", False)
        self.cfg.set("device", dev.id)
        c = self._conn
        if c:
            c.close()
        self._wake.set()

    def set_follow(self, on: bool) -> None:
        self.cfg.set("follow", on)
        self._wake.set()

    # ---------------------------------------------------------------- device choice
    def _choose(self, devices: List[DeviceInfo]) -> Optional[DeviceInfo]:
        sony = [d for d in devices if models.is_sony(d.name)]
        saved = self.cfg.get("device", "")
        if self.cfg.get("follow", True):
            live = [d for d in sony if d.connected]
            if live:
                keep = [d for d in live if d.id == saved]
                return (keep or live)[0]
        pick = next((d for d in devices if d.id == saved), None)
        if pick is None and sony:
            pick = sony[0]
        return pick

    def _apply_device(self, dev: DeviceInfo) -> None:
        s = self.snap
        if s.device_id == dev.id:
            s.name = dev.name or s.name
            return
        self.cfg.set("device", dev.id)
        d = self.cfg.dev(dev.id)
        auto_cap = models.auto_ambient_by_name(dev.name)
        s.device_id, s.name = dev.id, dev.name
        s.lost_at = 0.0
        s.proto = Proto[d["proto"]] if d.get("proto") in ("V1", "V2") else None
        s.mode, s.battery, s.codec, s.eq, s.listen = None, None, -1, None, None
        s.level = int(d.get("level", 10))
        s.voice = bool(d.get("voice", False))
        s.auto_supported = auto_cap is not False
        s.auto_on, s.sensitivity = False, 0
        ct = d.get("conn_type", -1)
        s.conn = P.ConnState(ct, d.get("conn_modes", []), d.get("conn_mode", -1)) if ct >= 0 else None
        s.status, s.error = "", ""

    # ---------------------------------------------------------------- main loop
    def _run(self) -> None:
        while not self._stop:
            try:
                self._cycle()
            except Exception as e:  # never let the loop die
                import traceback
                log_once("loop", "loop error: " + traceback.format_exc())
                self.snap.error = "%s: %s" % (e.__class__.__name__, e)
                self.snap.connected = self.snap.connecting = False
                self.changed()
            # a cycle only returns when there is no control link: try again soon (the other app may have let go),
            # slowing down while it keeps failing
            self._fails = 0 if self.snap.connected else getattr(self, "_fails", 0) + 1
            self._wake.wait(min(self.poll, 2.0 + 1.0 * min(self._fails, 3)))
            self._wake.clear()

    def reconnect_now(self) -> None:
        self._wake.set()

    def _cycle(self) -> None:
        s = self.snap
        if self.cfg.get("paused"):
            s.connected = s.connecting = False
            s.paused = True
            self.changed()
            return
        s.paused = False
        try:
            devices = self._list()
        except Exception as e:
            import traceback
            log_once("list", "listing devices failed: " + traceback.format_exc())
            s.error = "Couldn't list Bluetooth devices: %s: %s" % (e.__class__.__name__, e)
            self.changed()
            return
        sig = sorted((d.name, d.connected) for d in devices)     # Windows returns them in a different order each time
        if sig != getattr(self, "_logged_devs", None):
            self._logged_devs = sig
            log("paired devices: %s" % (sig or "none"))
        dev = self._choose(devices)
        s.os_connected = bool(dev and dev.connected)
        if dev is None:
            s.connected = s.connecting = False
            s.error = ""
            if devices:
                s.status = ("Found %d paired device(s) but none named like Sony headphones. Right-click > Choose headphones."
                            % len(devices))
            else:
                s.status = "Windows lists no paired Bluetooth audio devices. Pair the headphones in Windows settings."
            self.changed()
            return
        self._apply_device(dev)
        if not dev.connected and self.cfg.get("follow", True):
            # Windows says it isn't connected: still try once, the flag can lag behind the real state.
            pass
        s.connecting, s.connected = True, False
        self.changed()
        conn = self._open_link(dev)
        if conn is None:
            s.connecting = False
            self.changed()
            return
        self._conn = conn
        s.connecting, s.connected, s.error, s.lost_at = False, True, "", 0.0
        log_once("link-up", "control link open to %r (%s)" % (dev.name, s.proto.name if s.proto else "?"))
        _LAST_LOGGED.pop("link-up", None)
        self.changed()
        timeouts = 0
        try:
            try:
                self._sync_all(conn)
            except LinkTimeout:      # one slow answer must not end the whole link
                log_once("sync-timeout", "reading the headphones' state timed out; keeping the link")
            while conn.alive and not self._stop and not self.cfg.get("paused"):
                try:
                    self._service_dirty(conn)
                    timeouts = 0
                except LinkTimeout:
                    timeouts += 1
                    log_once("svc-timeout", "a command timed out (%d)" % timeouts)
                    if timeouts >= 3:
                        break
                self._wake.wait(1.0)
                self._wake.clear()
                if self._maybe_switch(conn):
                    break
        except LinkClosed:
            pass
        finally:
            conn.close()
            self._conn = None
            s.connected = False
            s.lost_at = time.monotonic()
            log("control link closed (%s)" % ("alive=%s" % conn.alive))
            self.changed()

    def _maybe_switch(self, conn: SonyConnection) -> bool:
        """While connected: leave when another Sony device became the connected one (follow mode)."""
        if not self.cfg.get("follow", True) or time.monotonic() - getattr(self, "_last_poll", 0) < max(self.poll, 8):
            return False
        self._last_poll = time.monotonic()
        try:
            dev = self._choose(self._list())
        except Exception:
            return False
        return bool(dev and dev.id != self.snap.device_id)

    def _open_link(self, dev: DeviceInfo) -> Optional[SonyConnection]:
        s = self.snap
        hint = s.proto
        order = [hint, Proto.V2 if hint == Proto.V1 else Proto.V1] if hint else (
            [Proto.V1, Proto.V2] if models.likely_v1(dev.name) else [Proto.V2, Proto.V1])
        reasons: List[str] = []
        for proto in order:
            t = None
            try:
                t = self._open(dev, proto)
                conn = SonyConnection(t, proto)
                conn.on_frame = self._on_frame
                conn.start()
                try:
                    conn.init()
                except Exception:
                    conn.close()
                    raise
                s.proto = proto
                self.cfg.set_dev(dev.id, proto=proto.name)
                return conn
            except Exception as e:
                import traceback
                log_once("link" + proto.name, "link attempt %s to %r failed: %s" % (proto.name, dev.name, traceback.format_exc()))
                reasons.append("%s: %s" % (proto.name, e))
                if t is not None:
                    try:
                        t.close()
                    except Exception:
                        pass
        s.error = " | ".join(reasons)
        log("could not open control link to %r: %s" % (dev.name, s.error))
        return None

    # ---------------------------------------------------------------- reading
    def _sync_all(self, c: SonyConnection) -> None:
        with self._cmd:
            self._sync_state(c)
            self._sync_battery(c)
            self._sync_codec(c)
            self._sync_conn(c)
            self._sync_listening(c)
            self._sync_eq(c)
        self.snap.status = "Connected"
        self.changed()

    def _service_dirty(self, c: SonyConnection) -> None:
        if not self._dirty:
            return
        todo, self._dirty = set(self._dirty), set()
        with self._cmd:
            if "battery" in todo:
                self._sync_battery(c)
            if "codec" in todo:
                self._sync_codec(c)
            if "eq" in todo:
                self._sync_eq(c)
        self.changed()

    @property
    def auto_unsupported(self) -> bool:
        s, d = self.snap, self.cfg.dev(self.snap.device_id) if self.snap.device_id else {}
        return s.proto == Proto.V1 or not s.auto_supported or d.get("na_misses", 0) >= 2

    def _read_state(self, c: SonyConnection, force_adaptive: bool = False) -> Optional[P.State]:
        s = self.snap
        try_adaptive = c.proto == Proto.V2 and (force_adaptive or not self.auto_unsupported)
        st = c.read_state(try_adaptive)
        if st is not None:
            self._apply_caps(st)
            if c.proto == Proto.V2 and st.adaptive is None and try_adaptive:
                d = self.cfg.dev(s.device_id)
                self.cfg.set_dev(s.device_id, na_misses=d.get("na_misses", 0) + 1)
        return st

    def _apply_caps(self, st: P.State) -> None:
        s = self.snap
        if st.adaptive is not None:
            s.auto_supported, s.auto_on, s.sensitivity = True, st.adaptive, st.sensitivity
            self.cfg.set_dev(s.device_id, na_misses=0)
        d = {}
        if st.inquired_type == P.NCASM_TYPE_V1:
            d.update(v1_nc=st.v1_nc_type, v1_asm=st.v1_asm_type)
        else:
            d.update(nc_type=st.inquired_type)
        self.cfg.set_dev(s.device_id, **d)

    def _apply_state(self, st: P.State) -> None:
        """State read from, or pushed by, the headphones. Not in ambient the level is still the stored one."""
        s = self.snap
        s.mode = st.mode
        self._apply_caps(st)
        if st.mode == NcMode.AMBIENT:
            self._store_level(st.level)
            s.voice = st.voice
            self.cfg.set_dev(s.device_id, voice=s.voice)
        elif 1 <= st.level <= 20 and st.level != self.FILLER[Proto.V1 if st.inquired_type == P.NCASM_TYPE_V1 else Proto.V2]:
            self._store_level(st.level)

    def _store_level(self, level: int) -> None:
        level = max(1, min(20, level))
        self.snap.level = level
        self.cfg.set_dev(self.snap.device_id, level=level)

    def _sync_state(self, c: SonyConnection) -> Optional[P.State]:
        st = self._read_state(c)
        if st:
            self._apply_state(st)
        return st

    def _sync_battery(self, c: SonyConnection) -> None:
        b = c.read_battery(models.is_earbuds(self.snap.name))
        if b:
            self.snap.battery = b

    def _sync_codec(self, c: SonyConnection) -> None:
        v = c.read_codec()
        if v is not None:
            self.snap.codec = v

    def _sync_conn(self, c: SonyConnection) -> None:
        s = self.snap
        d = self.cfg.dev(s.device_id)
        if s.conn and s.conn.type >= 0:
            m = c.read_conn_mode(s.conn.type)
            if m is not None:
                s.conn.mode = m
                self.cfg.set_dev(s.device_id, conn_mode=m)
            return
        if d.get("conn_misses", 0) >= 2:
            return
        st = c.probe_conn()
        if st:
            s.conn = st
            self.cfg.set_dev(s.device_id, conn_type=st.type, conn_modes=st.modes, conn_mode=st.mode, conn_misses=0)
        else:
            self.cfg.set_dev(s.device_id, conn_misses=d.get("conn_misses", 0) + 1)

    @property
    def listening_unsupported(self) -> bool:
        s = self.snap
        return s.proto == Proto.V1 or (s.listen is None and self.cfg.dev(s.device_id).get("bgm_misses", 0) >= 2)

    def _sync_listening(self, c: SonyConnection) -> None:
        s = self.snap
        if self.listening_unsupported:
            return
        st = c.read_listening(s.listen.type) if s.listen else c.probe_listening()
        if st:
            s.listen = st
        elif s.listen is None:
            self.cfg.set_dev(s.device_id, bgm_misses=self.cfg.dev(s.device_id).get("bgm_misses", 0) + 1)

    @property
    def eq_unsupported(self) -> bool:
        s = self.snap
        return s.eq is None and self.cfg.dev(s.device_id).get("eq_misses", 0) >= 2

    def _sync_eq(self, c: SonyConnection) -> None:
        s = self.snap
        if self.eq_unsupported:
            return
        st = c.read_eq(s.eq.kind if s.eq else None)
        if st:
            s.eq = st
        elif s.eq is None:
            self.cfg.set_dev(s.device_id, eq_misses=self.cfg.dev(s.device_id).get("eq_misses", 0) + 1)

    # ---------------------------------------------------------------- notifications from the headphones
    def _on_frame(self, f: P.Frame) -> None:
        p, s = f.payload, self.snap
        c = self._conn
        if f.type != P.TYPE_DATA_MDR or len(p) < 2:
            return
        op, sub = p[0], p[1]
        proto = s.proto
        hit = False
        if op == P.EQ_RET and s.eq and s.eq.kind.sub == sub:
            st = P.parse_eq(p, s.eq.kind)
            if st:
                s.eq, hit = st, True
        elif op == P.EQ_NTFY:
            self._dirty.add("eq"); self._wake.set()
        elif proto == Proto.V2 and s.listen and sub == s.listen.type and op in (P.AUDIO_RET_PARAM, P.AUDIO_NTFY_PARAM):
            r = P.parse_bgm(p, s.listen.type)
            if r:
                s.listen.bgm, s.listen.room, hit = r[0], r[1], True
        elif proto == Proto.V2 and s.listen and sub == s.listen.type and op in (P.AUDIO_RET_STATUS, P.AUDIO_NTFY_STATUS):
            a = P.parse_bgm_available(p, s.listen.type)
            if a is not None:
                s.listen.available, hit = a, True
        elif op in (P.AUDIO_RET_PARAM, P.AUDIO_NTFY_PARAM):
            want = P.CONN_TYPE_V1 if proto == Proto.V1 else (s.conn.type if s.conn else -1)
            if want >= 0 and sub == want and s.conn:
                m = P.parse_conn_mode(p, proto)
                if m is not None:
                    s.conn.mode, hit = m, True
                    self._dirty.add("codec"); self._wake.set()
        elif proto == Proto.V1:
            if op in (P.NCASM_RET, P.NCASM_NTFY):
                st = P.parse_state_v1(p)
                if st:
                    self._apply_state(st); hit = True
            elif op == P.BATTERY_RET_V1 and sub == 0:
                b = P.parse_single_battery(p)
                if b:
                    s.battery, hit = b, True
            elif op == P.BATTERY_NTFY_V1:
                self._dirty.add("battery"); self._wake.set()
            elif op == P.CODEC_RET_V1 and sub == 0:
                v = P.parse_codec(p)
                if v is not None:
                    s.codec, hit = v, True
        elif proto == Proto.V2:
            if op in (P.NCASM_RET, P.NCASM_NTFY) and sub in (P.NCASM_TYPE, P.NCASM_TYPE_NA):
                st = P.parse_state(p, sub)
                if st:
                    self._apply_state(st); hit = True
            elif op == P.BATTERY_RET:
                if sub in (P.BATTERY_SUB_DUAL, P.BATTERY_SUB_DUAL2):
                    b = P.parse_dual_battery(p)
                    if b:
                        old = s.battery
                        if old:
                            b.case, b.case_charging = old.case, old.case_charging
                        s.battery, hit = b, True
                elif sub == P.BATTERY_SUB_CASE and s.battery:
                    r = P.parse_case_battery(p)
                    if r:
                        s.battery.case, s.battery.case_charging = r; hit = True
                elif sub == P.BATTERY_SUB_SINGLE:
                    b = P.parse_single_battery(p)
                    if b:
                        s.battery, hit = b, True
            elif op == P.BATTERY_NTFY:
                self._dirty.add("battery"); self._wake.set()
            elif op == P.CODEC_RET_V2 and sub == 0x02:
                v = P.parse_codec(p)
                if v is not None:
                    s.codec, hit = v, True
            elif op == P.CODEC_NTFY_V2:
                self._dirty.add("codec"); self._wake.set()
        if hit:
            self.changed()

    # ---------------------------------------------------------------- operations (blocking)
    def _session(self, fn: Callable[[SonyConnection], str]) -> str:
        c = self._conn
        if c is None or not c.alive:
            return "Not connected to the headphones"
        try:
            with self._cmd:
                msg = fn(c)
        except (LinkClosed, LinkTimeout) as e:
            msg = "Failed: %s" % e
        except Exception as e:
            msg = "Error: %s" % e
        self.snap.status = msg
        self.changed()
        return msg

    def _send_mode(self, c: SonyConnection, mode: NcMode, before: Optional[P.State]) -> None:
        s, d = self.snap, self.cfg.dev(self.snap.device_id)
        c.set_mode(mode, s.level, s.voice, s.auto_on, s.sensitivity, before, d.get("nc_type", P.NCASM_TYPE),
                   d.get("v1_nc", P.V1_NC_DUAL_SINGLE_OFF), d.get("v1_asm", P.V1_ASM_LEVEL_ADJUSTMENT))

    def apply_mode(self, mode: NcMode) -> str:
        def go(c):
            before = self._read_state(c)
            self._send_mode(c, mode, before)
            st = self._read_state(c)
            self._sync_battery(c)
            if st is None:
                self.snap.mode = mode
                return "Sent %s (no read-back)" % mode.value
            self._apply_state(st)
            return "%s confirmed" % st.mode.value if st.mode == mode else "Sent %s, headphones report %s" % (mode.value, st.mode.value)
        return self._session(go)

    def set_ambient(self, level: Optional[int] = None, voice: Optional[bool] = None) -> str:
        """Saved for next time; sent immediately while Ambient is active (cheap enough to call while a slider moves)."""
        s = self.snap
        if level is not None:
            self._store_level(level)
        if voice is not None:
            s.voice = voice
            self.cfg.set_dev(s.device_id, voice=voice)
        if s.mode != NcMode.AMBIENT or not s.connected:
            return s.status

        def go(c):
            self._send_mode(c, NcMode.AMBIENT, None)
            return "Ambient level %d" % s.level
        return self._session(go)

    def set_auto(self, enabled: Optional[bool] = None, sensitivity: Optional[int] = None) -> str:
        if self.auto_unsupported:
            return "Auto ambient isn't available on %s" % self.snap.title

        def go(c):
            st = self._read_state(c, True)
            if st is None or st.adaptive is None:
                return "Auto ambient isn't available on these headphones"
            on = (not st.adaptive) if enabled is None else enabled
            sens = st.sensitivity if sensitivity is None else sensitivity
            c.send(P.set_mode(st.mode, st.level, st.voice, on, sens, P.NCASM_TYPE_NA))
            after = self._read_state(c, True)
            if after:
                self._apply_state(after)
            actual = after.adaptive if after and after.adaptive is not None else on
            return "Auto ambient %s%s" % ("on" if actual else "off", "" if after and after.adaptive == on else " (unconfirmed)")
        return self._session(go)

    def refresh(self) -> str:
        def go(c):
            self._sync_state(c); self._sync_battery(c); self._sync_codec(c)
            return "Updated " + time.strftime("%H:%M")
        return self._session(go)

    def set_conn_mode(self, mode: int) -> str:
        def go(c):
            s = self.snap
            if s.conn is None:
                self._sync_conn(c)
            if s.conn is None:
                return "This headphone didn't report a connection-quality setting"
            if mode not in s.conn.modes:
                return "These headphones don't offer that mode"
            c.send(P.set_conn_mode(c.proto, s.conn.type, mode))
            s.conn.mode = mode
            after = c.read_conn_mode(s.conn.type)
            if after is not None:
                s.conn.mode = after
            self.cfg.set_dev(s.device_id, conn_mode=s.conn.mode)
            self._sync_codec(c)
            m = P.ConnMode.of(s.conn.mode)
            return "%s applied" % (m.title if m else "Connection quality")
        return self._session(go)

    def set_listening(self, bgm: bool, room: int) -> str:
        def go(c):
            s = self.snap
            if s.listen is None:
                self._sync_listening(c)
            if s.listen is None:
                return "Background-music mode wasn't reported by these headphones"
            t = s.listen.type
            if bgm:
                r = c._try(P.get_bgm_status(t), P.AUDIO_RET_STATUS, t, 2)
                ok = P.parse_bgm_available(r, t) if r else None
                if ok is False:
                    s.listen.available = False
                    return "The headphones can't switch to Background music right now"
            c.send(P.set_bgm(t, bgm, room))
            s.listen.bgm, s.listen.room = bgm, room
            st = c.read_listening(t)
            if st:
                s.listen = st
            self._sync_eq(c)   # the headphones may change the equalizer with the listening mode
            r = P.RoomSize.of(s.listen.room)
            return "Background music - %s room" % (r.title if r else "") if s.listen.bgm else "Standard"
        return self._session(go)

    def set_eq_preset(self, preset: int) -> str:
        def go(c):
            s = self.snap
            if s.eq is None:
                self._sync_eq(c)
            if s.eq is None:
                return "This headphone didn't report an equalizer"
            c.send(P.set_eq_preset(s.eq.kind, preset))
            st = c.read_eq(s.eq.kind)
            if st:
                s.eq = st
            return "Equalizer: %s" % P.eq_preset_name(s.eq.preset)
        return self._session(go)

    def set_eq_custom(self, clear_bass: int, bands: List[int]) -> str:
        def go(c):
            s = self.snap
            if s.eq is None:
                return "This headphone didn't report an equalizer"
            c.send(P.set_eq_custom(s.eq.kind, clear_bass, bands))
            s.eq = P.EqState(s.eq.kind, P.EQ_MANUAL, clear_bass, list(bands))
            return "Equalizer: Custom"
        return self._session(go)

    def retry_probes(self) -> None:
        self.cfg.set_dev(self.snap.device_id, conn_misses=0, bgm_misses=0, eq_misses=0, na_misses=0)
        self._wake.set()

    def devices(self) -> List[DeviceInfo]:
        try:
            return [d for d in self._list() if models.is_sony(d.name)] or self._list()
        except Exception:
            return []
