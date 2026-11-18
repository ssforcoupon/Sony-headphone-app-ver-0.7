import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from sonybridge import protocol as P

TOOLS = pathlib.Path(__file__).resolve().parent.parent / "tools"
fails = 0
def check(name, ok):
    global fails
    fails += not ok
    print(("ok   " if ok else "FAIL ") + name)

# every golden frame from SonyBridge: build + parse round trip
for fn in ("golden_frames.txt", "golden_frames_xm4.txt"):
    for line in (TOOLS / fn).read_text().splitlines():
        if not line.strip() or line.startswith("#"): continue
        name, typ, seq, payload, frame = [x.strip() for x in line.split("|")]
        pb, want = bytes.fromhex(payload.replace(" ", "")), bytes.fromhex(frame.replace(" ", ""))
        got = P.build_frame(int(typ, 16), int(seq), pb)
        pf = P.parse_frame(want[1:-1])
        check(f"{fn} {name}", got == want and pf is not None and pf.payload == pb and pf.seq == int(seq))

golden = {}
for line in (TOOLS / "golden_frames_xm4.txt").read_text().splitlines():
    if line.strip() and not line.startswith("#"):
        n, _, _, payload, _ = [x.strip() for x in line.split("|")]
        golden[n] = bytes.fromhex(payload.replace(" ", ""))

check("xm4 ambient level 1", P.set_mode_v1(P.NcMode.AMBIENT, 1, False) == golden["ambient_level1"])
check("xm4 ambient level 20", P.set_mode_v1(P.NcMode.AMBIENT, 20, False) == golden["ambient_level20"])
check("xm4 noise cancel", P.set_mode_v1(P.NcMode.NOISE_CANCELLING, 10, False) == golden["noise_cancel"])
check("xm4 eq custom", P.set_eq_custom(P.EqKind.SIX_V1, 3, [-2, -1, 0, 1, 2]) == golden["eq_custom"])
check("xm4 eq preset bright", P.set_eq_preset(P.EqKind.SIX_V1, 0x10) == golden["eq_preset_bright"])
check("xm4 get frames", golden["battery_get"] == P.get_single_battery(P.Proto.V1) and
      golden["codec_get"] == P.get_codec(P.Proto.V1) and golden["eq_get"] == P.get_eq(P.EqKind.SIX_V1)
      and golden["ambient_get"] == P.get_state_v1())

check("escape round trip", P.parse_frame(P.build_frame(0x0C, 1, bytes([0x3C, 0x3D, 0x3E, 0, 255]))[1:-1]).payload == bytes([0x3C, 0x3D, 0x3E, 0, 255]))
check("bad checksum rejected", P.parse_frame(P.build_frame(0x0C, 1, b"\x01\x02")[1:-1][:-1] + b"\x00") is None)
check("v2 NC keeps level 7", P.set_mode(P.NcMode.NOISE_CANCELLING, 7, False) == bytes.fromhex("68 17 01 01 00 00 07"))
check("v2 off keeps level 7", P.set_mode(P.NcMode.OFF, 7, False) == bytes.fromhex("68 17 01 00 00 00 07"))
check("v2 ambient 7 + voice", P.set_mode(P.NcMode.AMBIENT, 7, True) == bytes.fromhex("68 17 01 01 01 01 07"))
check("v2 adaptive layout", P.set_mode(P.NcMode.AMBIENT, 5, False, True, 2, P.NCASM_TYPE_NA) == bytes.fromhex("68 19 01 01 01 00 05 01 02"))
st = P.parse_state(bytes.fromhex("67 17 01 01 01 00 0c"))
check("parse v2 state", st.mode == P.NcMode.AMBIENT and st.level == 12 and not st.voice)
st = P.parse_state(bytes.fromhex("67 19 01 01 00 00 03 01 01"), P.NCASM_TYPE_NA)
check("parse v2 adaptive state", st.mode == P.NcMode.NOISE_CANCELLING and st.adaptive is True and st.sensitivity == 1)
check("parse v1 state", P.parse_state_v1(bytes.fromhex("67 02 11 02 00 01 00 08")).level == 8)
check("v1 forbids 0x22 and 0x30..0x3F", P.forbidden_on_v1(b"\x22\x00") and P.forbidden_on_v1(b"\x31") and not P.forbidden_on_v1(b"\x10\x00"))
check("dual battery", P.parse_dual_battery(bytes.fromhex("23 09 50 00 4b 01")) == P.Battery(80, 75, -1, False, True, False))
check("codec names", P.codec_name(2) == "AAC" and P.codec_name(0x10) == "LDAC")
check("ten-band eq parse", P.parse_eq(bytes.fromhex("57 04 a0 0a 06 07 08 09 0a 0b 0c 05 04 03"), P.EqKind.TEN_V2).bands == [0, 1, 2, 3, 4, 5, 6, -1, -2, -3])
check("ten-band eq set", P.set_eq_custom(P.EqKind.TEN_V2, 0, [0] * 10) == bytes.fromhex("58 04 a0 0a") + bytes([6] * 10))
check("conn set v2 le-audio low latency", P.set_conn_mode(P.Proto.V2, 5, 2) == bytes.fromhex("E8 05 02 00"))
check("conn set v2 classic", P.set_conn_mode(P.Proto.V2, 0, 1) == bytes.fromhex("E8 00 01"))
check("conn set v1", P.set_conn_mode(P.Proto.V1, 1, 1) == bytes.fromhex("E8 01 00 01"))
check("conn cap list", P.parse_conn_cap(bytes.fromhex("E1 05 03 00 01 02 01 00"), 5) == [0, 1, 2])
check("conn mode v1/v2 parse", P.parse_conn_mode(bytes.fromhex("E7 01 00 01"), P.Proto.V1) == 1 and P.parse_conn_mode(bytes.fromhex("E7 05 02 00"), P.Proto.V2) == 2)
check("bgm set/parse", P.set_bgm(3, True, 2) == bytes.fromhex("E8 03 00 02") and P.parse_bgm(bytes.fromhex("E7 03 00 00"), 3) == (True, 0))
check("bgm availability", P.parse_bgm_available(bytes.fromhex("E3 03 00"), 3) is True and P.parse_bgm_available(bytes.fromhex("E5 03 01 00"), 3) is False)
sys.exit(1 if fails else 0)
