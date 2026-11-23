# Instala a versão mais recente da Totem CLI via GitHub Releases.
# Uso no PowerShell: irm https://raw.githubusercontent.com/sangatiberga/totem-cli/main/install.ps1 | iex

$ErrorActionPreference = 'Stop'
$Repository = 'sangatiberga/totem-cli'
$ReleaseApi = "https://api.github.com/repos/$Repository/releases/latest"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'Instale Python 3.11 ou superior e habilite o Python Launcher (py) antes de continuar.'
}

$PythonVersion = & py -3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
if ($LASTEXITCODE -ne 0 -or [version]$PythonVersion -lt [version]'3.11') {
    throw "Totem CLI requer Python 3.11 ou superior (encontrado: $PythonVersion)."
}

& py -m pipx --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Instalando pipx para o usuário atual...'
    & py -m pip install --user pipx
    if ($LASTEXITCODE -ne 0) { throw 'Não foi possível instalar pipx.' }
}

Write-Host "Consultando a última release de $Repository..."
$Release = Invoke-RestMethod -Uri $ReleaseApi
$Wheel = $Release.assets | Where-Object { $_.name.EndsWith('.whl') } | Select-Object -First 1
$Checksums = $Release.assets | Where-Object { $_.name -eq 'checksums.txt' } | Select-Object -First 1
if (-not $Wheel -or -not $Checksums) {
    throw 'A release não contém a wheel e o arquivo checksums.txt esperados.'
}

$TempDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $TempDirectory | Out-Null
try {
    $WheelPath = Join-Path $TempDirectory $Wheel.name
    $ChecksumsPath = Join-Path $TempDirectory 'checksums.txt'
    Write-Host "Baixando $($Wheel.name)..."
    Invoke-WebRequest -Uri $Wheel.browser_download_url -OutFile $WheelPath
    Invoke-WebRequest -Uri $Checksums.browser_download_url -OutFile $ChecksumsPath

    $ChecksumLine = Get-Content $ChecksumsPath | Where-Object { $_ -match ("\s+" + [regex]::Escape($Wheel.name) + '$') } | Select-Object -First 1
    if (-not $ChecksumLine) { throw "Checksum de $($Wheel.name) não encontrado." }
    $ExpectedHash = ($ChecksumLine -split '\s+')[0].ToLowerInvariant()
    $ActualHash = (Get-FileHash -Algorithm SHA256 -Path $WheelPath).Hash.ToLowerInvariant()
    if ($ExpectedHash -ne $ActualHash) { throw 'O checksum SHA-256 do pacote não confere.' }

    & py -m pipx install --force $WheelPath
    if ($LASTEXITCODE -ne 0) { throw 'A instalação da Totem CLI falhou.' }
    & py -m pipx ensurepath | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Não foi possível adicionar o diretório de comandos do pipx ao PATH.' }
    Write-Host 'Totem CLI instalada. Abra um novo PowerShell e execute: totem'
}
finally {
    Remove-Item -Recurse -Force $TempDirectory -ErrorAction SilentlyContinue
}
