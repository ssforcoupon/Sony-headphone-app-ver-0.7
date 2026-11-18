"""What we can tell about Sony models from their Bluetooth name. Only a HINT (which protocol to try first, whether to
show the Auto-ambient button before the headphones have been contacted); the headphones themselves have the last word."""
import re
from typing import Optional

_SONY = re.compile(r"^\s*(LE_)?(sony\s+)?(WH-|WF-|WI-|MDR-|LinkBuds|ULT\b|INZONE|h\.ear|NW-)", re.I)
_EARBUDS = re.compile(r"\bWF-|LinkBuds", re.I)
_LEGACY_V1 = re.compile(r"WH-1000XM[234]\b|WH-XB900N|MDR-", re.I)
_NO_AUTO_AMBIENT = re.compile(r"WH-1000XM[234]\b", re.I)


def is_sony(name: Optional[str]) -> bool:
    return bool(name and _SONY.search(name))


def is_earbuds(name: Optional[str]) -> bool:
    return bool(name and _EARBUDS.search(name))


def likely_v1(name: Optional[str]) -> bool:
    return bool(name and _LEGACY_V1.search(name))


def auto_ambient_by_name(name: Optional[str]) -> Optional[bool]:
    return False if name and _NO_AUTO_AMBIENT.search(name) else None


def display(name: Optional[str]) -> str:
    n = (name or "").strip()
    if n.startswith("LE_"):
        n = n[3:]
    return n or "Sony headphones"
