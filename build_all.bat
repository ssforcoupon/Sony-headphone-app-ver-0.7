@echo off
rem Builds everything on Windows: dist\SonyBridgeWindows.exe, dist\SonyHeadphonesWidget-Setup.exe (if Inno Setup 6
rem is installed: https://jrsoftware.org/isdl.php) and dist\SonyHeadphonesWidget-Portable.zip
cd /d "%~dp0"
py -3 -m pip install -q --disable-pip-version-check -r requirements.txt pyinstaller || exit /b 1
py -3 tests\test_protocol.py >nul || (echo protocol tests failed & exit /b 1)
py -3 tests\test_headset_mock.py >nul || (echo controller tests failed & exit /b 1)
py -3 -m PyInstaller --noconsole --onefile --name SonyBridgeWindows --collect-submodules winrt --collect-binaries winrt --hidden-import pystray._win32 main.py || exit /b 1
dist\SonyBridgeWindows.exe --selftest
if errorlevel 1 (echo The built exe is missing winrt or tkinter - see README & exit /b 1)

for /f %%v in ('py -3 -c "import sonybridge;print(sonybridge.__version__)"') do set VER=%%v
set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if defined ISCC ("%ISCC%" /DAppVersion=%VER% installer\SonyBridgeWindows.iss) else (echo Inno Setup 6 not found - skipping the installer, portable zip only)

if exist dist\portable rmdir /s /q dist\portable
mkdir dist\portable
copy dist\SonyBridgeWindows.exe dist\portable\ >nul
copy installer\portable.txt dist\portable\ >nul
powershell -NoProfile -Command "Compress-Archive -Path dist\portable\* -DestinationPath dist\SonyHeadphonesWidget-Portable.zip -Force"
echo.
echo Done. See the dist folder.
