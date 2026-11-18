# Sony headphones - Windows desktop widget (v0.3.6)

A small always-on-top widget for Windows 10/11 that controls Sony headphones and earbuds over Bluetooth, no Sony app
needed. Windows port of the Android app (same protocol code, ported to Python).

## Installer and portable build
Two ways to get a ready-to-run program without installing Python:
- **SonyHeadphonesWidget-Setup.exe** - per-user installer (no administrator rights). One checkbox: *Start the widget
  automatically when I sign in to Windows* (ticked by default; it is the same switch as "Start with Windows" in the
  widget's right-click menu, so you can change it later from either place). Adds a Start-menu entry, optional desktop
  shortcut, and a normal uninstaller that also removes the startup entry. Your settings are kept on uninstall.
- **SonyHeadphonesWidget-Portable.zip** - unzip anywhere (USB stick too) and run `SonyBridgeWindows.exe`. A file named
  `portable.txt` next to the exe makes it keep settings in a `data` folder beside it instead of `%APPDATA%`.
  "Start with Windows" in the menu works here too (toggle it off/on again after moving the folder).
The app only ever runs one copy, so starting it by hand while it is also in startup does nothing extra.

**How to build them** (I could not build Windows programs in my Linux environment, so none of this is built yet):
- Easiest: put this folder in a GitHub repository, open *Actions > build-windows > Run workflow*; it runs the tests,
  builds the exe, checks the built exe can load its Bluetooth parts (`--selftest`), builds the installer and the
  portable zip, and attaches both as downloadable files.
- Or on a Windows PC with Python 3.11+: run `build_all.bat` (the installer part also needs Inno Setup 6, free).
- If `--selftest` fails in a build, a Windows Bluetooth package didn't get bundled: tell me the message and I'll adjust.

## Run it from source
1. Pair the headphones in Windows Settings > Bluetooth (the normal audio pairing is enough).
2. Install Python 3.11+ from python.org (keep "tcl/tk" ticked), then double-click `run.bat`.
   (`python main.py --demo v2` / `--demo v1` runs against a simulated headset, useful to look at the UI.)
3. Optional: `build_exe.bat` makes a single `SonyBridgeWindows.exe`.

## The widget
- Styled like the Android widget: rounded frosted-glass card, bold name, battery line, four round buttons with the
  person icons joined by lines, and a centred Connected / Disconnected line (dark, bold).
- Buttons: Noise cancel / Ambient / Auto ambient / Off. Headphones without auto ambient (WH-1000XM2/3/4) show only
  three buttons.
- While Ambient is on: level slider (1-20) and "Focus on voice". The level is read from the headphones and
  remembered per headphone.
- Drag to move (position is remembered). Right-click or the "..." button: Settings, Refresh, Choose headphones,
  Follow connected headphones, Pause (frees the link for Sony's own app), Always on top, Start with Windows, Quit.

## Settings window
- **Appearance:** Transparency (0-100 %), Frosted edge, Blur (None / Acrylic / Classic), 3D buttons, thin black outline,
  colours (follow Windows / light / dark), Always on top, Start with Windows, **Show tray icon** and
  **Start minimised in the tray**. Changes show live. Right-click the widget > "Minimise to tray" hides it; click the
  tray icon to show/hide, right-click it for Settings / Pause / Quit.
  Blur uses an undocumented Windows call: if the widget turns black or shows no effect, set Blur to None (the card is
  then drawn solid and Transparency fades the whole widget instead).
- **Listening mode:** Standard / Background music + room size (small / medium / large), on models that have it.
- **Sound quality:** codec in use, and Bluetooth connection quality: Priority on sound quality / Priority on stable
  connection / Low latency (only where the headphones list it). This is a setting on the headphones.
- **Equalizer:** presets + custom curve, 10 bands (31 Hz-16 kHz) or Clear Bass + 5 bands, detected from the headphones.
- **Status:** protocol, last error, "Retry feature detection".

## Is a "real" Windows 11 Widgets-board widget possible?
Only with a different build: the Widgets board needs a packaged (MSIX) app with a widget provider written in C#/C++
(Windows App SDK), signed and installed from a package. This project is a normal floating desktop widget instead.

## What is and isn't verified
- Protocol layer: 51 checks against SonyBridge's golden frames (including the WH-1000XM4 set), all pass (`tests/test_protocol.py`).
- Controller: 28 checks against simulated v1 and v2 headsets (`tests/test_headset_mock.py`): connect, protocol
  fallback, modes, ambient level, auto ambient, EQ, connection quality, background music, notifications, follow.
- `python tools/preview.py out.png` draws the widget with Pillow (no Windows needed) to check layout and colours.
  Blur, transparency, rounded corners and the tray icon are Windows effects and have **not** been seen on a real screen.
- The UI code ran only against a stub tkinter (`tests/test_gui_stub.py`): no real window was drawn, so layout and
  looks are unverified.
- **Not run on Windows or with real headphones.** The Windows Bluetooth part (`transport.py`) follows the
  `winrt` API stubs but is untested. Layouts for connection quality / background music come from libmdr, not Sony's app,
  and low latency is unverified on hardware.
- Windows allows one control connection at a time: close Sony's own app, or use Pause.

## If the headphones aren't found
Right-click the widget > **Open log file** (or Settings > Status > Open log file). It lists the paired devices Windows
reported and the exact error for each connection attempt. The log is `log.txt` in the data folder
(`%APPDATA%\SonyBridgeWindows`, or next to the exe in portable mode). Also: close Sony's own app first (the
headphones allow one control connection), and make sure they are paired in Windows Bluetooth settings.

## If the corners look square
Windows' Acrylic effect doesn't always follow the rounded window shape. In Settings > Appearance try **Classic blur**
(follows the shape reliably) or **None** (solid card, always clean). `log.txt` records what Windows reported for the
shape and effect ("window shape ... region=... glass=...").
