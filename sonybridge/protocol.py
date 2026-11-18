"""Sony MDR protocol (v1 + v2) over RFCOMM. Port of the Android app's SonyProtocol.kt (itself from SonyBridge 0.4.0 and
the public libmdr tables). No I/O in here: only byte builders and parsers, so it can be tested without hardware."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import List, Optional, Tuple

START, END, ESC = 0x3E, 0x3C, 0x3D
TYPE_ACK, TYPE_DATA_MDR, TYPE_DATA_MDR_NO2 = 0x01, 0x0C, 0x0E
MAX_FRAME = 2048

SERVICE_UUID_V1 = "96CC203E-5068-46AD-B32D-E316F5E069BA"  # WH-1000XM2/3/4, XB900N, MDR-...
SERVICE_UUID_V2 = "956C7B26-D49A-4BA8-B03F-B17D393CB6E2"  # everything newer


class Proto(Enum):
    V1 = 1
    V2 = 2


class NcMode(Enum):
    NOISE_CANCELLING = "Noise cancelling"
    AMBIENT = "Ambient"
    OFF = "Off"


# opcodes ---------------------------------------------------------------------------------------------------
INIT_REQUEST, INIT_REPLY = 0x00, 0x01
NCASM_GET, NCASM_RET, NCASM_SET, NCASM_NTFY = 0x66, 0x67, 0x68, 0x69
NCASM_TYPE, NCASM_TYPE_NA, NCASM_TYPE_V1 = 0x17, 0x19, 0x02
SENS_STANDARD, SENS_HIGH, SENS_LOW = 0, 1, 2
BATTERY_GET, BATTERY_RET, BATTERY_NTFY = 0x22, 0x23, 0x25
BATTERY_SUB_DUAL2, BATTERY_SUB_DUAL, BATTERY_SUB_CASE, BATTERY_SUB_SINGLE = 0x01, 0x09, 0x0A, 0x00
V1_NC_DUAL_SINGLE_OFF, V1_ASM_LEVEL_ADJUSTMENT, V1_EFFECT_ON = 2, 1, 0x11
BATTERY_GET_V1, BATTERY_RET_V1, BATTERY_NTFY_V1 = 0x10, 0x11, 0x13
CODEC_GET_V1, CODEC_RET_V1, CODEC_GET_V2, CODEC_RET_V2, CODEC_NTFY_V2 = 0x18, 0x19, 0x12, 0x13, 0x15
AUDIO_GET_CAP, AUDIO_RET_CAP, AUDIO_GET_STATUS, AUDIO_RET_STATUS = 0xE0, 0xE1, 0xE2, 0xE3
AUDIO_NTFY_STATUS, AUDIO_GET_PARAM, AUDIO_RET_PARAM, AUDIO_SET_PARAM, AUDIO_NTFY_PARAM = 0xE5, 0xE6, 0xE7, 0xE8, 0xE9
CONN_TYPES_V2 = (0x05, 0x02, 0x00)
CONN_TYPE_V1 = 0x01
BGM_TYPES = (0x09, 0x03)
EQ_GET, EQ_RET, EQ_SET, EQ_NTFY = 0x56, 0x57, 0x58, 0x59
EQ_MANUAL = 0xA0
MIN_VOICE_FOCUS_LEVEL = 2

EQ_PRESETS: List[Tuple[int, str]] = [
    (0x00, "Off"), (0x10, "Bright"), (0x11, "Excited"), (0x12, "Mellow"), (0x13, "Relaxed"),
    (0x14, "Vocal"), (0x15, "Treble boost"), (0x16, "Bass boost"), (0x17, "Speech"), (EQ_MANUAL, "Custom"),
]


def eq_preset_name(pid: int) -> str:
    return next((n for i, n in EQ_PRESETS if i == pid), "Preset 0x%02X" % pid)


class EqKind(Enum):
    # name: (sub, proto, has_clear_bass, min, max, labels)
    SIX_V1 = (0x01, Proto.V1, True, -10, 10, ["400", "1k", "2.5k", "6.3k", "16k"])
    SIX_V2 = (0x00, Proto.V2, True, -10, 10, ["400", "1k", "2.5k", "6.3k", "16k"])
    TEN_V2 = (0x04, Proto.V2, False, -6, 6, ["31", "63", "125", "250", "500", "1k", "2k", "4k", "8k", "16k"])

    @property
    def sub(self): return self.value[0]
    @property
    def proto(self): return self.value[1]
    @property
    def has_clear_bass(self): return self.value[2]
    @property
    def min(self): return self.value[3]
    @property
    def max(self): return self.value[4]
    @property
    def labels(self): return self.value[5]
    @property
    def manual_id(self): return 0xFF if self.proto == Proto.V1 else 0xA0


class ConnMode(Enum):
    SOUND_QUALITY = (0, "Priority on sound quality", "Best codec your PC/phone supports (AAC / LDAC), automatic")
    STABLE = (1, "Priority on stable connection", "Steadier link, falls back to SBC - use if sound cuts out")
    LOW_LATENCY = (2, "Low latency", "Least delay for games and video. The equalizer may be unavailable")

    @property
    def id(self): return self.value[0]
    @property
    def title(self): return self.value[1]
    @property
    def detail(self): return self.value[2]

    @staticmethod
    def of(i: int) -> Optional["ConnMode"]:
        return next((m for m in ConnMode if m.id == i), None)


class RoomSize(Enum):
    SMALL = (0, "Small")
    MEDIUM = (1, "Medium")
    LARGE = (2, "Large")

    @property
    def id(self): return self.value[0]
    @property
    def title(self): return self.value[1]

    @staticmethod
    def of(i: int) -> Optional["RoomSize"]:
        return next((r for r in RoomSize if r.id == i), None)


@dataclass
class Frame:
    type: int
    seq: int
    payload: bytes


@dataclass
class Battery:
    left: int = -1
    right: int = -1
    case: int = -1
    left_charging: bool = False
    right_charging: bool = False
    case_charging: bool = False
    single: int = -1
    single_charging: bool = False


@dataclass
class State:
    mode: NcMode
    level: int
    voice: bool
    inquired_type: int = NCASM_TYPE
    adaptive: Optional[bool] = None
    sensitivity: int = SENS_STANDARD
    v1_nc_type: int = V1_NC_DUAL_SINGLE_OFF
    v1_asm_type: int = V1_ASM_LEVEL_ADJUSTMENT


@dataclass
class ConnState:
    type: int
    modes: List[int]
    mode: int


@dataclass
class ListenState:
    type: int
    bgm: bool
    room: int
    available: bool


@dataclass
class EqState:
    kind: EqKind
    preset: int
    clear_bass: int
    bands: List[int] = field(default_factory=list)


# framing -----------------------------------------------------------------------------------------------------
_ESC_MAP = {0x3C: 0x2C, 0x3D: 0x2D, 0x3E: 0x2E}
_UNESC_MAP = {v: k for k, v in _ESC_MAP.items()}


def escape(src: bytes) -> bytes:
    out = bytearray()
    for b in src:
        if b in _ESC_MAP:
            out += bytes((ESC, _ESC_MAP[b]))
        else:
            out.append(b)
    return bytes(out)


def unescape(src: bytes) -> Optional[bytes]:
    out = bytearray()
    i = 0
    while i < len(src):
        c = src[i]
        if c == ESC:
            if i == len(src) - 1:
                return None
            i += 1
            if src[i] not in _UNESC_MAP:
                return None
            out.append(_UNESC_MAP[src[i]])
        else:
            out.append(c)
        i += 1
    return bytes(out)


def build_frame(ftype: int, seq: int, payload: bytes) -> bytes:
    raw = bytes([ftype, seq & 0xFF]) + len(payload).to_bytes(4, "big") + payload
    frame = bytes([START]) + escape(raw + bytes([sum(raw) & 0xFF])) + bytes([END])
    if len(frame) > MAX_FRAME:
        raise ValueError("frame too large")
    return frame


def parse_frame(body: bytes) -> Optional[Frame]:
    """[body] = everything between START and END, still escaped."""
    u = unescape(body)
    if u is None or len(u) < 7:
        return None
    size = int.from_bytes(u[2:6], "big")
    if size != len(u) - 7 or u[6 + size] != (sum(u[:6 + size]) & 0xFF):
        return None
    return Frame(u[0], u[1], bytes(u[6:6 + size]))


def forbidden_on_v1(payload: bytes) -> bool:
    """0x22 powers a v1 headset OFF (v2: battery); 0x30-0x3F is firmware update."""
    if not payload:
        return False
    op = payload[0]
    return op == 0x22 or 0x30 <= op <= 0x3F


# noise control -----------------------------------------------------------------------------------------------
def init_request() -> bytes:
    return bytes([INIT_REQUEST, 0x00])


def get_state(inquired_type: int) -> bytes:
    return bytes([NCASM_GET, inquired_type])


def set_mode(mode: NcMode, ambient_level: int, voice: bool, adaptive: bool = False,
             sensitivity: int = SENS_STANDARD, typ: int = NCASM_TYPE) -> bytes:
    """68 <type> 01 <effect> <NC 0/Ambient 1> <voice> <level> [<adaptive> <sensitivity>]; the stored ambient level is
    sent in every mode (the headphones keep it, as libmdr does)."""
    level = max(1, min(20, ambient_level))
    effect = 0 if mode == NcMode.OFF else 1
    kind = 1 if mode == NcMode.AMBIENT else 0
    v = 1 if (mode == NcMode.AMBIENT and voice and level > MIN_VOICE_FOCUS_LEVEL) else 0
    base = bytes([NCASM_SET, typ, 0x01, effect, kind, v, level])
    if typ != NCASM_TYPE_NA:
        return base
    return base + bytes([1 if adaptive else 0, max(0, min(2, sensitivity))])


def parse_state(resp: bytes, inquired_type: int = NCASM_TYPE) -> Optional[State]:
    if len(resp) < 7 or resp[2] != 0x01:
        return None
    if inquired_type == NCASM_TYPE_NA and len(resp) < 9:
        return None
    on, ambient, voice, level = resp[3] != 0, resp[4] != 0, resp[5] != 0, resp[6]
    mode = NcMode.OFF if not on else NcMode.AMBIENT if ambient else NcMode.NOISE_CANCELLING
    if inquired_type != NCASM_TYPE_NA:
        return State(mode, level, voice, inquired_type)
    sens = resp[8]
    return State(mode, level, voice, inquired_type, resp[7] != 0, sens if sens in (0, 1, 2) else SENS_STANDARD)


def get_state_v1() -> bytes:
    return bytes([NCASM_GET, NCASM_TYPE_V1])


def set_mode_v1(mode: NcMode, ambient_level: int, voice: bool,
                nc_type: int = V1_NC_DUAL_SINGLE_OFF, asm_type: int = V1_ASM_LEVEL_ADJUSTMENT) -> bytes:
    effect = 0 if mode == NcMode.OFF else V1_EFFECT_ON
    nc_value = (2 if nc_type == V1_NC_DUAL_SINGLE_OFF else 1) if mode == NcMode.NOISE_CANCELLING else 0
    level = max(1, min(20, ambient_level)) if mode == NcMode.AMBIENT else 0
    v = 1 if (mode == NcMode.AMBIENT and voice) else 0
    return bytes([NCASM_SET, NCASM_TYPE_V1, effect, nc_type, nc_value, asm_type, v, level])


def parse_state_v1(resp: bytes) -> Optional[State]:
    if len(resp) < 8 or resp[1] != NCASM_TYPE_V1:
        return None
    enabled, nc_type, nc_value, asm_type, voice, level = resp[2] != 0, resp[3], resp[4], resp[5], resp[6] != 0, resp[7]
    if not enabled:
        mode = NcMode.OFF
    elif nc_value != 0:
        mode = NcMode.NOISE_CANCELLING
    elif level > 0:
        mode = NcMode.AMBIENT
    else:
        mode = NcMode.OFF
    return State(mode, level, voice and mode == NcMode.AMBIENT, NCASM_TYPE_V1,
                 v1_nc_type=nc_type if 0 <= nc_type <= 2 else V1_NC_DUAL_SINGLE_OFF,
                 v1_asm_type=asm_type if asm_type in (0, 1) else V1_ASM_LEVEL_ADJUSTMENT)


# battery -------------------------------------------------------------------------------------------------------
def get_battery(sub: int) -> bytes:
    return bytes([BATTERY_GET, sub])


def parse_dual_battery(resp: bytes) -> Optional[Battery]:
    if len(resp) < 6:
        return None
    l, r = resp[2], resp[4]
    return Battery(l if l > 0 else -1, r if r > 0 else -1, -1, resp[3] == 1, resp[5] == 1, False)


def parse_case_battery(resp: bytes) -> Optional[Tuple[int, bool]]:
    if len(resp) < 4:
        return None
    return (resp[2] if resp[2] > 0 else -1, resp[3] == 1)


def get_single_battery(proto: Proto) -> bytes:
    return bytes([BATTERY_GET_V1, 0x00]) if proto == Proto.V1 else bytes([BATTERY_GET, BATTERY_SUB_SINGLE])


def parse_single_battery(resp: bytes) -> Optional[Battery]:
    if len(resp) < 4 or resp[2] > 100:
        return None
    return Battery(single=resp[2] if resp[2] > 0 else -1, single_charging=resp[3] == 1)


# codec -----------------------------------------------------------------------------------------------------------
def get_codec(proto: Proto) -> bytes:
    return bytes([CODEC_GET_V1, 0x00]) if proto == Proto.V1 else bytes([CODEC_GET_V2, 0x02])


def codec_ret_id(proto: Proto) -> int:
    return CODEC_RET_V1 if proto == Proto.V1 else CODEC_RET_V2


def codec_ret_sub(proto: Proto) -> int:
    return 0x00 if proto == Proto.V1 else 0x02


def parse_codec(resp: bytes) -> Optional[int]:
    return resp[2] if len(resp) >= 3 else None


def codec_name(code: int) -> str:
    return {0x00: "Not playing", 0x01: "SBC", 0x02: "AAC", 0x10: "LDAC", 0x20: "aptX", 0x21: "aptX HD"}.get(
        code, "Codec 0x%02X" % code)


# equalizer -------------------------------------------------------------------------------------------------------
def get_eq(kind: EqKind) -> bytes:
    return bytes([EQ_GET, kind.sub])


def parse_eq(resp: bytes, kind: EqKind) -> Optional[EqState]:
    if len(resp) < 4 or resp[1] != kind.sub:
        return None
    preset = resp[2]
    if kind.proto == Proto.V1 and preset == 0xFF:
        preset = EQ_MANUAL
    count = resp[3]
    clamp = lambda v: max(kind.min, min(kind.max, v))
    if kind == EqKind.TEN_V2:
        if count != 10 or len(resp) < 4 + count:
            return None
        return EqState(kind, preset, 0, [clamp(resp[4 + i] - 6) for i in range(10)])
    if count < 6 or len(resp) < 10:
        return None
    return EqState(kind, preset, clamp(resp[4] - 10), [clamp(resp[5 + i] - 10) for i in range(5)])


def set_eq_preset(kind: EqKind, preset: int) -> bytes:
    pid = kind.manual_id if preset == EQ_MANUAL else preset
    return bytes([EQ_SET, kind.sub, pid, 0x00])


def set_eq_custom(kind: EqKind, clear_bass: int, bands: List[int]) -> bytes:
    clamp = lambda v: max(kind.min, min(kind.max, v))
    out = bytearray([EQ_SET, kind.sub, kind.manual_id])
    if kind == EqKind.TEN_V2:
        out.append(10)
        out += bytes(clamp(bands[i] if i < len(bands) else 0) + 6 for i in range(10))
    else:
        out.append(6)
        out.append(clamp(clear_bass) + 10)
        out += bytes(clamp(bands[i] if i < len(bands) else 0) + 10 for i in range(5))
    return bytes(out)


# connection quality / sound quality mode -----------------------------------------------------------------------------
def get_conn_cap(typ: int) -> bytes:
    return bytes([AUDIO_GET_CAP, typ])


def parse_conn_cap(resp: bytes, typ: int) -> Optional[List[int]]:
    if len(resp) < 2 or resp[1] != typ:
        return None
    if typ != 0x05:
        return [ConnMode.SOUND_QUALITY.id, ConnMode.STABLE.id]
    if len(resp) < 3 or len(resp) < 3 + resp[2]:
        return None
    modes = []
    for i in range(resp[2]):
        m = resp[3 + i]
        if ConnMode.of(m) is not None and m not in modes:
            modes.append(m)
    return modes or None


def get_conn_mode(proto: Proto, typ: int) -> bytes:
    return bytes([AUDIO_GET_PARAM, CONN_TYPE_V1 if proto == Proto.V1 else typ])


def parse_conn_mode(resp: bytes, proto: Proto) -> Optional[int]:
    idx = 3 if proto == Proto.V1 else 2
    if len(resp) <= idx:
        return None
    return resp[idx] if ConnMode.of(resp[idx]) is not None else None


def set_conn_mode(proto: Proto, typ: int, mode: int) -> bytes:
    if proto == Proto.V1:
        return bytes([AUDIO_SET_PARAM, CONN_TYPE_V1, 0x00, mode])
    if typ == 0x05:
        return bytes([AUDIO_SET_PARAM, 0x05, mode, 0x00])
    return bytes([AUDIO_SET_PARAM, typ, mode])


# listening mode (standard / background music), v2 only ----------------------------------------------------------------
def get_bgm_cap(typ: int) -> bytes:
    return bytes([AUDIO_GET_CAP, typ])


def get_bgm_status(typ: int) -> bytes:
    return bytes([AUDIO_GET_STATUS, typ])


def get_bgm(typ: int) -> bytes:
    return bytes([AUDIO_GET_PARAM, typ])


def parse_bgm_available(resp: bytes, typ: int) -> Optional[bool]:
    if len(resp) < 3 or resp[1] != typ or resp[2] > 1:
        return None
    return resp[2] == 0


def parse_bgm(resp: bytes, typ: int) -> Optional[Tuple[bool, int]]:
    if len(resp) < 4 or resp[1] != typ or resp[2] > 1 or RoomSize.of(resp[3]) is None:
        return None
    return (resp[2] == 0, resp[3])


def set_bgm(typ: int, on: bool, room: int) -> bytes:
    return bytes([AUDIO_SET_PARAM, typ, 0 if on else 1, room])
