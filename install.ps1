# Instala a versão mais recente da Totem CLI via GitHub Releases privados.
# Uso: gh api repos/sangatiberga/totem-cli/contents/install.ps1 -H "Accept: application/vnd.github.raw" | iex

$ErrorActionPreference = 'Stop'
$Repository = 'sangatiberga/totem-cli'

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'Instale Python 3.11 ou superior e habilite o Python Launcher (py) antes de continuar.'
}

$PythonVersion = & py -3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
if ($LASTEXITCODE -ne 0 -or [version]$PythonVersion -lt [version]'3.11') {
    throw "Totem CLI requer Python 3.11 ou superior (encontrado: $PythonVersion)."
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) não encontrado. Instale-o e autentique-se com 'gh auth login --hostname github.com'."
}
& gh auth status --hostname github.com *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Autentique-se no GitHub CLI com 'gh auth login --hostname github.com' e tente novamente."
}

& py -m pipx --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Instalando pipx para o usuário atual...'
    & py -m pip install --user pipx
    if ($LASTEXITCODE -ne 0) { throw 'Não foi possível instalar pipx.' }
}

$TempDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $TempDirectory | Out-Null
try {
    Write-Host "Baixando a última release de $Repository..."
    & gh release download --repo $Repository --pattern '*.whl' --pattern checksums.txt --dir $TempDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "Não foi possível baixar a última release. Verifique o acesso e se já existe uma release publicada."
    }

    $WheelFile = Get-ChildItem -Path $TempDirectory -Filter '*.whl' | Select-Object -First 1
    if (-not $WheelFile) { throw 'A release não contém uma wheel.' }
    $WheelPath = $WheelFile.FullName
    $WheelName = $WheelFile.Name
    $ChecksumsPath = Join-Path $TempDirectory 'checksums.txt'
    if (-not (Test-Path $ChecksumsPath)) { throw 'A release não contém checksums.txt.' }

    $ChecksumLine = Get-Content $ChecksumsPath | Where-Object { $_ -match ("\s+" + [regex]::Escape($WheelName) + '$') } | Select-Object -First 1
    if (-not $ChecksumLine) { throw "Checksum de $WheelName não encontrado." }
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
