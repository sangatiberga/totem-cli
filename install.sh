#!/bin/sh
# Instala a versão mais recente da Totem CLI via GitHub Releases privados.
# Uso: gh api repos/sangatiberga/totem-cli/contents/install.sh -H "Accept: application/vnd.github.raw" | sh

set -eu

REPO="sangatiberga/totem-cli"
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

if ! command -v gh >/dev/null 2>&1; then
    echo "Erro: GitHub CLI (gh) não foi encontrado. Instale-o e autentique-se com 'gh auth login'." >&2
    exit 1
fi
if ! gh auth status --hostname github.com >/dev/null 2>&1; then
    echo "Erro: autentique-se no GitHub CLI. Execute 'gh auth login --hostname github.com' e tente novamente." >&2
    exit 1
fi

echo "Consultando a última release de ${REPO}..."
if ! gh release download --repo "$REPO" --pattern '*.whl' --pattern checksums.txt --dir "$TMP_DIR"; then
    echo "Erro: não foi possível baixar a última release de ${REPO}. Verifique o acesso e se já existe uma release publicada." >&2
    exit 1
fi

WHEEL_PATH="$(find "$TMP_DIR" -maxdepth 1 -type f -name '*.whl' -print -quit)"
if [ -z "$WHEEL_PATH" ] || [ ! -f "$TMP_DIR/checksums.txt" ]; then
    echo "Erro: a release não contém a wheel e o arquivo checksums.txt esperados." >&2
    exit 1
fi
WHEEL_NAME="$(basename "$WHEEL_PATH")"

EXPECTED_SHA="$(awk -v name="$WHEEL_NAME" '$2 == name { print $1 }' "$TMP_DIR/checksums.txt")"
if [ -z "$EXPECTED_SHA" ]; then
    echo "Erro: checksum de ${WHEEL_NAME} não encontrado." >&2
    exit 1
fi
if command -v sha256sum >/dev/null 2>&1; then
    ACTUAL_SHA="$(sha256sum "$WHEEL_PATH" | awk '{ print $1 }')"
elif command -v shasum >/dev/null 2>&1; then
    ACTUAL_SHA="$(shasum -a 256 "$WHEEL_PATH" | awk '{ print $1 }')"
else
    echo "Erro: sha256sum ou shasum é necessário para verificar o download." >&2
    exit 1
fi
if [ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]; then
    echo "Erro: o checksum SHA-256 do pacote não confere." >&2
    exit 1
fi

pipx install --force "$WHEEL_PATH"
pipx ensurepath >/dev/null
echo "Totem CLI instalada. Abra um novo terminal e execute: totem"
