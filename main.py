"""Sony headphones desktop widget for Windows 10/11.

    python main.py            real headphones (paired in Windows Bluetooth settings)
    python main.py --demo v2  simulated WF-1000XM6 style earbuds
    python main.py --demo v1  simulated WH-1000XM4
    python main.py --selftest checks that the Windows Bluetooth packages and tkinter are present (exit code 0 = ok)
"""
import os
import sys


def _trace_path():
    import tempfile
    return os.path.join(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir(), "selftest.log")


def build(demo=None):
    # imports live here (not at the top) so a problem in them is reported by the crash handler below instead of
    # showing a modal error box that nobody can click (a windowed exe on a build server would hang forever)
    from sonybridge import protocol as P
    from sonybridge.controller import UUIDS, Config, Headset
    from sonybridge.transport import DeviceInfo, MockHeadset
    if demo:
        proto = P.Proto.V1 if demo == "v1" else P.Proto.V2
        name = "WH-1000XM4" if proto == P.Proto.V1 else "WF-1000XM6"
        devs = [DeviceInfo("demo-" + name, name, True)]

        def opener(dev, p):
            from sonybridge.transport import TransportError
            if p != proto:
                raise TransportError("service not offered")
            return MockHeadset(proto, earbuds=proto == P.Proto.V2, eq_kind=P.EqKind.TEN_V2 if proto == P.Proto.V2 else None)

        import tempfile, pathlib
        return Headset(Config(pathlib.Path(tempfile.mkdtemp()) / "demo.json"), lambda: devs, opener)

    from sonybridge.transport import WinTransport, list_paired_windows

    def opener(dev, proto):
        t = WinTransport()
        t.connect(dev.id, UUIDS[proto])
        return t

    return Headset(Config(), list_paired_windows, opener)


_MUTEX = None


def single_instance() -> bool:
    """False when another copy is already running (so 'start with Windows' plus a manual start can't give two widgets).
    The same mutex name is used by the installer (AppMutex) to ask the user to close the app before updating."""
    global _MUTEX
    if sys.platform != "win32":
        return True
    import ctypes
    k = ctypes.windll.kernel32
    k.CreateMutexW.restype = ctypes.c_void_p
    _MUTEX = k.CreateMutexW(None, False, "SonyBridgeWindows_Single")
    return k.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def selftest() -> int:
    """Exit code 0 = ok. Writes a step-by-step trace to selftest.log next to the exe (it has no console) and dumps all
    thread stacks to it if the check takes more than 60 s, so a hang shows where it hangs."""
    import faulthandler
    f = _SELF_LOG
    faulthandler.dump_traceback_later(60, file=f, exit=True)

    def step(msg):
        f.write(msg + "\n")
    try:
        step("tkinter")
        import tkinter  # noqa: F401
        step("winrt")
        from winrt.windows.devices.bluetooth import BluetoothDevice  # noqa: F401
        from winrt.windows.devices.bluetooth.rfcomm import RfcommServiceId  # noqa: F401
        from winrt.windows.devices.enumeration import DeviceInformation  # noqa: F401
        from winrt.windows.networking.sockets import StreamSocket  # noqa: F401
        from winrt.windows.storage.streams import DataReader, DataWriter  # noqa: F401
        step("sonybridge.gui")
        from sonybridge import gui, transport  # noqa: F401
        step("tray packages")
        from sonybridge import tray
        step("tray available=%s" % tray.AVAILABLE)
        if tray.AVAILABLE:
            step("tray load")
            tray._load()
            step("tray image")
            tray.make_icon_image(True)
        step("ok")
        faulthandler.cancel_dump_traceback_later()
        return 0
    except Exception:
        import traceback
        step(traceback.format_exc())
        return 1


_SELF_LOG = None


def _main():
    global _SELF_LOG
    if "--selftest" in sys.argv:
        _SELF_LOG = open(_trace_path(), "w", buffering=1)
        _SELF_LOG.write("started\n")
        code = selftest()
        os._exit(code)      # don't wait for any leftover non-daemon threads
    if not single_instance():
        sys.exit(0)
    demo = None
    if "--demo" in sys.argv:
        i = sys.argv.index("--demo")
        demo = sys.argv[i + 1] if i + 1 < len(sys.argv) else "v2"
    from sonybridge.gui import WidgetApp
    WidgetApp(build(demo)).run()


if __name__ == "__main__":
    try:
        _main()
    except SystemExit:
        raise
    except BaseException:
        import traceback
        text = traceback.format_exc()
        try:
            with open(_trace_path() if "--selftest" in sys.argv else os.path.join(os.path.expanduser("~"), "SonyBridgeWindows-crash.log"),
                      "a") as fh:
                fh.write(text)
        except Exception:
            pass
        os._exit(1)
