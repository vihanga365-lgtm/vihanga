@echo off
setlocal
cd /d "%~dp0"

echo Building Crow Trading Bot...
echo Python 3.10 or newer and an internet connection are required.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
if errorlevel 1 (
    echo.
    echo Build failed. Check the error above.
    echo Make sure 64-bit Python 3.10 or newer is installed and added to PATH.
    pause
    exit /b 1
)

echo.
echo Ready: "%~dp0CrowTradingBot.exe"
echo Double-click CrowTradingBot.exe to open the application.
pause
exit /b 0
