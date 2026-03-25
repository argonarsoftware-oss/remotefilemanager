@echo off
echo ========================================
echo  Remote File Manager Agent - Build
echo ========================================
echo.

echo Installing dependencies...
pip install flask pyinstaller requests psutil

echo.
echo Building executable (hidden window, admin rights)...
pyinstaller --onefile --noconsole --uac-admin ^
    --name RemoteFileManager ^
    --icon=NONE ^
    app.py

echo.
echo ========================================
echo  Build complete!
echo  Output: dist\RemoteFileManager.exe
echo ========================================
echo.
echo The .exe will:
echo   - Request admin (UAC) on launch
echo   - Hide its window (runs invisibly)
echo   - Auto-register in Task Scheduler
echo   - Start automatically at logon
echo.
echo To uninstall: RemoteFileManager.exe --uninstall
echo To run visible: RemoteFileManager.exe --visible
echo.
pause
