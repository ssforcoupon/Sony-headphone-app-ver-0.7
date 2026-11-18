@echo off
cd /d "%~dp0"
echo Installing the 9 small Bluetooth packages the widget needs (first run only)...
py -3 -m pip install -q --disable-pip-version-check -r requirements.txt || goto :err
start "" pythonw main.py
exit /b 0
:err
echo Install failed. You need Python 3.11+ from python.org (tick "Add python.exe to PATH" and keep tcl/tk).
pause
