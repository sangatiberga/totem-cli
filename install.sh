#!/bin/sh
# Instala a Totem CLI (binário compilado) a partir da última release do GitHub.
#
# Instalação em um comando:
#   curl -fsSL https://raw.githubusercontent.com/sangatiberga/totem-cli/main/install.sh | sh
# Ou baixe este arquivo e execute: sh install.sh

set -eu

REPO="${TOTEM_REPO:-sangatiberga/totem-cli}"

# ─── Cores só em terminal interativo ────────────────────────────────────────
if [ -t 1 ] && [ "${NO_COLOR:-}" != "1" ]; then
    BOLD='\033[1m'
    DIM='\033[2m'
    CYAN='\033[0;36m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    RED='\033[0;31m'
    BLUE='\033[0;34m'
    RESET='\033[0m'
else
    BOLD=''; DIM=''; CYAN=''; GREEN=''; YELLOW=''; RED=''; BLUE=''; RESET=''
fi

step() { printf "\n${BOLD}[%s/%s]${RESET} %s\n" "$1" "$2" "$3"; }
ok()   { printf "  ${GREEN}✓${RESET} %s\n" "$1"; }
info() { printf "  ${DIM}→${RESET} %s\n" "$1"; }
warn() { printf "  ${YELLOW}▲${RESET} %s\n" "$1"; }
fail() { printf "\n${RED}${BOLD}✗ Erro:${RESET} %s\n" "$1" >&2; exit 1; }

banner() {
    printf "\n"
    printf "  ${BOLD}${CYAN}TOTEM CLI${RESET} ${DIM}· Instalador oficial${RESET}\n"
    printf "  ${DIM}https://github.com/%s${RESET}\n" "$REPO"
    printf "  ${DIM}────────────────────────────────────────────────────────${RESET}\n"
}

success_box() {
    ver="$1"
    dest="$2"
    printf "\n"
    printf "  ${GREEN}${BOLD}✓ Totem CLI instalada com sucesso!${RESET}\n"
    printf "\n"
    printf "  ${CYAN}${BOLD}┌─ Resumo ─────────────────────────────────────────────┐${RESET}\n"
    printf "  ${CYAN}│${RESET}  Release instalada: ${BOLD}%s${RESET}\n" "$ver"
    printf "  ${CYAN}│${RESET}  Executável:        ${BOLD}%s${RESET}\n" "$dest"
    printf "  ${CYAN}│${RESET}  Execute agora:     ${BOLD}totem${RESET}\n"
    printf "  ${CYAN}${BOLD}└────────────────────────────────────────────────────────┘${RESET}\n"
    printf "\n"
}

# ─── Pré-requisitos ──────────────────────────────────────────────────────────
banner

step 0 5 "Pré-requisitos"
if ! command -v curl >/dev/null 2>&1; then
    fail "curl não foi encontrado. Instale-o e tente novamente."
fi
ok "curl encontrado"

if ! command -v sha256sum >/dev/null 2>&1; then
    fail "sha256sum não foi encontrado. Instale-o e tente novamente."
fi
ok "sha256sum encontrado"

if ! command -v python3 >/dev/null 2>&1; then
    fail "python3 é necessário para ler a resposta da API do GitHub."
fi
ok "python3 encontrado"

OS="$(uname -s)"
ARCH="$(uname -m)"
if [ "$OS" != "Linux" ]; then
    fail "este instalador atende Linux (para Windows, use install.ps1)."
fi
case "$ARCH" in
    x86_64|amd64) ASSET="totem-linux-amd64" ;;
    *) fail "arquitetura ${ARCH} sem binário publicado." ;;
esac
ok "sistema: ${OS} / ${ARCH} → ${ASSET}"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT INT TERM

step 1 4 "Consultando a última release de ${REPO}"
API_JSON="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest")" || {
    fail "não foi possível consultar a última release."
}
info "conectado à API do GitHub"

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
ok "release ${RELEASE_TAG} localizada"

step 2 4 "Baixando ${BIN_NAME}"
info "binário: ${ASSET}"
curl --progress-bar -fsSL -o "${TMP_DIR}/${BIN_NAME}" "$BIN_URL"
ok "binário baixado ($(du -h "${TMP_DIR}/${BIN_NAME}" | awk '{ print $1 }'))"
curl -fsSL -o "${TMP_DIR}/checksums.txt" "$CHECKSUMS_URL"
info "checksums.txt obtidos"

step 3 4 "Verificando checksum SHA-256"
if [ -n "${BIN_DIGEST:-}" ]; then
    EXPECTED_SHAS="${BIN_DIGEST#sha256:}"
else
    EXPECTED_SHAS="$(awk -v name="$BIN_NAME" '$2 == name { print $1 }' "${TMP_DIR}/checksums.txt")"
fi
if [ -z "$EXPECTED_SHAS" ]; then
    fail "checksum de ${BIN_NAME} não encontrado."
fi
ACTUAL_SHA="$(sha256sum "${TMP_DIR}/${BIN_NAME}" | awk '{ print $1 }')"
if ! printf '%s\n' "$EXPECTED_SHAS" | grep -qx "$ACTUAL_SHA"; then
    fail "o checksum SHA-256 do binário não confere."
fi
ok "checksum OK (${ACTUAL_SHA})"

step 4 4 "Instalando"

chmod +x "${TMP_DIR}/${BIN_NAME}"
if [ -w /usr/local/bin ]; then
    DEST=/usr/local/bin/totem
    cp "${TMP_DIR}/${BIN_NAME}" "$DEST"
elif command -v sudo >/dev/null 2>&1; then
    DEST=/usr/local/bin/totem
    sudo cp "${TMP_DIR}/${BIN_NAME}" "$DEST"
    info "permissões elevadas via sudo"
else
    DEST="$HOME/.local/bin/totem"
    mkdir -p "$HOME/.local/bin"
    cp "${TMP_DIR}/${BIN_NAME}" "$DEST"
    warn "instalado em ${DEST}. Garanta ~/.local/bin no PATH e reabra o terminal."
fi
ok "instalado em ${DEST}"

success_box "${RELEASE_TAG:-}" "$DEST"
