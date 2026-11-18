"""One live RFCOMM link: framing, ACK handling and request/response. Port of SonyConnection.kt."""
from __future__ import annotations

import queue
import threading
import time
from typing import Callable, List, Optional

from . import models
from . import protocol as P
from .protocol import Proto
from .transport import Transport, TransportError

_DEAD = P.Frame(-1, 0, b"")


class LinkClosed(IOError):
    pass


class LinkTimeout(IOError):
    pass


class SonyConnection:
    def __init__(self, transport: Transport, proto: Proto):
        self.t = transport
        self.proto = proto
        self.alive = False
        self.on_frame: Optional[Callable[[P.Frame], None]] = None
        self._acks: "queue.Queue[int]" = queue.Queue(16)
        self._inbox: "queue.Queue[P.Frame]" = queue.Queue(64)
        self._wlock = threading.Lock()
        self._seq = 0

    # -- life cycle
    def start(self) -> None:
        self.alive = True
        threading.Thread(target=self._read_loop, name="sony-reader", daemon=True).start()

    def close(self) -> None:
        self.alive = False
        try:
            self.t.close()
        except Exception:
            pass

    def _read_loop(self) -> None:
        body = bytearray()
        in_frame = False
        try:
            while True:
                chunk = self.t.read()
                if not chunk:
                    break
                for b in chunk:
                    if b == P.START:
                        body.clear(); in_frame = True
                    elif in_frame and b == P.END:
                        in_frame = False
                        f = P.parse_frame(bytes(body))
                        if f is None:
                            continue
                        if f.type == P.TYPE_ACK:
                            self._seq = f.seq
                            self._offer(self._acks, f.seq)
                        else:
                            if f.type in (P.TYPE_DATA_MDR, P.TYPE_DATA_MDR_NO2):
                                try:
                                    self._write(P.TYPE_ACK, (1 - f.seq) & 0xFF, b"")
                                except IOError:
                                    pass
                            if self.on_frame:
                                try:
                                    self.on_frame(f)
                                except Exception:
                                    pass
                            self._offer(self._inbox, f)
                    elif in_frame:
                        body.append(b)
                        if len(body) > P.MAX_FRAME:
                            in_frame = False
        except Exception:
            pass
        finally:
            self.alive = False
            try:
                self.t.close()
            except Exception:
                pass
            self._offer(self._acks, -1)
            self._offer(self._inbox, _DEAD)

    @staticmethod
    def _offer(q: queue.Queue, item) -> None:
        try:
            q.put_nowait(item)
        except queue.Full:
            try:
                q.get_nowait()
                q.put_nowait(item)
            except Exception:
                pass

    @staticmethod
    def _clear(q: queue.Queue) -> None:
        try:
            while True:
                q.get_nowait()
        except queue.Empty:
            pass

    def _write(self, ftype: int, seq: int, payload: bytes) -> None:
        with self._wlock:
            try:
                self.t.write(P.build_frame(ftype, seq, payload))
            except TransportError as e:
                raise LinkClosed(str(e)) from e

    def _guard(self, payload: bytes) -> None:
        if self.proto == Proto.V1 and P.forbidden_on_v1(payload):
            raise RuntimeError("Refusing to send opcode 0x%02X to a v1 headset" % payload[0])

    def _next_seq(self) -> int:
        s = self._seq
        self._seq = (s + 1) & 0xFF
        return s

    # -- request / response
    def send(self, payload: bytes, timeout: float = 4.0) -> None:
        if not self.alive:
            raise LinkClosed("Not connected")
        self._guard(payload)
        self._clear(self._acks)
        self._write(P.TYPE_DATA_MDR, self._next_seq(), payload)
        try:
            ack = self._acks.get(timeout=timeout)
        except queue.Empty:
            raise LinkTimeout("No acknowledgment received from the headphones")
        if ack < 0:
            raise LinkClosed("Bluetooth connection closed")

    def send_and_read(self, payload: bytes, ret_id: int, ret_sub: int = -1, timeout: float = 4.0) -> bytes:
        if not self.alive:
            raise LinkClosed("Not connected")
        self._guard(payload)
        self._clear(self._acks)
        self._clear(self._inbox)
        self._write(P.TYPE_DATA_MDR, self._next_seq(), payload)
        deadline = time.monotonic() + timeout
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise LinkTimeout("Timed out waiting for the headphones")
            try:
                f = self._inbox.get(timeout=remain)
            except queue.Empty:
                raise LinkTimeout("Timed out waiting for the headphones")
            if f.type < 0:
                raise LinkClosed("Bluetooth connection closed")
            p = f.payload
            if (f.type == P.TYPE_DATA_MDR and p and p[0] == ret_id
                    and (ret_sub < 0 or (len(p) >= 2 and p[1] == ret_sub))):
                return p

    def init(self) -> None:
        self.send_and_read(P.init_request(), P.INIT_REPLY)

    def _try(self, payload: bytes, ret_id: int, ret_sub: int, timeout: float) -> Optional[bytes]:
        """send_and_read that turns "no answer" into None but still raises when the link itself is gone."""
        try:
            return self.send_and_read(payload, ret_id, ret_sub, timeout)
        except LinkTimeout:
            return None

    # -- noise control
    def read_state(self, try_adaptive: bool = True) -> Optional[P.State]:
        if self.proto == Proto.V1:
            r = self._try(P.get_state_v1(), P.NCASM_RET, P.NCASM_TYPE_V1, 4)
            return P.parse_state_v1(r) if r else None
        for inq in ((P.NCASM_TYPE_NA, P.NCASM_TYPE) if try_adaptive else (P.NCASM_TYPE,)):
            r = self._try(P.get_state(inq), P.NCASM_RET, inq, 1.5 if inq == P.NCASM_TYPE_NA else 4)
            st = P.parse_state(r, inq) if r else None
            if st:
                return st
        return None

    def set_mode(self, mode: P.NcMode, level: int, voice: bool, adaptive: bool, sens: int,
                 before: Optional[P.State], fallback_type: int, v1_nc: int, v1_asm: int) -> None:
        if self.proto == Proto.V1:
            self.send(P.set_mode_v1(mode, level, voice, before.v1_nc_type if before else v1_nc,
                                    before.v1_asm_type if before else v1_asm))
        else:
            typ = before.inquired_type if before else fallback_type
            self.send(P.set_mode(mode, level, voice, adaptive, sens, typ))

    # -- battery
    def read_single_battery(self) -> Optional[P.Battery]:
        ret = P.BATTERY_RET_V1 if self.proto == Proto.V1 else P.BATTERY_RET
        r = self._try(P.get_single_battery(self.proto), ret, P.BATTERY_SUB_SINGLE, 2.5)
        return P.parse_single_battery(r) if r else None

    def read_battery(self, likely_earbuds: bool = True) -> Optional[P.Battery]:
        if self.proto == Proto.V1:
            return self.read_single_battery()
        if not likely_earbuds:
            b = self.read_single_battery()
            if b:
                return b
        dual = None
        for sub in (P.BATTERY_SUB_DUAL, P.BATTERY_SUB_DUAL2):
            r = self._try(P.get_battery(sub), P.BATTERY_RET, sub, 2.5)
            dual = P.parse_dual_battery(r) if r else None
            if dual:
                break
        if dual is None:
            return self.read_single_battery() if likely_earbuds else None
        r = self._try(P.get_battery(P.BATTERY_SUB_CASE), P.BATTERY_RET, P.BATTERY_SUB_CASE, 2.5)
        case = P.parse_case_battery(r) if r else None
        if case:
            dual.case, dual.case_charging = case
        return dual

    # -- codec / connection quality
    def read_codec(self) -> Optional[int]:
        r = self._try(P.get_codec(self.proto), P.codec_ret_id(self.proto), P.codec_ret_sub(self.proto), 2.5)
        return P.parse_codec(r) if r else None

    def read_conn_mode(self, typ: int) -> Optional[int]:
        sub = P.CONN_TYPE_V1 if self.proto == Proto.V1 else typ
        r = self._try(P.get_conn_mode(self.proto, typ), P.AUDIO_RET_PARAM, sub, 2.5)
        return P.parse_conn_mode(r, self.proto) if r else None

    def probe_conn(self) -> Optional[P.ConnState]:
        if self.proto == Proto.V1:
            m = self.read_conn_mode(P.CONN_TYPE_V1)
            return P.ConnState(P.CONN_TYPE_V1, [0, 1], m) if m is not None else None
        for t in P.CONN_TYPES_V2:
            cap = self._try(P.get_conn_cap(t), P.AUDIO_RET_CAP, t, 1.5)
            modes = P.parse_conn_cap(cap, t) if cap else None
            if not modes:
                continue
            m = self.read_conn_mode(t)
            if m is not None:
                return P.ConnState(t, modes, m)
        return None

    # -- listening mode (background music)
    def read_listening(self, typ: int) -> Optional[P.ListenState]:
        if self.proto != Proto.V2:
            return None
        s = self._try(P.get_bgm_status(typ), P.AUDIO_RET_STATUS, typ, 2)
        avail = P.parse_bgm_available(s, typ) if s else None
        r = self._try(P.get_bgm(typ), P.AUDIO_RET_PARAM, typ, 2)
        parsed = P.parse_bgm(r, typ) if r else None
        if parsed is None:
            return None
        return P.ListenState(typ, parsed[0], parsed[1], True if avail is None else avail)

    def probe_listening(self) -> Optional[P.ListenState]:
        if self.proto != Proto.V2:
            return None
        for t in P.BGM_TYPES:
            cap = self._try(P.get_bgm_cap(t), P.AUDIO_RET_CAP, t, 1.5)
            if cap and len(cap) >= 2:
                st = self.read_listening(t)
                if st:
                    return st
        return None

    # -- equalizer
    def read_eq(self, kind: Optional[P.EqKind] = None) -> Optional[P.EqState]:
        if kind is not None:
            kinds: List[P.EqKind] = [kind]
        elif self.proto == Proto.V1:
            kinds = [P.EqKind.SIX_V1]
        else:
            kinds = [P.EqKind.TEN_V2, P.EqKind.SIX_V2]
        for k in kinds:
            r = self._try(P.get_eq(k), P.EQ_RET, k.sub, 1.5 if len(kinds) > 1 and k == P.EqKind.TEN_V2 else 3)
            st = P.parse_eq(r, k) if r else None
            if st:
                return st
        return None
