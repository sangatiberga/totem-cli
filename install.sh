#!/bin/sh
# Instala a Totem CLI (binário compilado, sem Python/pipx) a partir da
# última release privada do GitHub.
#
# 1. Crie um token em https://github.com/settings/tokens com acesso de
#    leitura ao conteúdo do repositório sangatiberga/totem-cli
#    (fine-grained: Contents Read-only; ou clássico com escopo repo).
# 2. Instalação em um comando:
#      export GITHUB_TOKEN='SEU_TOKEN'; curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" https://raw.githubusercontent.com/sangatiberga/totem-cli/main/install.sh | sh
#    Ou baixe este arquivo pelo navegador (logado no GitHub) e execute:
#      GITHUB_TOKEN='SEU_TOKEN' sh install.sh

set -eu

REPO="${TOTEM_REPO:-sangatiberga/totem-cli}"

if [ -z "${GITHUB_TOKEN:-}" ]; then
    echo "Erro: defina GITHUB_TOKEN com um token de leitura do repositório ${REPO}." >&2
    echo "Exemplo: GITHUB_TOKEN='SEU_TOKEN' sh install.sh" >&2
    exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
    echo "Erro: curl não foi encontrado. Instale-o e tente novamente." >&2
    exit 1
fi

if ! command -v sha256sum >/dev/null 2>&1; then
    echo "Erro: sha256sum não foi encontrado. Instale-o e tente novamente." >&2
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
API_JSON="$(curl -fsSL \
    -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    -H 'Accept: application/vnd.github+json' \
    -H 'X-GitHub-Api-Version: 2022-11-28' \
    "https://api.github.com/repos/${REPO}/releases/latest")" || {
    echo "Erro: não foi possível consultar a última release. Verifique o token e o acesso ao repositório." >&2
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
print("BIN_NAME=" + shlex.quote(binary["name"]))
print("CHECKSUMS_URL=" + shlex.quote(checksums["browser_download_url"]))
')"

echo "Baixando ${BIN_NAME} da última release..."
curl -fsSL \
    -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    -H 'Accept: application/octet-stream' \
    -o "${TMP_DIR}/${BIN_NAME}" "$BIN_URL"
curl -fsSL \
    -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    -H 'Accept: application/octet-stream' \
    -o "${TMP_DIR}/checksums.txt" "$CHECKSUMS_URL"

EXPECTED_SHA="$(awk -v name="$BIN_NAME" '$2 == name { print $1 }' "${TMP_DIR}/checksums.txt")"
if [ -z "$EXPECTED_SHA" ]; then
    echo "Erro: checksum de ${BIN_NAME} não encontrado." >&2
    exit 1
fi
ACTUAL_SHA="$(sha256sum "${TMP_DIR}/${BIN_NAME}" | awk '{ print $1 }')"
if [ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]; then
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
echo "Totem CLI instalada em ${DEST}. Execute: totem"
