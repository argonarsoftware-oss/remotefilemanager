@echo off
schtasks /Create /TN "RemoteFileManagerAgent" /TR "\"%~dp0dist\RemoteFileManager.exe\"" /SC ONLOGON /RL HIGHEST /F
if %errorlevel%==0 (
    echo Task Scheduler: Installed successfully.
    echo Agent will auto-start at logon with admin rights.
) else (
    echo Task Scheduler: Failed. Run this as Administrator.
)
pause
