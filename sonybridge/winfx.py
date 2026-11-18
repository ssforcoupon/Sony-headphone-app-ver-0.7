"""Windows-only window effects through ctypes: sharp rendering on high-DPI screens, blur-behind (classic or acrylic) and
a rounded window shape. Every function is safe to call anywhere: off Windows, or if Windows refuses, it returns False and
the widget simply stays a plain solid window."""
from __future__ import annotations

import sys

IS_WINDOWS = sys.platform == "win32"


def set_dpi_aware() -> None:
    """Call BEFORE creating the Tk window, otherwise Windows bitmap-stretches the widget on 125%/150% screens."""
    if not IS_WINDOWS:
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def toplevel_hwnd(root) -> int:
    """The real top-level window handle behind a Tk root (winfo_id is the inner child window)."""
    if not IS_WINDOWS:
        return 0
    import ctypes
    root.update_idletasks()
    inner = root.winfo_id()
    try:
        top = ctypes.windll.user32.GetAncestor(inner, 2)  # GA_ROOT
    except Exception:
        top = 0
    return int(top or inner)


def apply_blur(hwnd: int, mode: str, tint_rgb=(255, 255, 255), tint_alpha: int = 90) -> bool:
    """mode: 'off' | 'tint' (see-through tinted glass, no blur) | 'classic' (gaussian blur behind) | 'acrylic' (blur + tint).
    Returns True if Windows accepted it.
    Uses the undocumented SetWindowCompositionAttribute, the same call Windows' own shell uses."""
    if not IS_WINDOWS or not hwnd:
        return False
    import ctypes
    from ctypes import POINTER, Structure, byref, c_int, c_size_t, c_uint, c_void_p, sizeof

    class ACCENT_POLICY(Structure):
        _fields_ = [("AccentState", c_int), ("AccentFlags", c_int), ("GradientColor", c_uint), ("AnimationId", c_int)]

    class WINCOMPATTRDATA(Structure):
        _fields_ = [("Attribute", c_int), ("Data", POINTER(ACCENT_POLICY)), ("SizeOfData", c_size_t)]

    state = {"off": 0, "tint": 2, "classic": 3, "acrylic": 4}.get(mode, 0)
    r, g, b = tint_rgb
    policy = ACCENT_POLICY(state, 2 if state == 2 else 0, (int(tint_alpha) << 24) | (b << 16) | (g << 8) | r, 0)  # colour is 0xAABBGGRR
    data = WINCOMPATTRDATA(19, ctypes.pointer(policy), sizeof(policy))  # 19 = WCA_ACCENT_POLICY
    try:
        fn = ctypes.windll.user32.SetWindowCompositionAttribute
        fn.argtypes = [c_void_p, POINTER(WINCOMPATTRDATA)]
        fn.restype = c_int
        return bool(fn(hwnd, byref(data)))
    except Exception:
        return False


def round_window(hwnd: int, width: int, height: int, radius: int) -> bool:
    """Clips the window to a rounded rectangle (works on Windows 10 and 11)."""
    if not IS_WINDOWS or not hwnd:
        return False
    import ctypes
    from ctypes import c_int, c_void_p
    try:
        gdi, user = ctypes.windll.gdi32, ctypes.windll.user32
        gdi.CreateRoundRectRgn.argtypes = [c_int] * 6
        gdi.CreateRoundRectRgn.restype = c_void_p
        user.SetWindowRgn.argtypes = [c_void_p, c_void_p, c_int]
        user.SetWindowRgn.restype = c_int
        rgn = gdi.CreateRoundRectRgn(0, 0, int(width) + 1, int(height) + 1, int(radius) * 2, int(radius) * 2)
        if not rgn:
            return False
        return bool(user.SetWindowRgn(hwnd, rgn, True))  # the system owns the region from here on
    except Exception:
        return False


def windows_build() -> int:
    try:
        return int(sys.getwindowsversion().build)  # type: ignore[attr-defined]
    except Exception:
        return 0


def round_corners_dwm(hwnd: int) -> bool:
    """Windows 11 (build 22000+): asks the window manager itself to round the window, which also rounds the blurred glass
    behind it (SetWindowRgn doesn't clip Acrylic). The radius is fixed by Windows (about 8 px at 100 % scale)."""
    if not IS_WINDOWS or not hwnd or windows_build() < 22000:
        return False
    import ctypes
    from ctypes import byref, c_int, c_void_p
    try:
        fn = ctypes.windll.dwmapi.DwmSetWindowAttribute
        fn.argtypes = [c_void_p, c_int, c_void_p, c_int]
        fn.restype = c_int
        pref = c_int(2)                      # DWMWCP_ROUND
        return fn(hwnd, 33, byref(pref), 4) == 0   # DWMWA_WINDOW_CORNER_PREFERENCE
    except Exception:
        return False


def clear_region(hwnd: int) -> None:
    if IS_WINDOWS and hwnd:
        import ctypes
        try:
            ctypes.windll.user32.SetWindowRgn(hwnd, None, True)
        except Exception:
            pass


def ensure_toolwindow(hwnd: int) -> bool:
    """Checks (cheaply) that the window is still a tool window and fixes it if Tk has rebuilt the styles since. Returns True
    when it had to fix it. Called regularly because Tk resets the style whenever it changes -alpha/-topmost/etc."""
    if not IS_WINDOWS or not hwnd:
        return False
    import ctypes
    from ctypes import c_int, c_long, c_void_p
    try:
        u = ctypes.windll.user32
        u.GetWindowLongW.argtypes = [c_void_p, c_int]
        u.GetWindowLongW.restype = c_long
        u.SetWindowLongW.argtypes = [c_void_p, c_int, c_long]
        u.SetWindowLongW.restype = c_long
        u.ShowWindow.argtypes = [c_void_p, c_int]
        ex = u.GetWindowLongW(hwnd, -20)
        if (ex & 0x80) and not (ex & 0x40000):
            return False
        u.ShowWindow(hwnd, 0)                                   # SW_HIDE: Windows only drops the taskbar button on a re-show
        u.SetWindowLongW(hwnd, -20, (ex | 0x80) & ~0x40000)
        u.ShowWindow(hwnd, 8)                                   # SW_SHOWNA (no focus steal)
        return True
    except Exception:
        return False


def hide_from_taskbar(hwnd: int) -> bool:
    """Makes the window a 'tool window' so it gets no taskbar button and doesn't show in Alt+Tab. Call it while the window
    is hidden (root.withdraw()) and show it again afterwards, otherwise Windows keeps the old button."""
    if not IS_WINDOWS or not hwnd:
        return False
    import ctypes
    from ctypes import c_int, c_long, c_void_p
    try:
        u = ctypes.windll.user32
        u.GetWindowLongW.argtypes = [c_void_p, c_int]
        u.GetWindowLongW.restype = c_long
        u.SetWindowLongW.argtypes = [c_void_p, c_int, c_long]
        u.SetWindowLongW.restype = c_long
        ex = u.GetWindowLongW(hwnd, -20)                       # GWL_EXSTYLE
        u.SetWindowLongW(hwnd, -20, (ex | 0x80) & ~0x40000)    # + WS_EX_TOOLWINDOW, - WS_EX_APPWINDOW
        return True
    except Exception:
        return False
