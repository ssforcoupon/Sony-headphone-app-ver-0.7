"""Byte transports. WinTransport talks to a paired Sony headset over classic Bluetooth RFCOMM through the Windows
Runtime (the `winrt-*` packages); MockHeadset is a simulated headset used by the tests and by `--demo`."""
from __future__ import annotations

import asyncio
import queue
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional

from . import protocol as P


class TransportError(IOError):
    pass


class Transport(ABC):
    @abstractmethod
    def read(self) -> bytes:
        """Blocks until at least one byte arrives; returns b'' when the link is closed."""

    @abstractmethod
    def write(self, data: bytes) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


@dataclass
class DeviceInfo:
    id: str           # Windows device id (or a fake one in demo mode)
    name: str
    connected: bool
    address: int = 0


# ---------------------------------------------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------------------------------------------
class _Loop:
    """One asyncio loop in a daemon thread, shared by all WinRT calls."""
    _inst: Optional["_Loop"] = None

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, name="winrt-loop", daemon=True).start()

    @classmethod
    def get(cls) -> "_Loop":
        if cls._inst is None:
            cls._inst = _Loop()
        return cls._inst

    def run(self, coro, timeout: Optional[float] = None):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)


def list_paired_windows() -> List[DeviceInfo]:
    """Every paired classic Bluetooth device with its current connection state."""
    from winrt.windows.devices.bluetooth import BluetoothDevice, BluetoothConnectionStatus
    from winrt.windows.devices.enumeration import DeviceInformation

    async def go() -> List[DeviceInfo]:
        sel = BluetoothDevice.get_device_selector_from_pairing_state(True)
        # winrt-python picks the overload by argument COUNT, and the 1-argument one is find_all_async(DeviceClass), which
        # fails with "'str' object cannot be interpreted as an integer". The 2-argument form is unambiguous.
        infos = None
        last: Optional[Exception] = None
        for call in (lambda: DeviceInformation.find_all_async(sel, []),
                     lambda: DeviceInformation.find_all_async(sel, ["System.Devices.Aep.IsConnected"]),
                     lambda: DeviceInformation.find_all_async(sel)):
            try:
                infos = await call()
                break
            except Exception as e:   # try the next way of calling it
                last = e
        if infos is None:
            raise last or TransportError("could not enumerate paired devices")
        out: List[DeviceInfo] = []
        for info in infos:
            try:
                dev = await BluetoothDevice.from_id_async(info.id)
                if dev is None:
                    continue
                out.append(DeviceInfo(info.id, dev.name or info.name or "", dev.connection_status == BluetoothConnectionStatus.CONNECTED,
                                      int(dev.bluetooth_address)))
                dev.close()
            except Exception:
                out.append(DeviceInfo(info.id, info.name or "", False))
        return out

    return _Loop.get().run(go(), 30)


class WinTransport(Transport):
    """RFCOMM stream socket to the headset's Sony control service (found by UUID, so no channel numbers needed)."""

    def __init__(self):
        self._lp = _Loop.get()
        self._sock = None
        self._reader = None
        self._writer = None
        self._closed = False

    def connect(self, device_id: str, service_uuid: str, timeout: float = 12.0) -> None:
        from winrt.windows.devices.bluetooth import BluetoothDevice, BluetoothCacheMode, BluetoothError
        from winrt.windows.devices.bluetooth.rfcomm import RfcommServiceId
        from winrt.windows.networking.sockets import StreamSocket, SocketProtectionLevel
        from winrt.windows.storage.streams import DataReader, DataWriter, InputStreamOptions

        async def go():
            dev = await BluetoothDevice.from_id_async(device_id)
            if dev is None:
                raise TransportError("Windows doesn't know this Bluetooth device any more (is it still paired?)")
            res = await dev.get_rfcomm_services_for_id_async(
                RfcommServiceId.from_uuid(uuid.UUID(service_uuid)), BluetoothCacheMode.UNCACHED)
            if res.error != BluetoothError.SUCCESS or len(res.services) == 0:
                raise TransportError("service not offered (%s)" % res.error.name)
            svc = res.services[0]
            sock = StreamSocket()
            try:
                await sock.connect_async(svc.connection_host_name, svc.connection_service_name,
                                         SocketProtectionLevel.BLUETOOTH_ENCRYPTION_ALLOW_NULL_AUTHENTICATION)
            except Exception:
                sock.close()
                raise
            reader = DataReader(sock.input_stream)
            reader.input_stream_options = InputStreamOptions.PARTIAL
            writer = DataWriter(sock.output_stream)
            return sock, reader, writer

        try:
            self._sock, self._reader, self._writer = self._lp.run(go(), timeout)
        except TransportError:
            raise
        except Exception as e:  # winrt raises OSError / RuntimeError with HRESULT text
            raise TransportError(str(e) or e.__class__.__name__) from e

    def read(self) -> bytes:
        if self._closed or self._reader is None:
            return b""

        async def go() -> bytes:
            n = await self._reader.load_async(1024)
            if n == 0:
                return b""
            buf = bytearray(n)
            self._reader.read_bytes(buf)
            return bytes(buf)

        try:
            return self._lp.run(go())
        except Exception:
            return b""

    def write(self, data: bytes) -> None:
        if self._closed or self._writer is None:
            raise TransportError("Not connected")

        async def go():
            self._writer.write_bytes(data)
            await self._writer.store_async()

        try:
            self._lp.run(go(), 8)
        except Exception as e:
            raise TransportError(str(e) or "write failed") from e

    def close(self) -> None:
        self._closed = True
        for obj in (self._reader, self._writer, self._sock):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------------------------------------------
# Simulated headset (tests, --demo)
# ---------------------------------------------------------------------------------------------------------------
class MockHeadset(Transport):
    """Speaks just enough of v1/v2 to exercise the app: init, noise control, battery, codec, equalizer, connection
    quality and background music. Not a model of any real device."""

    def __init__(self, proto: P.Proto = P.Proto.V2, adaptive: bool = True, earbuds: bool = True,
                 has_bgm: bool = True, has_conn: bool = True, eq_kind: Optional[P.EqKind] = None):
        self.proto = proto
        self.adaptive = adaptive and proto == P.Proto.V2
        self.earbuds = earbuds
        self.has_bgm = has_bgm and proto == P.Proto.V2
        self.has_conn = has_conn
        self.eq_kind = eq_kind or (P.EqKind.SIX_V1 if proto == P.Proto.V1 else P.EqKind.SIX_V2)
        self.rx: "queue.Queue[bytes]" = queue.Queue()
        self.sent: List[bytes] = []      # payloads of every DATA frame we received (for assertions)
        self.dev_seq = 0
        self._buf = bytearray()
        self._in = False
        self._closed = False
        self.mode, self.level, self.voice = P.NcMode.NOISE_CANCELLING, 12, False
        self.adapt_on, self.adapt_sens = False, 0
        self.codec, self.conn_mode, self.bgm_on, self.bgm_room = 2, 0, False, 1
        n = 10 if self.eq_kind == P.EqKind.TEN_V2 else 5
        self.eq_preset, self.eq_clear, self.eq_bands = 0x00, 0, [0] * n

    # -- Transport
    def read(self) -> bytes:
        if self._closed:
            return b""
        try:
            return self.rx.get()
        except Exception:
            return b""

    def write(self, data: bytes) -> None:
        if self._closed:
            raise TransportError("closed")
        for b in data:
            if b == P.START:
                self._buf.clear(); self._in = True
            elif self._in and b == P.END:
                self._in = False
                f = P.parse_frame(bytes(self._buf))
                if f is not None and f.type in (P.TYPE_DATA_MDR, P.TYPE_DATA_MDR_NO2):
                    self._on_data(f)
            elif self._in:
                self._buf.append(b)

    def close(self) -> None:
        self._closed = True
        self.rx.put(b"")

    # -- device side
    def _send(self, ftype: int, seq: int, payload: bytes) -> None:
        self.rx.put(P.build_frame(ftype, seq, payload))

    def push(self, payload: bytes) -> None:
        """Unsolicited notification from the 'headset'."""
        s = self.dev_seq
        self.dev_seq = 1 - s
        self._send(P.TYPE_DATA_MDR, s, payload)

    def _reply(self, payload: bytes) -> None:
        self.push(payload)

    def _on_data(self, f: P.Frame) -> None:
        p = f.payload
        self.sent.append(p)
        self._send(P.TYPE_ACK, (1 - f.seq) & 0xFF, b"")
        if not p:
            return
        op, sub = p[0], (p[1] if len(p) > 1 else 0)
        v1 = self.proto == P.Proto.V1
        if v1 and P.forbidden_on_v1(p):
            raise AssertionError("forbidden opcode sent to a v1 headset: %02x" % op)
        if op == P.INIT_REQUEST:
            self._reply(bytes([P.INIT_REPLY, 0x00, 0x01, 0x00]))
        elif op == P.NCASM_GET:
            self._reply(self._nc_state(sub))
        elif op == P.NCASM_SET:
            self._nc_set(p)
        elif op == P.BATTERY_GET and not v1:
            if sub in (P.BATTERY_SUB_DUAL, P.BATTERY_SUB_DUAL2) and self.earbuds:
                self._reply(bytes([P.BATTERY_RET, sub, 80, 0, 75, 1]))
            elif sub == P.BATTERY_SUB_CASE and self.earbuds:
                self._reply(bytes([P.BATTERY_RET, sub, 60, 0]))
            elif sub == P.BATTERY_SUB_SINGLE and not self.earbuds:
                self._reply(bytes([P.BATTERY_RET, 0x00, 55, 0]))
        elif op == P.BATTERY_GET_V1 and v1:
            self._reply(bytes([P.BATTERY_RET_V1, 0x00, 55, 0]))
        elif op == (P.CODEC_GET_V1 if v1 else P.CODEC_GET_V2):
            self._reply(bytes([P.codec_ret_id(self.proto), P.codec_ret_sub(self.proto), self.codec]))
        elif op == P.EQ_GET and sub == self.eq_kind.sub:
            self._reply(self._eq_reply())
        elif op == P.EQ_SET and sub == self.eq_kind.sub:
            self._eq_set(p)
        elif op == P.AUDIO_GET_CAP:
            if v1:
                return
            if sub == 0x05 and self.has_conn:
                self._reply(bytes([P.AUDIO_RET_CAP, 0x05, 3, 0, 1, 2, 0]))
            elif sub == 0x09 and self.has_bgm:
                self._reply(bytes([P.AUDIO_RET_CAP, 0x09, 0]))
        elif op == P.AUDIO_GET_STATUS and sub == 0x09 and self.has_bgm:
            self._reply(bytes([P.AUDIO_RET_STATUS, 0x09, 0x00]))
        elif op == P.AUDIO_GET_PARAM:
            if sub == (P.CONN_TYPE_V1 if v1 else 0x05) and self.has_conn:
                self._reply(bytes([P.AUDIO_RET_PARAM, sub, 0, self.conn_mode]) if v1 else bytes([P.AUDIO_RET_PARAM, sub, self.conn_mode, 0]))
            elif sub == 0x09 and self.has_bgm:
                self._reply(bytes([P.AUDIO_RET_PARAM, 0x09, 0 if self.bgm_on else 1, self.bgm_room]))
        elif op == P.AUDIO_SET_PARAM:
            if sub == P.CONN_TYPE_V1 and v1:
                self.conn_mode = p[3]
            elif sub == 0x05:
                self.conn_mode = p[2]
                self.codec = {0: 2, 1: 1, 2: 1}.get(p[2], 2)
            elif sub == 0x09:
                self.bgm_on, self.bgm_room = p[2] == 0, p[3]

    def _nc_state(self, sub: int) -> bytes:
        if self.proto == P.Proto.V1:
            nc_val = 2 if self.mode == P.NcMode.NOISE_CANCELLING else 0
            eff = 0 if self.mode == P.NcMode.OFF else P.V1_EFFECT_ON
            lvl = self.level if self.mode == P.NcMode.AMBIENT else 0
            return bytes([P.NCASM_RET, 0x02, eff, 2, nc_val, 1, int(self.voice), lvl])
        base = bytes([P.NCASM_RET, sub, 1, 0 if self.mode == P.NcMode.OFF else 1, 1 if self.mode == P.NcMode.AMBIENT else 0,
                      int(self.voice), self.level])
        if sub == P.NCASM_TYPE_NA:
            return base + bytes([int(self.adapt_on), self.adapt_sens])
        return base

    def _nc_set(self, p: bytes) -> None:
        if self.proto == P.Proto.V1:
            eff, nc_val, voice, lvl = p[2], p[4], p[6], p[7]
            self.mode = P.NcMode.OFF if eff == 0 else P.NcMode.NOISE_CANCELLING if nc_val else P.NcMode.AMBIENT
            self.voice = bool(voice)
            if lvl:
                self.level = lvl
        else:
            typ = p[1]
            self.mode = P.NcMode.OFF if p[3] == 0 else P.NcMode.AMBIENT if p[4] else P.NcMode.NOISE_CANCELLING
            self.voice, self.level = bool(p[5]), p[6]
            if typ == P.NCASM_TYPE_NA:
                self.adapt_on, self.adapt_sens = bool(p[7]), p[8]
        self.push(self._nc_state(P.NCASM_TYPE_V1 if self.proto == P.Proto.V1 else p[1]).replace(
            bytes([P.NCASM_RET]), bytes([P.NCASM_NTFY]), 1))

    def _eq_reply(self) -> bytes:
        k = self.eq_kind
        pid = k.manual_id if self.eq_preset == P.EQ_MANUAL else self.eq_preset
        if k == P.EqKind.TEN_V2:
            return bytes([P.EQ_RET, k.sub, pid, 10]) + bytes(b + 6 for b in self.eq_bands)
        return bytes([P.EQ_RET, k.sub, pid, 6, self.eq_clear + 10]) + bytes(b + 10 for b in self.eq_bands)

    def _eq_set(self, p: bytes) -> None:
        k = self.eq_kind
        pid = p[2]
        if k.proto == P.Proto.V1 and pid == 0xFF:
            pid = P.EQ_MANUAL
        self.eq_preset = pid
        if len(p) > 4:
            if k == P.EqKind.TEN_V2:
                self.eq_bands = [b - 6 for b in p[4:14]]
            else:
                self.eq_clear = p[4] - 10
                self.eq_bands = [b - 10 for b in p[5:10]]
