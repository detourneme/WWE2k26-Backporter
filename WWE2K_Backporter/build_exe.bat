@echo off
REM build_exe.bat -- builds a standalone WWE2K_Backporter.exe from app.py
REM and its dependencies, using PyInstaller. Run this on Windows, from
REM inside this same folder (the one with app.py in it).
REM
REM Why build on Windows and not have Claude build it elsewhere: this app
REM is a plain Tkinter GUI with no third-party pip dependencies, so
REM PyInstaller "just works" -- but it needs to be run on the same OS
REM you'll run the result on, so a native Windows build here is the
REM reliable way to get a real, correctly-packaged Windows .exe.
REM
REM What this does NOT bundle: Blender itself. The app still calls out to
REM your existing Blender install (blender.exe) exactly as it does when
REM run from source -- nothing about that changes.

setlocal

echo === Checking for Python ===
python --version
if errorlevel 1 (
    echo.
    echo Python wasn't found on PATH. Install Python from https://python.org
    echo ^(make sure "Add python.exe to PATH" is checked during install^),
    echo then re-run this script.
    pause
    exit /b 1
)

echo.
echo === Installing/upgrading PyInstaller ===
python -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo.
    echo pip install pyinstaller failed -- see the error above.
    pause
    exit /b 1
)

echo.
echo === Building WWE2K_Backporter.exe ===
REM --onefile        : a single .exe, no companion folder to ship alongside it
REM --windowed       : no console window behind the Tkinter GUI
REM --icon ...       : the .exe's own icon (Explorer, taskbar) -- templates\
REM                    icon.ico, a square crop of the WWE 2K25 logo's "W"
REM                    mark. app.py also sets this as the window/titlebar
REM                    icon at runtime via the same bundled file.
REM --add-data ...   : blender_headless_convert.py and templates/ must be
REM                    bundled as real extracted files (not just compiled
REM                    code), since blender_headless_convert.py is handed to
REM                    Blender's own Python as a --python script argument --
REM                    see blender_runner.py's _APP_DIR comment for why.
python -m PyInstaller --onefile --windowed --name WWE2K_Backporter ^
    --icon "templates\icon.ico" ^
    --add-data "blender_headless_convert.py;." ^
    --add-data "templates;templates" ^
    app.py

if errorlevel 1 (
    echo.
    echo Build failed -- see the error above.
    pause
    exit /b 1
)

echo.
echo === Done ===
echo Your standalone exe is at: dist\WWE2K_Backporter.exe
echo You can copy that one file anywhere and run it directly -- it no
echo longer needs app.py, converter.py, or the other .py files next to it.
echo ^(It still needs Blender itself installed, same as before.^)
pause
