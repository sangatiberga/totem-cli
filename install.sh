#!/bin/sh
# Instala a versão mais recente da Totem CLI via GitHub Releases.
# Uso: curl -fsSL https://raw.githubusercontent.com/sangatiberga/totem-cli/main/install.sh | sh

set -eu

REPO="sangatiberga/totem-cli"
API_URL="https://api.github.com/repos/${REPO}/releases/latest"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT INT TERM

if ! command -v python3 >/dev/null 2>&1; then
    echo "Erro: instale Python 3.11 ou superior antes de instalar a Totem CLI." >&2
    exit 1
fi

PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    echo "Erro: Totem CLI requer Python 3.11 ou superior (encontrado: ${PYTHON_VERSION})." >&2
    exit 1
fi

if ! command -v pipx >/dev/null 2>&1; then
    echo "Erro: pipx não foi encontrado. Instale-o com o gerenciador de pacotes da sua distribuição e tente novamente." >&2
    echo "Ubuntu/Debian: sudo apt update && sudo apt install pipx && pipx ensurepath" >&2
    exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
    echo "Erro: curl não foi encontrado." >&2
    exit 1
fi

echo "Consultando a última release de ${REPO}..."
curl -fsSL "$API_URL" -o "$TMP_DIR/release.json"
python3 - "$TMP_DIR/release.json" "$TMP_DIR/assets.txt" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as release_file:
    release = json.load(release_file)
assets = release.get("assets", [])
wheel = next((asset for asset in assets if asset["name"].endswith(".whl")), None)
checksums = next((asset for asset in assets if asset["name"] == "checksums.txt"), None)
if wheel is None or checksums is None:
    raise SystemExit("A release não contém a wheel e o arquivo checksums.txt esperados.")
with open(sys.argv[2], "w", encoding="utf-8") as output:
    output.write(wheel["name"] + "\n")
    output.write(wheel["browser_download_url"] + "\n")
    output.write(checksums["browser_download_url"] + "\n")
PY

WHEEL_NAME="$(sed -n '1p' "$TMP_DIR/assets.txt")"
WHEEL_URL="$(sed -n '2p' "$TMP_DIR/assets.txt")"
CHECKSUM_URL="$(sed -n '3p' "$TMP_DIR/assets.txt")"
echo "Baixando ${WHEEL_NAME}..."
curl -fsSL "$WHEEL_URL" -o "$TMP_DIR/$WHEEL_NAME"
curl -fsSL "$CHECKSUM_URL" -o "$TMP_DIR/checksums.txt"

EXPECTED_SHA="$(awk -v name="$WHEEL_NAME" '$2 == name { print $1 }' "$TMP_DIR/checksums.txt")"
if [ -z "$EXPECTED_SHA" ]; then
    echo "Erro: checksum de ${WHEEL_NAME} não encontrado." >&2
    exit 1
fi
if command -v sha256sum >/dev/null 2>&1; then
    ACTUAL_SHA="$(sha256sum "$TMP_DIR/$WHEEL_NAME" | awk '{ print $1 }')"
elif command -v shasum >/dev/null 2>&1; then
    ACTUAL_SHA="$(shasum -a 256 "$TMP_DIR/$WHEEL_NAME" | awk '{ print $1 }')"
else
    echo "Erro: sha256sum ou shasum é necessário para verificar o download." >&2
    exit 1
fi
if [ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]; then
    echo "Erro: o checksum SHA-256 do pacote não confere." >&2
    exit 1
fi

pipx install --force "$TMP_DIR/$WHEEL_NAME"
pipx ensurepath >/dev/null
echo "Totem CLI instalada. Abra um novo terminal e execute: totem"
