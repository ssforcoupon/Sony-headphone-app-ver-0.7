"""list_paired_windows against a fake winrt whose find_all_async behaves like the real one: the 1-argument call raises
TypeError (wrong overload), the 2-argument call works."""
import pathlib, sys, types, asyncio
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

class Status: CONNECTED = 1
class Info:
    def __init__(self, i, n): self.id, self.name = i, n
class Dev:
    def __init__(self, n, c): self.name, self.connection_status, self.bluetooth_address = n, (1 if c else 0), 5
    def close(self): pass
DEVS = {"a": Dev("WH-1000XM4", True), "b": Dev("Speaker", False)}

class DeviceInformation:
    @staticmethod
    async def find_all_async(*a):
        if len(a) == 1:
            raise TypeError("'str' object cannot be interpreted as an integer")
        return [Info("a", "WH-1000XM4"), Info("b", "Speaker")]
class BluetoothDevice:
    @staticmethod
    def get_device_selector_from_pairing_state(p): return "SEL"
    @staticmethod
    async def from_id_async(i): return DEVS[i]

bt = types.ModuleType("winrt.windows.devices.bluetooth"); bt.BluetoothDevice = BluetoothDevice
bt.BluetoothConnectionStatus = Status
en = types.ModuleType("winrt.windows.devices.enumeration"); en.DeviceInformation = DeviceInformation
for n, m in (("winrt", types.ModuleType("winrt")), ("winrt.windows", types.ModuleType("winrt.windows")),
             ("winrt.windows.devices", types.ModuleType("winrt.windows.devices")),
             ("winrt.windows.devices.bluetooth", bt), ("winrt.windows.devices.enumeration", en)):
    sys.modules[n] = m
from sonybridge import transport
devs = transport.list_paired_windows()
ok = [(d.name, d.connected) for d in devs] == [("WH-1000XM4", True), ("Speaker", False)]
print(("ok   " if ok else "FAIL ") + "paired devices listed despite the 1-arg overload problem")
sys.exit(0 if ok else 1)
