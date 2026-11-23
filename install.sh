#!/bin/sh
# Instala a Totem CLI (binário compilado) a partir da última release do GitHub.
#
# Instalação em um comando:
#   curl -fsSL https://raw.githubusercontent.com/sangatiberga/totem-cli/main/install.sh | sh
# Ou baixe este arquivo e execute: sh install.sh

set -eu

REPO="${TOTEM_REPO:-sangatiberga/totem-cli}"

if ! command -v curl >/dev/null 2>&1; then
    echo "Erro: curl não foi encontrado. Instale-o e tente novamente." >&2
    exit 1
fi

if ! command -v sha256sum >/dev/null 2>&1; then
    echo "Erro: sha256sum não foi encontrado. Instale-o e tente novamente." >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "Erro: python3 é necessário para ler a resposta da API do GitHub." >&2
    exit 1
fi

OS="$(uname -s)"
ARCH="$(uname -m)"
if [ "$OS" != "Linux" ]; then
    echo "Erro: este instalador atende Linux (para Windows, use install.ps1)." >&2
    exit 1
fi
case "$ARCH" in
    x86_64|amd64) ASSET="totem-linux-amd64" ;;
    *) echo "Erro: arquitetura ${ARCH} sem binário publicado." >&2; exit 1 ;;
esac

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT INT TERM

echo "Consultando a última release de ${REPO}..."
API_JSON="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest")" || {
    echo "Erro: não foi possível consultar a última release." >&2
    exit 1
}

eval "$(printf '%s' "$API_JSON" | TOTEM_ASSET="$ASSET" python3 -c '
import json, os, shlex, sys
try:
    release = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit("resposta inesperada da API do GitHub")
want = os.environ["TOTEM_ASSET"]
binary = next((a for a in release.get("assets", []) if a.get("name") == want), None)
checksums = next((a for a in release.get("assets", []) if a.get("name") == "checksums.txt"), None)
if not binary or not checksums:
    sys.exit("a release não contém o binário e o checksums.txt esperados")
print("BIN_URL=" + shlex.quote(binary["browser_download_url"]))
print("BIN_DIGEST=" + shlex.quote(str(binary.get("digest", ""))))
print("BIN_NAME=" + shlex.quote(binary["name"]))
print("CHECKSUMS_URL=" + shlex.quote(checksums["browser_download_url"]))
print("RELEASE_TAG=" + shlex.quote(release.get("tag_name", "(sem tag)")))
')"

echo "Baixando ${BIN_NAME} da última release..."
curl -fsSL -o "${TMP_DIR}/${BIN_NAME}" "$BIN_URL"
curl -fsSL -o "${TMP_DIR}/checksums.txt" "$CHECKSUMS_URL"

if [ -n "${BIN_DIGEST:-}" ]; then
    EXPECTED_SHAS="${BIN_DIGEST#sha256:}"
else
    EXPECTED_SHAS="$(awk -v name="$BIN_NAME" '$2 == name { print $1 }' "${TMP_DIR}/checksums.txt")"
fi
if [ -z "$EXPECTED_SHAS" ]; then
    echo "Erro: checksum de ${BIN_NAME} não encontrado." >&2
    exit 1
fi
ACTUAL_SHA="$(sha256sum "${TMP_DIR}/${BIN_NAME}" | awk '{ print $1 }')"
if ! printf '%s\n' "$EXPECTED_SHAS" | grep -qx "$ACTUAL_SHA"; then
    echo "Erro: o checksum SHA-256 do binário não confere." >&2
    exit 1
fi

chmod +x "${TMP_DIR}/${BIN_NAME}"
if [ -w /usr/local/bin ]; then
    DEST=/usr/local/bin/totem
    cp "${TMP_DIR}/${BIN_NAME}" "$DEST"
elif command -v sudo >/dev/null 2>&1; then
    DEST=/usr/local/bin/totem
    sudo cp "${TMP_DIR}/${BIN_NAME}" "$DEST"
else
    DEST="$HOME/.local/bin/totem"
    mkdir -p "$HOME/.local/bin"
    cp "${TMP_DIR}/${BIN_NAME}" "$DEST"
    echo "Instalado em ${DEST}. Garanta ~/.local/bin no PATH e reabra o terminal." >&2
fi
echo "Totem CLI instalada com sucesso."
echo "Release instalada: ${RELEASE_TAG:-}"
echo "Executavel: ${DEST}"
echo "Execute: totem"
