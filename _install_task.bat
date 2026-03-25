@echo off
schtasks /Create /TN "RemoteFileManagerAgent" /TR "\"%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe\" \"%USERPROFILE%\Desktop\remotefilemanager\app.py\" --no-elevate" /SC ONLOGON /RL HIGHEST /F
if %errorlevel%==0 (
    echo SUCCESS: Agent will auto-start on logon.
) else (
    echo FAILED: Run this as Administrator.
)
pause
