[CmdletBinding()]
param(
    [ValidatePattern('^\d+\.\d+\.\d+(\.\d+)?$')]
    [string]$Version = '1.0.0',
    [string]$ExecutablePath,
    [string]$CompilerPath
)

$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    if (-not $CompilerPath) {
        $compilerCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($compilerCommand) {
            $CompilerPath = $compilerCommand.Source
        } else {
            $candidates = @(
                "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
                "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
            )
            $CompilerPath = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
        }
    }
    if (-not $CompilerPath -or -not (Test-Path -LiteralPath $CompilerPath -PathType Leaf)) {
        throw 'Install Inno Setup 6 from https://jrsoftware.org/isdl.php, then rerun setup.ps1. Alternatively, pass -CompilerPath with the full path to ISCC.exe.'
    }
    $CompilerPath = (Resolve-Path -LiteralPath $CompilerPath).Path

    if ($ExecutablePath) {
        $ExecutablePath = (Resolve-Path -LiteralPath $ExecutablePath).Path
        if (-not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf) -or
            [IO.Path]::GetExtension($ExecutablePath) -ne '.exe') {
            throw 'ExecutablePath must point to a built Windows .exe file.'
        }
    } else {
        # Never terminate a running trading bot to replace its executable.
        $runningBot = Get-Process CrowTradingBot -ErrorAction SilentlyContinue
        if ($runningBot) {
            throw 'Close CrowTradingBot before rebuilding, or use -ExecutablePath to package an existing build without interrupting the running bot.'
        }
        & (Join-Path $PSScriptRoot 'build.ps1')
        $ExecutablePath = (Resolve-Path -LiteralPath 'CrowTradingBot.exe').Path
    }

    & $CompilerPath "/DAppVersion=$Version" "/DAppExe=$ExecutablePath" (Join-Path $PSScriptRoot 'installer\setup.iss')
    if ($LASTEXITCODE -ne 0) { throw "Installer build failed (exit code $LASTEXITCODE)." }
    $setupPath = Join-Path $PSScriptRoot "CrowTradingBot-Setup-$Version.exe"
    if (-not (Test-Path -LiteralPath $setupPath -PathType Leaf)) {
        throw "Compiler did not create the expected installer: $setupPath"
    }
    Write-Host "Built $setupPath"
} finally {
    Pop-Location
}
