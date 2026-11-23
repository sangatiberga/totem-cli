# Instala a Totem CLI (binário compilado, sem Python/pipx) a partir da
# última release privada do GitHub.
#
# 1. Crie um token em https://github.com/settings/tokens com acesso de
#    leitura ao conteúdo do repositório sangatiberga/totem-cli
#    (fine-grained: Contents Read-only; ou clássico com escopo repo).
# 2. Instalação em um comando, passando o token no cabeçalho
#    (o repositório é privado):
#      $env:GITHUB_TOKEN='SEU_TOKEN'; irm https://raw.githubusercontent.com/sangatiberga/totem-cli/main/install.ps1 -Headers @{Authorization="Bearer $env:GITHUB_TOKEN"} | iex
#    Ou baixe este arquivo pelo navegador (logado no GitHub) e execute:
#      $env:GITHUB_TOKEN = 'SEU_TOKEN'
#      powershell -ExecutionPolicy Bypass -File install.ps1
[CmdletBinding()]
param(
    [string]$Token = $env:GITHUB_TOKEN,
    [string]$Repository = 'sangatiberga/totem-cli'
)
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($Token)) {
    throw 'Defina $env:GITHUB_TOKEN com um token de leitura do repositório sangatiberga/totem-cli antes de continuar.'
}

if (-not [System.Environment]::Is64BitOperatingSystem) {
    throw 'A Totem CLI distribui binário apenas para Windows 64-bit.'
}
$AssetName = 'totem-windows-amd64.exe'

$ApiHeaders = @{
    Authorization          = "Bearer $Token"
    Accept                 = 'application/vnd.github+json'
    'X-GitHub-Api-Version' = '2022-11-28'
}
try {
    $Release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repository/releases/latest" -Headers $ApiHeaders
}
catch {
    throw "Não foi possível consultar a última release de $Repository. Verifique o token e o acesso ao repositório. Detalhe: $($_.Exception.Message)"
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
    $DownloadHeaders = @{
        Authorization = "Bearer $Token"
        Accept        = 'application/octet-stream'
    }
    $DownloadPath = Join-Path $TempDirectory $AssetName
    $ChecksumsPath = Join-Path $TempDirectory 'checksums.txt'
    Write-Host "Baixando $AssetName da última release..."
    Invoke-WebRequest -Uri $Binary.browser_download_url -Headers $DownloadHeaders -OutFile $DownloadPath
    Invoke-WebRequest -Uri $Checksums.browser_download_url -Headers $DownloadHeaders -OutFile $ChecksumsPath

    $ChecksumLine = Get-Content $ChecksumsPath | Where-Object { $_ -match ("\s+" + [regex]::Escape($AssetName) + '$') } | Select-Object -First 1
    if (-not $ChecksumLine) { throw "Checksum de $AssetName não encontrado." }
    $ExpectedHash = ($ChecksumLine -split '\s+')[0].ToLowerInvariant()
    $ActualHash = (Get-FileHash -Algorithm SHA256 -Path $DownloadPath).Hash.ToLowerInvariant()
    if ($ExpectedHash -ne $ActualHash) { throw 'O checksum SHA-256 do binário não confere.' }

    $Destination = Join-Path $InstallDirectory 'totem.exe'
    Copy-Item -Path $DownloadPath -Destination $Destination -Force

    $UserPath = [System.Environment]::GetEnvironmentVariable('PATH', 'User')
    if ($UserPath -notlike "*$InstallDirectory*") {
        [System.Environment]::SetEnvironmentVariable('PATH', "$UserPath;$InstallDirectory", 'User')
        Write-Host 'Diretório adicionado ao PATH do usuário. Feche e reabra o PowerShell.'
    }
    Write-Host "Totem CLI instalada. Execute: totem"
}
finally {
    Remove-Item -Recurse -Force $TempDirectory -ErrorAction SilentlyContinue
}
