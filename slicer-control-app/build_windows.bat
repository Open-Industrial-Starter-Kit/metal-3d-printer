@echo off
REM ============================================================================
REM  Build PiccolaCNC.exe on Windows.  Double-click this file (or run it in a
REM  terminal).  Requires Python 3.10+ installed and on PATH
REM  (get it from https://www.python.org/downloads/ — tick "Add to PATH").
REM ============================================================================
setlocal
cd /d "%~dp0"

echo.
echo === Piccola CNC - Windows build ===
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    echo and make sure "Add python.exe to PATH" is checked during install.
    pause
    exit /b 1
)

echo [1/5] Creating build virtual environment...
python -m venv build-venv || goto :fail

echo [2/5] Installing dependencies...
call build-venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
pip install -r requirements.txt pyinstaller || goto :fail

REM The CAM tab serves vendored browser tools out of vendor\. EasyTrace5000 is
REM committed; Kiri:Moto has no published static build and must be produced by
REM its npm pipeline, so this step needs Node + git. It is NOT fatal: a missing
REM bundle only means that CAM tab reports it, and the rest of the app builds.
REM Open3D is the one dependency that can be absent on a machine where
REM everything else installed: it is ~284 MB of compiled code and its wheels
REM trail new Python releases. The app runs without it -- the .ply viewer's
REM Surface view is then disabled and says why -- but that is worth knowing
REM here rather than discovering it on the shop floor.
python -c "import open3d" >nul 2>nul
if errorlevel 1 (
    echo.
    echo [WARN] Open3D is not installed in the build environment.
    echo        The app will build and run, but the 3-D scan viewer's Surface
    echo        reconstruction will be unavailable. This usually means no
    echo        Open3D wheel exists for this Python version - try the latest
    echo        Python that open3d publishes for.
    echo.
)

echo [3/5] Fetching CAM vendor bundles (needs Node.js + git)...
python tools\fetch_vendor.py all
if errorlevel 1 (
    echo [WARN] A CAM bundle could not be prepared - continuing without it.
    echo        That tab will say so at runtime. See docs\cam-integration.md.
)

REM The build and the packaging live in tools\build_app.py, which is the same
REM on all three platforms - so Windows, macOS and Linux cannot drift apart in
REM anything but the environment setup above. It also zips the result.
echo [4/5] Building the application (this can take several minutes)...
python tools\build_app.py || goto :fail

echo [5/5] Done!
echo.
echo IMPORTANT: this is a one-FOLDER build, not a single .exe. QtWebEngine
echo (which powers the CAM tab) ships a helper process plus ICU/locale data that
echo Chromium loads by path, and that does not survive one-file packing.
echo Hand over the .zip that the step above wrote into dist\ - the .exe alone
echo will not run.
echo.
pause
exit /b 0

:fail
echo.
echo [ERROR] Build failed - see the messages above.
pause
exit /b 1
