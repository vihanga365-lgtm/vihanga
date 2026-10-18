$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'Install 64-bit Python 3.10 or newer and add Python to PATH, then run setup.bat again.'
}
python -c "import struct, sys; sys.exit(0 if sys.version_info >= (3, 10) and struct.calcsize('P') == 8 else 1)"
if ($LASTEXITCODE -ne 0) { throw '64-bit Python 3.10 or newer is required.' }
if (Get-Process CrowTradingBot -ErrorAction SilentlyContinue) {
    throw 'Close CrowTradingBot before rebuilding the application.'
}
python -m pip install -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
python -m PyInstaller --noconfirm --clean --onefile --windowed --distpath . --workpath build --specpath build --name CrowTradingBot --collect-all ccxt --collect-all MetaTrader5 --exclude-module torch --exclude-module matplotlib --exclude-module scipy desktop.py
if ($LASTEXITCODE -ne 0) { throw 'Executable build failed' }
Write-Host 'Built CrowTradingBot.exe'
