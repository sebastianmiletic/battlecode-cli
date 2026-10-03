#Requires -Version 5.1
# User-only installation; Windows Credential Manager holds Battlecode secrets.
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Use install.sh on macOS or Linux.'
}
$Package = 'https://github.com/sebastianmiletic/battlecode-cli/archive/refs/tags/v0.4.1.tar.gz'
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot 'src\battlecode_cli\__init__.py'))) {
    $Package = $PSScriptRoot
}

$Command = Get-Command uv -ErrorAction SilentlyContinue
if ($Command) {
    $Uv = $Command.Source
} else {
    Write-Host 'Installing uv from https://astral.sh/uv ...'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $Bootstrap = Join-Path ([IO.Path]::GetTempPath()) (([guid]::NewGuid().ToString()) + '.ps1')
    try {
        Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/install.ps1' -OutFile $Bootstrap
        & powershell -NoProfile -ExecutionPolicy Bypass -File $Bootstrap
        if ($LASTEXITCODE -ne 0) { throw 'uv installation failed.' }
    } finally {
        Remove-Item -LiteralPath $Bootstrap -Force -ErrorAction SilentlyContinue
    }
    $InstallDir = if ($env:UV_INSTALL_DIR) { $env:UV_INSTALL_DIR } else { Join-Path $HOME '.local\bin' }
    $Uv = Join-Path $InstallDir 'uv.exe'
    if (-not (Test-Path -LiteralPath $Uv)) { throw 'uv was not found. Open a new terminal and rerun install.ps1.' }
}

& $Uv tool install --force --python 3.13 --from $Package battlecode-cli
if ($LASTEXITCODE -ne 0) { throw 'battlecode-cli installation failed.' }
$Bin = (& $Uv tool dir --bin).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Could not locate the installed command.' }
$UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$Parts = @($UserPath -split ';' | Where-Object { $_ })
if (-not ($Parts | Where-Object { $_.TrimEnd('\') -eq $Bin.TrimEnd('\') })) {
    [Environment]::SetEnvironmentVariable('Path', (($Parts + $Bin) -join ';'), 'User')
}
if (-not (($env:Path -split ';') -contains $Bin)) {
    $env:Path = $Bin + ';' + $env:Path
}
& (Join-Path $Bin 'battlecode-cli.exe') --version
if ($LASTEXITCODE -ne 0) { throw 'Installed command did not start.' }
Write-Host ''
Write-Host 'Installed: battlecode-cli (battlecode is also available).'
Write-Host 'Open a new terminal, then run: battlecode-cli'
Write-Host ('To launch immediately: & "' + (Join-Path $Bin 'battlecode-cli.exe') + '"')
