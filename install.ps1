# Instala a Totem CLI (binário compilado) a partir da última release do GitHub.
#
# Instalação em um comando:
#   irm https://raw.githubusercontent.com/sangatiberga/totem-cli/main/install.ps1 | iex
# Ou baixe este arquivo e execute:
#   powershell -ExecutionPolicy Bypass -File install.ps1
[CmdletBinding()]
param(
    [string]$Repository = 'sangatiberga/totem-cli'
)
$ErrorActionPreference = 'Stop'

# ─── Cabeçalho ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  TOTEM CLI · Instalador oficial" -ForegroundColor Cyan -NoNewline
Write-Host " · https://github.com/$Repository" -ForegroundColor DarkGray
Write-Host "  ────────────────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host ""

function Step($n, $total, $msg) {
    Write-Host "`n  [$n/$total] $msg" -ForegroundColor White -NoNewline
}
function Ok($msg)   { Write-Host "  `✓ $msg" -ForegroundColor Green }
function Info($msg)  { Write-Host "  →  $msg" -ForegroundColor DarkGray }

if (-not [System.Environment]::Is64BitOperatingSystem) {
    throw 'A Totem CLI distribui binário apenas para Windows 64-bit.'
}
$AssetName = 'totem-windows-amd64.exe'

try {
    Step 1 4 "Consultando a última release de $Repository"
    $Release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repository/releases/latest"
    Ok "conectado à API do GitHub"
}
catch {
    throw "Não foi possível consultar a última release de $Repository. Detalhe: $($_.Exception.Message)"
}

$Binary = @($Release.assets | Where-Object { $_.name -eq $AssetName }) | Select-Object -First 1
$Checksums = @($Release.assets | Where-Object { $_.name -eq 'checksums.txt' }) | Select-Object -First 1
if (-not $Binary) { throw "A última release não contém $AssetName." }
if (-not $Checksums) { throw 'A última release não contém checksums.txt.' }

$InstallDirectory = Join-Path $env:LOCALAPPDATA 'TotemCLI'
New-Item -ItemType Directory -Path $InstallDirectory -Force | Out-Null
$TempDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $TempDirectory | Out-Null
try {
    $DownloadPath = Join-Path $TempDirectory $AssetName
    $ChecksumsPath = Join-Path $TempDirectory 'checksums.txt'
    Step 2 4 "Baixando $AssetName"
    Info "release $($Release.tag_name)"
    Invoke-WebRequest -Uri $Binary.browser_download_url -OutFile $DownloadPath
    Invoke-WebRequest -Uri $Checksums.browser_download_url -OutFile $ChecksumsPath
    Ok "download concluído"
    Step 3 4 "Verificando checksum SHA-256"

    if ($Binary.digest -and $Binary.digest -match '^sha256:([0-9a-fA-F]{64})$') {
        # Preferred source: digest served by the API itself (no CDN staleness).
        $ExpectedHashes = @($Matches[1].ToLowerInvariant())
    }
    else {
        $ExpectedHashes = @(Get-Content $ChecksumsPath |
            Where-Object { $_ -match ("\s+" + [regex]::Escape($AssetName) + '$') } |
            ForEach-Object { ($_ -split '\s+')[0].ToLowerInvariant() })
    }
    if (-not $ExpectedHashes) { throw "Checksum de $AssetName não encontrado." }
    $ActualHash = (Get-FileHash -Algorithm SHA256 -Path $DownloadPath).Hash.ToLowerInvariant()
    if ($ActualHash -notin $ExpectedHashes) { throw 'O checksum SHA-256 do binário não confere.' }
    Ok "checksum OK ($ActualHash)"

    Step 4 4 "Instalando"

    $Destination = Join-Path $InstallDirectory 'totem.exe'
    Copy-Item -Path $DownloadPath -Destination $Destination -Force

    $UserPath = [System.Environment]::GetEnvironmentVariable('PATH', 'User')
    if ($UserPath -notlike "*$InstallDirectory*") {
        [System.Environment]::SetEnvironmentVariable('PATH', "$UserPath;$InstallDirectory", 'User')
        Ok 'Diretório adicionado ao PATH do usuário. Feche e reabra o PowerShell.'
    }
    else {
        Ok "já estava no PATH"
    }
    Ok "instalado em $Destination"

    $InstalledVersion = $Release.tag_name
    if (-not $InstalledVersion) { $InstalledVersion = '(release sem tag)' }
    Write-Host ""
    Write-Host "  ✓ Totem CLI instalada com sucesso!" -ForegroundColor Green -NoNewline
    Write-Host ""
    Write-Host "  ── Resumo ──────────────────────────────────────────────" -ForegroundColor DarkGray
    Write-Host "  Release instalada: $InstalledVersion" -NoNewline
    Write-Host ""
    Write-Host "  Executável:        $Destination" -NoNewline
    Write-Host ""
    Write-Host "  Execute agora:     totem" -ForegroundColor Green -NoNewline
    Write-Host ""
    Write-Host ""
}
finally {
    Remove-Item -Recurse -Force $TempDirectory -ErrorAction SilentlyContinue
}
