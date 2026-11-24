from __future__ import annotations

import asyncio
import os
import re
import tempfile
from pathlib import Path
from typing import Optional
from textual.app import App, ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    TextArea,
    Button,
    Footer,
    Input,
    Label,
    ProgressBar,
    Select,
    Static,
)

from totem_cli import __version__
from totem_cli.resources import package_dir
from totem_cli.core.disk import DiskDrive, get_removable_disks
from totem_cli.core.imager import (
    ImagerError,
    authorize_sudo,
    build_flash_command,
    ensure_imager,
    is_elevated,
    describe_flash_failure,
    read_imager_log_tail,
    wait_imager_log_tail,
    resolve_bookworm_image,
    unmount_device_partitions,
    verify_first_run_script,
    wipe_device_signatures,
)
from totem_cli.core.injector import TotemConfig, build_first_run_script, validate_config
from totem_cli.core.ssh import (
    RemoteTotemState,
    SshClientError,
    TotemSshClient,
    UnknownHostKeyError,
    VALID_ROTATIONS,
    WifiLinkDroppedError,
    validate_hostname,
    validate_timezone,
    validate_wifi_credentials,
)


def _validate_nexus_url(value: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse((value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SshClientError("Informe uma URL Nexus HTTP ou HTTPS válida.")
    return (value or "").strip()


TOTEM_ASCII = (package_dir() / "assets" / "ascii-art.txt").read_text(encoding="utf-8").rstrip("\n")


class BaseScreen(Screen[None]):
    def compose(self) -> ComposeResult:
        with Vertical(id="frame"):
            with Horizontal(id="main-layout"):
                with Vertical(id="sidebar"):
                    yield Label("SEU FLUXO", classes="kicker")
                    yield Static(id="flow-steps")
                    yield Label("PERFIL DO EQUIPAMENTO", classes="kicker")
                    yield Static("Raspberry Pi 5\nOpenbox → Chromium kiosk\nRaspberry Pi OS Bookworm · 64-bit", classes="side-note")
                    yield Static("●  PROVISIONAMENTO ATIVO\nAmbiente Industrial", classes="status-card")
                    yield Static("", id="flow-context", classes="side-note")
                    yield Static(f"CLI v{__version__}", id="app-version")
                with Vertical(id="right"):
                    with VerticalScroll(id="content"):
                        yield Static(id="page-title", classes="page-title")
                        yield Static(id="page-copy", classes="page-copy")
                        yield Vertical(id="form-area")
                    yield Horizontal(id="actions")
        yield Footer()

    def set_steps(self, steps: list[str], current: int) -> None:
        lines = []
        for index, name in enumerate(steps):
            mark = "✓" if index < current else ("●" if index == current else "○")
            lines.append(f"{index + 1:02d}  {mark}  {name}")
        self.query_one("#flow-steps", Static).update("\n".join(lines))

    def set_context(self, lines: list[str]) -> None:
        self.query_one("#flow-context", Static).update("\n".join(lines))


class HomeScreen(BaseScreen):
    CSS = """
    #sidebar { display: none; }
    #content { border: none; background: #090909; padding: 1 3; }
    #page-title, #page-copy { display: none; }
    #form-area { height: 1fr; layout: vertical; }
    #ascii-banner { height: 8; color: #ff3347; text-align: center; content-align: center middle; text-style: bold; }
    #choice-grid { height: 1fr; width: 1fr; grid-size: 2 1; grid-columns: 1fr 1fr; grid-rows: 1fr; grid-gutter: 0 2; }
    .choice {
        width: 1fr; height: 1fr; min-width: 38; min-height: 12;
        padding: 2 5; text-align: center; content-align: center middle;
    }
    #go-card { background: #c01428; color: white; border: round #e8263a; }
    #go-card:hover { background: #e8263a; }
    #go-ssh { background: #1a1a21; color: #f4f4f6; border: round #3a3a46; }
    #go-ssh:hover { background: #23232c; border: round #e8263a; }
    .choice:focus { border: heavy white; text-style: bold; }
    """

    def on_mount(self) -> None:
        area = self.query_one("#form-area", Vertical)
        area.mount(
            Static(TOTEM_ASCII, id="ascii-banner"),
            Grid(
                Button(
                    "[b #ffffff]01  /  NOVA INSTALAÇÃO[/]\n\n[b]PREPARAR CARTÃO SD[/b]\n\nGrave o Raspberry Pi OS Bookworm para Raspberry Pi 5 e configure o Totem para abrir o Nexus em modo quiosque.",
                    id="go-card",
                    classes="choice",
                ),
                Button(
                    "[b #ffffff]02  /  EQUIPAMENTO EXISTENTE[/]\n\n[b]CONECTAR VIA SSH[/b]\n\nAcesse um Raspberry Pi por IP para consultar o hostname e a configuração atual ou atualizar a URL do Nexus.",
                    id="go-ssh",
                    classes="choice",
                ),
                id="choice-grid",
            )
        )


class CardScreen(BaseScreen):
    def on_mount(self) -> None:
        self.set_steps(["Origem do sistema", "Cartão de destino", "Totem e rede", "Revisão"], 0)
        self.query_one("#page-title", Static).update("Escolha o sistema e o destino")
        self.query_one("#page-copy", Static).update(
            "Escolha Bookworm para Raspberry Pi 5 ou uma imagem customizada compatível. Cartões montados também aparecem; o Imager gerencia o acesso ao cartão."
        )

        area = self.query_one("#form-area", Vertical)
        area.mount(
            Label("ORIGEM DA IMAGEM", classes="section-label"),
            Select(
                [
                    ("Raspberry Pi OS (Legacy, 64-bit) Lite · Bookworm (recomendado)", "bookworm-lite"),
                    ("Raspberry Pi OS (Legacy, 64-bit) com Desktop · Bookworm", "bookworm-desktop"),
                    ("Imagem customizada da empresa (.img / .img.xz)", "custom"),
                ],
                value="bookworm-lite",
                id="os-choice",
            ),
            Label("ARQUIVO DE IMAGEM CUSTOMIZADA (OPCIONAL)", classes="section-label"),
            Input(placeholder="Ex.: /home/user/Totem_RPi5.img.xz", id="custom-image"),
            Label("CARTÃO SD / UNIDADE REMOVÍVEL DE DESTINO", classes="section-label"),
        )

        disks = get_removable_disks()
        if disks:
            options = [(d.display_label(), d.device) for d in disks]
            area.mount(Select(options, value=options[0][1], id="select-disk"))
        else:
            area.mount(
                Static(
                    "[b #ff3347]Nenhum cartão SD ou leitor USB removível detectado![/]\n"
                    "Conecte o cartão microSD e clique em 'Atualizar Discos'. Discos do sistema são ocultados.",
                    id="no-disks-warning",
                    classes="warning-card",
                )
            )

        self.query_one("#actions", Horizontal).mount(
            Button("Continuar", id="card-next", variant="primary"),
            Button("Atualizar Discos", id="refresh-disks"),
            Button("Cancelar", id="cancel"),
        )


class ConfirmWipeModal(ModalScreen[bool]):
    CSS = """
    ConfirmWipeModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }
    #dialog {
        padding: 1 3;
        width: 65;
        height: auto;
        border: heavy #ff3347;
        background: #141414;
    }
    #modal-title {
        color: #ff3347;
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
    }
    #modal-msg {
        color: white;
        margin-bottom: 1;
    }
    #modal-actions {
        height: 3;
        align: center middle;
    }
    """

    def __init__(self, target_disk: str, target_label: str = "") -> None:
        super().__init__()
        self.target_disk = target_disk
        self.target_label = target_label

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("ATENÇÃO: CONFIRMAÇÃO DE GRAVAÇÃO", id="modal-title")
            yield Static(
                f"Todos os dados nesta unidade serão [b]PERMANENTEMENTE APAGADOS[/].\n\n"
                f"Destino: [b #ff3347]{self.target_disk}[/]\n{self.target_label}\n\n"
                "Deseja realmente iniciar a gravação do sistema operacional?",
                id="modal-msg",
            )
            with Horizontal(id="modal-actions"):
                yield Button("SIM, GRAVAR E APAGAR", id="confirm-wipe", variant="error")
                yield Button("CANCELAR", id="cancel-wipe")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-wipe":
            self.dismiss(True)
        else:
            self.dismiss(False)


class SudoPasswordModal(ModalScreen[Optional[str]]):
    CSS = """
    SudoPasswordModal { align: center middle; background: rgba(0, 0, 0, 0.78); }
    #dialog { width: 68; height: auto; padding: 1 3; border: heavy #ff3347; background: #141414; }
    #modal-title { color: #ff3347; text-style: bold; text-align: center; margin-bottom: 1; }
    #modal-copy { color: #dddddd; margin-bottom: 1; }
    #modal-actions { height: 3; align: center middle; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("AUTORIZAÇÃO PARA GRAVAR", id="modal-title")
            yield Static("Digite sua senha sudo. Ela será usada para autorizar esta gravação e não será salva.", id="modal-copy")
            yield Input(placeholder="Senha sudo", password=True, id="sudo-password")
            with Horizontal(id="modal-actions"):
                yield Button("Autorizar e continuar", id="authorize-sudo", variant="primary")
                yield Button("Cancelar", id="cancel-sudo")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "authorize-sudo":
            password = self.query_one("#sudo-password", Input).value
            self.dismiss(password or None)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "sudo-password":
            self.dismiss(event.value or None)


class TrustHostKeyModal(ModalScreen[bool]):
    CSS = """
    TrustHostKeyModal { align: center middle; background: rgba(0, 0, 0, 0.78); }
    #dialog { width: 78; height: auto; padding: 1 3; border: heavy #ff3347; background: #141414; }
    #modal-title { color: #ff3347; text-style: bold; text-align: center; margin-bottom: 1; }
    #host-fingerprint { color: #ffffff; margin: 1 0; }
    #modal-actions { height: 3; align: center middle; }
    """

    def __init__(self, host: str, key_type: str, fingerprint: str) -> None:
        super().__init__()
        self.host = host
        self.key_type = key_type
        self.fingerprint = fingerprint

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("PRIMEIRO ACESSO SSH", id="modal-title")
            yield Static(
                f"O host [b]{self.host}[/] ainda não está salvo como confiável.\n\n"
                "Compare a impressão digital com o Raspberry Pi ou com o administrador da rede. "
                "Só confie se os valores coincidirem.",
                id="modal-msg",
            )
            yield Static(f"{self.key_type}\n{self.fingerprint}", id="host-fingerprint")
            with Horizontal(id="modal-actions"):
                yield Button("CONFIAR E SALVAR CHAVE", id="trust-host", variant="primary")
                yield Button("CANCELAR", id="cancel-trust")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "trust-host")


class ApplyConfirmModal(ModalScreen[bool]):
    CSS = """
    ApplyConfirmModal { align: center middle; background: rgba(0, 0, 0, 0.78); }
    #dialog { width: 72; height: auto; padding: 1 3; border: heavy #ff3347; background: #141414; }
    #modal-title { color: #ff3347; text-style: bold; text-align: center; margin-bottom: 1; }
    #modal-copy { color: #ffffff; margin: 1 0; }
    #modal-actions { height: 3; align: center middle; }
    """

    def __init__(self, changes: list[str]) -> None:
        super().__init__()
        self.changes = changes

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("APLICAR ALTERAÇÕES", id="modal-title")
            yield Static(
                "\n".join(f"• {change}" for change in self.changes)
                + "\n\nO Totem será REINICIADO ao final para concluir as alterações.",
                id="modal-copy",
            )
            with Horizontal(id="modal-actions"):
                yield Button("APLICAR E REINICIAR", id="confirm-apply", variant="primary")
                yield Button("CANCELAR", id="cancel-apply")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-apply")


class SshScreen(BaseScreen):
    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "ssh-host" or getattr(self, "_formatting_ssh_host", False):
            return

        raw_value = event.value
        digits = "".join(char for char in raw_value if char.isdigit())[:12]
        formatted = ".".join(digits[index:index + 3] for index in range(0, len(digits), 3))
        if digits and len(digits) < 12 and len(digits) % 3 == 0:
            formatted += "."
        if formatted != raw_value:
            digits_before_cursor = sum(char.isdigit() for char in raw_value[:event.input.cursor_position])
            separators_before_cursor = min(digits_before_cursor // 3, 3)
            cursor = digits_before_cursor + separators_before_cursor
            self._formatting_ssh_host = True
            event.input.value = formatted
            event.input.cursor_position = min(cursor, len(formatted))
            self._formatting_ssh_host = False

    def on_mount(self) -> None:
        self.set_steps(["Conectar", "Inspecionar", "Editar"], 0)
        self.query_one("#page-title", Static).update("Conecte ao Totem via SSH")
        self.query_one("#page-copy", Static).update(
            "Informe o endereço IP e as credenciais administrativas do Totem para inspecionar ou alterar os parâmetros."
        )
        self.query_one("#form-area", Vertical).mount(
            Label("ENDEREÇO IP DO RASPBERRY PI", classes="section-label"),
            Input(placeholder="Ex.: 192.168.202.110", id="ssh-host"),
            Label("USUÁRIO LINUX (EX.: usinagem / qualidade)", classes="section-label"),
            Input(placeholder="Ex.: usinagem", id="ssh-user"),
            Label("SENHA DO USUÁRIO", classes="section-label"),
            Input(placeholder="Digite a senha", password=True, id="ssh-password"),
            Label("CHAVE PRIVADA SSH (OPCIONAL)", classes="section-label"),
            Input(placeholder="Caminho do arquivo (ex.: ~/.ssh/id_rsa)", id="ssh-key"),
        )
        self.query_one("#actions", Horizontal).mount(
            Button("Conectar e Inspecionar", id="ssh-connect", variant="primary"),
            Button("Voltar", id="cancel"),
        )


class SshInventoryScreen(BaseScreen):
    """Screen displaying inspected parameters of an active Totem with action buttons."""
    STAGE_ORDER = ("hostname", "password", "nexus_url", "rotation", "wifi", "wifi_config")

    def __init__(self, state: RemoteTotemState, host: str, user: str, password: Optional[str] = None, key_file: Optional[str] = None) -> None:
        super().__init__()
        self.state = state
        self.host = host
        self.user = user
        self.password = password
        self.key_file = key_file
        self.pending: list[dict] = []

    def on_mount(self) -> None:
        self.set_steps(["Conectado", "Inventário", "Edição"], 1)
        self.query_one("#page-title", Static).update(f"Totem em Linha — {self.host}")
        self.query_one("#page-copy", Static).update(
            "Configurações ativas detectadas no sistema operacional do Raspberry Pi."
        )

        area = self.query_one("#form-area", Vertical)
        area.mount(
            Vertical(
                Label("IDENTIDADE E SISTEMA", classes="fieldset-title"),
                Static("Valores aplicados no Raspberry Pi. Hostname exige reinício do Totem.", classes="field-hint"),
                Static(self._identity_line(), id="inv-identity", classes="readonly-value"),
                Horizontal(
                    Vertical(
                        Label("Hostname", classes=""),
                        Input(value=self.state.hostname, id="new-hostname"),
                        classes="kv",
                    ),
                    Vertical(
                        Label("Nova senha (preencha para trocar)", classes=""),
                        Input(placeholder="Digite a nova senha", password=True, id="new-password"),
                        classes="kv",
                    ),
                    classes="kv-row",
                ),
                classes="fieldset",
            ),
            Vertical(
                Label("NEXUS E TELA", classes="fieldset-title"),
                Static("URL aberta no Chromium quiosque. Rotação exige reinício do ambiente gráfico.", classes="field-hint"),
                Static(f"URL atual: [b #8ee6cf]{self.state.nexus_url}[/]", id="inv-nexus", classes="readonly-value"),
                Static(f"Orientação atual: [b]{self.state.rotation}[/]", id="inv-rotation", classes="readonly-value"),
                Horizontal(
                    Vertical(
                        Label("URL do Nexus", classes=""),
                        Input(value=self.state.nexus_url, id="new-nexus-url"),
                        classes="kv",
                    ),
                    Vertical(
                        Label("Orientação", classes=""),
                        Select(
                            [
                                ("Paisagem — normal", "normal"),
                                ("Retrato — girar à esquerda", "left"),
                                ("Retrato — girar à direita", "right"),
                                ("Invertida — 180°", "inverted"),
                            ],
                            value=self.state.rotation if self.state.rotation in {"normal", "left", "right", "inverted"} else "left",
                            id="new-rotation",
                        ),
                        classes="kv",
                    ),
                    classes="kv-row",
                ),
                Label("STATUS DOS SERVIÇOS", classes="section-label"),
                Static(self._services_line(), id="inv-services", classes="side-note"),
                classes="fieldset",
            ),
            Vertical(
                Label("WI-FI", classes="fieldset-title"),
                Static("Se você está conectado pelo Wi-Fi, desativá-lo derruba esta sessão.", classes="field-hint"),
                Static(f"[b]{self._wifi_line()}[/]", id="inv-wifi", classes="readonly-value"),
                Horizontal(
                    Vertical(
                        Label("Rede (SSID)", classes=""),
                        Input(placeholder="Nome da rede", id="new-wifi-ssid"),
                        classes="kv",
                    ),
                    Vertical(
                        Label("Senha Wi-Fi", classes=""),
                        Input(placeholder="Senha da rede", password=True, id="new-wifi-password"),
                        classes="kv",
                    ),
                    classes="kv-row",
                ),
                Horizontal(
                    Label("Ação do Wi-Fi", classes="field-label"),
                    Select(
                        [
                            ("Manter como está", "keep"),
                            ("Ativar", "on"),
                            ("Desativar", "off"),
                        ],
                        value="keep",
                        id="wifi-action",
                    ),
                    classes="field-row",
                ),
                classes="fieldset",
            ),
            Vertical(
                Label("LOGS DO TOTEM", classes="fieldset-title"),
                Static("Leitura dos registros sem alterar o equipamento. Numeracao de linhas aparece à esquerda; role para ver o historico.", classes="field-hint"),
                Horizontal(
                    Select(
                        [
                            ("Provisionamento (firstboot)", "firstboot"),
                            ("Monitor do Nexus", "monitor"),
                            ("Chromium", "chromium"),
                            ("LightDM (journal)", "lightdm"),
                        ],
                        value="firstboot",
                        id="log-source",
                    ),
                    Button("Ver log", id="view-logs", classes="mini"),
                    classes="field-row",
                ),
                TextArea("Nenhum log carregado.", read_only=True, show_line_numbers=True, show_cursor=False, id="log-view"),
                classes="fieldset",
            ),
            Vertical(
                Label("ENVIAR ARQUIVO", classes="fieldset-title"),
                Static("Substitui a página offline ou o vídeo de abertura na home do usuário.", classes="field-hint"),
                Horizontal(
                    Select(
                        [("Página offline (offline.html)", "offline.html"), ("Vídeo de abertura (.mp4)", "splash")],
                        value="offline.html",
                        id="upload-target",
                    ),
                    Input(placeholder="Caminho local do arquivo", id="upload-path"),
                    Button("Enviar", id="upload-send", classes="mini"),
                    classes="field-row",
                ),
                classes="fieldset",
            ),
            Vertical(
                Label("ALTERAÇÕES PENDENTES", classes="fieldset-title"),
                Static(
                    "Edite os campos acima e marque as mudanças. "
                    "Nada é aplicado até você confirmar em Aplicar Alterações.",
                    classes="field-hint",
                ),
                Static("(nenhuma alteração pendente)", id="pending-list"),
                Static("", id="op-status"),
                classes="fieldset",
            ),
        )
        self.query_one("#actions", Horizontal).mount(
            Button("Aplicar Alterações", id="apply-staged", variant="primary", disabled=True),
            Button("Reiniciar Gráfico", id="restart-gfx"),
            Button("Atualizar Status", id="refresh-status"),
            Button("Reiniciar Totem", id="reboot-system"),
            Button("Desconectar", id="cancel"),
        )

    def _diff_items(self) -> tuple[list[dict], list[str]]:
        """Compare the form against the live state. Returns (changes, problems)."""
        items: list[dict] = []
        problems: list[str] = []
        try:
            hostname = self.query_one("#new-hostname", Input).value.strip()
            if hostname and hostname != self.state.hostname:
                try:
                    validate_hostname(hostname)
                    items.append({"kind": "hostname", "label": "Hostname", "detail": hostname, "payload": hostname})
                except SshClientError as exc:
                    problems.append(f"Hostname: {exc}")
            password = self.query_one("#new-password", Input).value
            if password:
                if any(ch in password for ch in "\r\n\x00"):
                    problems.append("Senha: valor inválido.")
                else:
                    items.append({"kind": "password", "label": "Senha do usuário", "detail": "nova senha definida", "payload": password})
            url = self.query_one("#new-nexus-url", Input).value.strip()
            if url and url != self.state.nexus_url:
                try:
                    items.append({"kind": "nexus_url", "label": "URL do Nexus", "detail": _validate_nexus_url(url), "payload": _validate_nexus_url(url)})
                except SshClientError as exc:
                    problems.append(f"URL do Nexus: {exc}")
            rotation = str(self.query_one("#new-rotation", Select).value)
            if rotation != self.state.rotation:
                if rotation not in VALID_ROTATIONS:
                    problems.append(f"Rotação: {rotation} inválida.")
                else:
                    items.append({"kind": "rotation", "label": "Rotação da tela", "detail": rotation, "payload": rotation})
            ssid = self.query_one("#new-wifi-ssid", Input).value.strip()
            psk = self.query_one("#new-wifi-password", Input).value
            action = str(self.query_one("#wifi-action", Select).value)
            if ssid or psk:
                try:
                    clean_ssid, clean_psk = validate_wifi_credentials(ssid, psk)
                    items.append({"kind": "wifi_config", "label": "Rede Wi-Fi", "detail": clean_ssid, "payload": (clean_ssid, clean_psk)})
                except SshClientError as exc:
                    problems.append(f"Wi-Fi: {exc}")
            elif action in ("on", "off"):
                items.append({
                    "kind": "wifi",
                    "label": "Wi-Fi",
                    "detail": "ativar" if action == "on" else "desativar",
                    "payload": action == "on",
                })
        except Exception:
            pass
        ordered = sorted(items, key=lambda item: self.STAGE_ORDER.index(item["kind"]))
        return ordered, problems

    def _refresh_pending(self) -> None:
        """Live diff preview. Apply is only enabled with valid detected changes."""
        try:
            items, problems = self._diff_items()
        except Exception:
            return
        self.pending = items
        lines = [f"{i}. {item['label']} → {item['detail']}" for i, item in enumerate(items, 1)]
        lines += [f"✗ {problem}" for problem in problems]
        if not lines:
            lines = ["(nenhuma alteração pendente)"]
        try:
            self.query_one("#pending-list", Static).update("\n".join(lines))
            apply_btn = self.query_one("#apply-staged", Button)
            apply_btn.disabled = not items or bool(problems)
            apply_btn.label = f"Aplicar Alterações ({len(items)})" if items else "Aplicar Alterações"
        except Exception:
            pass

    def on_input_changed(self, event: Input.Changed) -> None:
        if self.is_mounted and event.input.id and event.input.id.startswith("new-"):
            self._refresh_pending()

    def on_select_changed(self, event: Select.Changed) -> None:
        if self.is_mounted and event.select.id in {"new-rotation", "wifi-action"}:
            self._refresh_pending()

    def op_status(self, lines: list[str]) -> None:
        self.query_one("#op-status", Static).update("\n".join(lines))

    def _services_line(self) -> str:
        lightdm_status = "[b #8ee6cf]ATIVO[/]" if self.state.lightdm_active else "[b #ff3347]INATIVO[/]"
        kiosk_status = "[b #8ee6cf]RODANDO[/]" if self.state.chromium_running else "[b #ff3347]PARADO[/]"
        provisioned = "provisionado" if self.state.provisioned else "primeiro boot pendente"
        return f"LightDM: {lightdm_status}   ·   Chromium Kiosk: {kiosk_status}   ·   {provisioned}"

    def _identity_line(self) -> str:
        return (
            f"Hostname atual: [b #ff6675]{self.state.hostname}[/]"
            f"    ·    Fuso: [b]{self.state.timezone}[/]"
            f"    ·    Login: [b #ff6675]{self.state.autologin_user}[/]"
        )

    def _wifi_line(self) -> str:
        radio = "ativado" if self.state.wifi_enabled else "desativado"
        return f"Wi-Fi {radio}   ·   Rede: {self.state.wifi_ssid}"

    def refresh_from(self, state: RemoteTotemState) -> None:
        """Repaint inventory values after a successful remote edit."""
        self.state = state
        self.query_one("#inv-identity", Static).update(self._identity_line())
        self.query_one("#inv-nexus", Static).update(f"[b #8ee6cf]{state.nexus_url}[/]")
        self.query_one("#inv-rotation", Static).update(f"[b]{state.rotation}[/]")
        self.query_one("#inv-services", Static).update(self._services_line())
        self.query_one("#inv-wifi", Static).update(f"[b]{self._wifi_line()}[/]")


class ProfileScreen(BaseScreen):
    def on_mount(self) -> None:
        self.set_steps(["Origem e destino", "Totem e rede", "Revisão"], 1)
        self.query_one("#page-title", Static).update("Identifique este Totem")
        self.query_one("#page-copy", Static).update("Cada seção mostra onde a informação será aplicada no Totem.")
        app = self.app
        if getattr(app, "active_target_disk", ""):
            if getattr(app, "active_image_variant", ""):
                variant = app.active_image_variant
                image_label = "Raspberry Pi OS Bookworm " + ("Lite" if variant == "lite" else "Desktop")
            elif getattr(app, "active_image_source", ""):
                image_label = Path(app.active_image_source).name
            else:
                image_label = "—"
            self.set_context([f"Destino: {app.active_target_disk}", f"Imagem: {image_label}"])
        area = self.query_one("#form-area", Vertical)
        area.mount(
            Vertical(
                Label("IDENTIDADE DO EQUIPAMENTO", classes="fieldset-title"),
                Static("Nome do Totem na rede e no sistema. Aplicado como hostname no primeiro boot.", classes="field-hint"),
                Horizontal(
                    Label("Hostname", classes=""),
                    Input("totem-usinagem-01", placeholder="Ex.: qualidade-rpi5-01", id="hostname"),
                    classes="field-row",
                ),
                classes="fieldset",
            ),
            Vertical(
                Label("ACESSO AO SISTEMA", classes="fieldset-title"),
                Static("Conta Linux criada no primeiro boot. Usada no login automático do LightDM e no acesso SSH.", classes="field-hint"),
                Horizontal(
                    Label("Usuário Linux", classes=""),
                    Input("usinagem", placeholder="Ex.: usinagem", id="username"),
                    classes="field-row",
                ),
                Horizontal(
                    Label("Senha", classes=""),
                    Input("", placeholder="Defina uma senha para este Totem", id="linux-password", password=True),
                    classes="field-row",
                ),
                classes="fieldset",
            ),
            Vertical(
                Label("APLICAÇÃO NEXUS", classes="fieldset-title"),
                Static("Endereço aberto em tela cheia no Chromium em modo quiosque.", classes="field-hint"),
                Horizontal(
                    Label("URL do Nexus", classes=""),
                    Input("http://192.168.202.110:3001/auth", placeholder="Ex.: http://servidor:3001/auth", id="nexus-url"),
                    classes="field-row",
                ),
                classes="fieldset",
            ),
            Vertical(
                Label("TELA", classes="fieldset-title"),
                Static("Rotação aplicada via xrandr ao iniciar a sessão gráfica.", classes="field-hint"),
                Horizontal(
                    Label("Orientação", classes=""),
                    Select(
                        [
                            ("Paisagem — normal", "normal"),
                            ("Retrato — girar à esquerda", "left"),
                            ("Retrato — girar à direita", "right"),
                            ("Invertida — 180°", "inverted"),
                        ],
                        value="left",
                        id="rotation",
                    ),
                    classes="field-row",
                ),
                classes="fieldset",
            ),
            Vertical(
                Label("REDE E REGIÃO", classes="fieldset-title"),
                Static("Ethernet usa DHCP. O Wi-Fi é configurado via NetworkManager quando preenchido.", classes="field-hint"),
                Horizontal(
                    Label("Fuso horário", classes=""),
                    Input("America/Fortaleza", placeholder="Região/Cidade", id="timezone"),
                    classes="field-row",
                ),
                Horizontal(
                    Label("Wi-Fi (opcional)", classes=""),
                    Input(placeholder="Nome da rede (SSID)", id="wifi-ssid"),
                    classes="field-row",
                ),
                Horizontal(
                    Label("Senha Wi-Fi", classes=""),
                    Input(placeholder="Senha da rede", id="wifi-password", password=True),
                    classes="field-row",
                ),
                classes="fieldset",
            ),
        )
        self.query_one("#actions", Horizontal).mount(
            Button("Iniciar Gravação e Provisionamento", id="start-flash", variant="primary"),
            Button("Voltar", id="cancel"),
        )


class ProgressFlashScreen(BaseScreen):
    def __init__(self, target_disk: str, config: TotemConfig, image_source: str,
                 variant: str = "", target_model: str = "", target_size: int = 0,
                 sudo_password: Optional[str] = None) -> None:
        super().__init__()
        self.target_disk = target_disk
        self.config = config
        self.image_source = image_source
        self.variant = variant
        self.target_model = target_model
        self.target_size = target_size
        self.sudo_password = sudo_password
        self.operation_finished = False
        self.succeeded = False

    async def on_mount(self) -> None:
        self.set_steps(["Origem e destino", "Totem e rede", "Gravação"], 2)
        self.set_context([f"Destino: {self.target_disk}"])
        self.query_one("#page-title", Static).update("Preparando o cartão SD")
        self.query_one("#page-copy", Static).update(
            f"Destino: [b #ff3347]{self.target_disk}[/]. O conteúdo atual será apagado após a validação do dispositivo."
        )

        area = self.query_one("#form-area", Vertical)
        await area.mount(
            Static("Preparando…", id="flash-phase"),
            Horizontal(
                ProgressBar(total=100, show_percentage=False, show_eta=False, id="flash-progress"),
                Static("0%", id="flash-percent"),
                id="flash-progress-row",
            ),
            VerticalScroll(
                Static("Iniciando…", id="flash-detail"),
                id="flash-detail-wrap",
            ),
            Label("ETAPAS", classes="section-label"),
            Static("", id="provisioning-log"),
        )
        self.query_one("#actions", Horizontal).mount(
            Button("Aguarde…", id="done-flash", variant="primary", disabled=True),
        )
        self._set_phase(0, "Validando autorização e o cartão selecionado.")

        self.run_worker(self._perform_flashing(), exclusive=True)

    PHASES = (
        "Autorização e validação do cartão",
        "Localizar o Imager e a imagem Bookworm",
        "Preparar a configuração do Totem",
        "Apagar e gravar a imagem no cartão",
        "Verificar a gravação",
        "Conferir a configuração no cartão",
    )

    def _set_phase(self, index: int, detail: str = "", percent: Optional[int] = None) -> None:
        phase = self.query_one("#flash-phase", Static)
        phase.update(f"Etapa {index + 1} de {len(self.PHASES)} — {self.PHASES[index]}")
        if detail:
            self.query_one("#flash-detail", Static).update(detail)
        if percent is not None:
            pct = max(0, min(100, int(percent)))
            self.query_one("#flash-progress", ProgressBar).update(progress=pct)
            self.query_one("#flash-percent", Static).update(f"{pct}%")
        lines = []
        for i, name in enumerate(self.PHASES):
            mark = "✓" if i < index else ("●" if i == index else "○")
            lines.append(f"{mark}  {name}")
        self.query_one("#provisioning-log", Static).update("\n".join(lines))

    async def _perform_flashing(self) -> None:
        done_button = self.query_one("#done-flash", Button)
        progress = self.query_one("#flash-progress", ProgressBar)

        def phase(index: int, detail: str, percent: Optional[int] = None) -> None:
            self._set_phase(index, detail, percent)
        try:
            if not is_elevated():
                if os.name == "nt":
                    raise ImagerError("Abra a CLI em um terminal como Administrador; nenhum dado foi escrito.")
                if not self.sudo_password:
                    raise ImagerError("A gravação foi cancelada porque a autorização sudo não foi fornecida.")
                phase(0, "Validando sua autorização (sudo)…", 2)
                sudo_password = self.sudo_password
                self.sudo_password = None
                await asyncio.to_thread(authorize_sudo, sudo_password)
                sudo_password = None

            phase(0, "Revalidando o cartão selecionado (modelo e tamanho)…", 5)
            current_disks = await asyncio.to_thread(get_removable_disks)
            disk = next((item for item in current_disks if item.device == self.target_disk), None)
            if disk is None or (self.target_size and disk.size_bytes != self.target_size) \
                    or (self.target_model and disk.model != self.target_model):
                raise ImagerError("O cartão não está mais disponível como unidade removível. Nenhum dado foi escrito.")

            phase(1, "Localizando o Raspberry Pi Imager…", 8)
            executable = await asyncio.to_thread(ensure_imager)
            expected_hash: Optional[str] = None
            image_uri = self.image_source
            expected_size = 0
            if self.variant:
                phase(1, "Consultando o catálogo oficial — Raspberry Pi OS Bookworm…", 12)
                image = await asyncio.to_thread(resolve_bookworm_image, self.variant)
                image_uri = image.url
                expected_hash = image.sha256
                expected_size = image.size_bytes
                phase(1, f"Imagem: {image.name} · {image.release_date}", 18)
            elif not Path(image_uri).is_file():
                raise ImagerError("O arquivo de imagem customizada não existe ou não pode ser acessado.")

            if expected_size and disk.size_bytes < expected_size:
                raise ImagerError(
                    f"O cartão tem {disk.size_gb:.1f} GB, mas a imagem exige pelo menos {expected_size / (1024 ** 3):.1f} GB."
                )

            phase(2, "Gerando hostname, usuário, rede e o script de primeiro boot…", 22)
            first_run_script = await asyncio.to_thread(build_first_run_script, self.config)
            # Stable path on purpose: the failure panel prints this exact
            # command, and the operator can re-run it by hand to isolate
            # CLI vs Imager issues. The temp dir used to vanish on exit.
            script_path = Path(tempfile.gettempdir()) / "totem-first-run.sh"
            script_path.write_text(first_run_script, encoding="utf-8", newline="\n")

            # Revalidate the selected card as close as possible to the write.
            fresh_disks = await asyncio.to_thread(get_removable_disks)
            confirmed = next((item for item in fresh_disks if item.device == self.target_disk), None)
            if confirmed is None or confirmed.size_bytes != disk.size_bytes or confirmed.model != disk.model:
                raise ImagerError("A unidade selecionada mudou durante a preparação. Nenhum dado foi escrito.")
            if os.name != "nt":
                phase(3, "Desmontando as partições do cartão…", 25)
                await asyncio.to_thread(unmount_device_partitions, self.target_disk)
                phase(3, "Apagando o conteúdo anterior do cartão…", 27)
                await asyncio.to_thread(wipe_device_signatures, self.target_disk)
            # Keep the Imager log outside the temp dir so the operator can
            # send it for diagnosis even after this screen closes.
            log_path = Path(tempfile.gettempdir()) / "totem-imager.log"
            console_log_path = Path(tempfile.gettempdir()) / "totem-imager-console.log"
            command = build_flash_command(
                executable, image_uri, self.target_disk, expected_hash, str(script_path),
                str(log_path),
            )
            phase(3, "Gravando a imagem com o Raspberry Pi Imager — não remova o cartão…", 25)
            phase(3, "Iniciando a gravação…", 28)
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            last_output = ""
            output_buffer = ""
            assert process.stdout is not None
            with console_log_path.open("wb") as console_log:
                console_log.write(b"Saida capturada do Raspberry Pi Imager:\n")
                while True:
                    chunk = await process.stdout.read(1024)
                    if not chunk:
                        break
                    console_log.write(chunk)
                    console_log.flush()
                    output_buffer += chunk.decode("utf-8", errors="replace")
                    lines = output_buffer.replace("\r", "\n").split("\n")
                    output_buffer = lines.pop()
                    for line in lines:
                        last_output = line.strip()
                        if not last_output:
                            continue
                        self.query_one("#flash-detail", Static).update(last_output[-400:])
                        percent_match = re.search(r"(\d{1,3})\s*%", last_output)
                        if percent_match:
                            percent = min(100, int(percent_match.group(1)))
                            if "verif" in last_output.lower():
                                self._set_phase(4, last_output[-400:], 70 + int(percent * 0.25))
                            else:
                                self._set_phase(3, last_output[-400:], 25 + int(percent * 0.45))
            if output_buffer.strip():
                last_output = output_buffer.strip()
            return_code = await process.wait()
            if return_code != 0:
                log_tail = await asyncio.to_thread(wait_imager_log_tail, str(log_path))
                console_tail = await asyncio.to_thread(read_imager_log_tail, str(console_log_path))
                detail = await asyncio.to_thread(
                    describe_flash_failure,
                    return_code,
                    console_tail,
                    last_output,
                    log_tail,
                    command,
                    executable,
                    str(script_path),
                    str(console_log_path),
                    str(log_path),
                )
                raise ImagerError(detail)

            if os.name != "nt":
                phase(5, "Montando a partição de boot (somente leitura) para conferir o firstrun.sh…", 95)
                pass
                await asyncio.to_thread(
                    verify_first_run_script, self.target_disk, self.config.hostname
                )

            self._set_phase(len(self.PHASES) - 1, "", 100)
            self.query_one("#provisioning-log", Static).update(
                "\n".join(f"✓  {name}" for name in self.PHASES)
            )
            self.query_one("#flash-phase", Static).update("Concluído — cartão pronto")
            self.query_one("#flash-detail", Static).update(
                "Cartão gravado, verificado e com o perfil Totem para o primeiro boot. "
                "Pode remover o cartão e ligar o Raspberry Pi."
            )
            done_button.label = "Concluir — voltar ao início"
            self.succeeded = True
        except Exception as exc:
            self.query_one("#flash-phase", Static).update("Não foi possível concluir")
            self.query_one("#flash-detail", Static).update(f"Falha: {exc}")
            self.query_one("#flash-detail-wrap", VerticalScroll).styles.height = 24
            done_button.label = "Voltar"
        finally:
            self.operation_finished = True
            done_button.disabled = False


class TotemApp(App[None]):
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    /* Design system — Sangati red on charcoal, one accent, no decoration. */
    $accent: #e8263a;
    $accent-hover: #ff3b52;
    $bg: #0c0c0f;
    $panel: #141419;
    $panel-2: #1a1a21;
    $line: #2b2b34;
    $ink: #f4f4f6;
    $muted: #a7a7b3;
    $ok: #3ecf8e;

    Screen { background: $bg; color: $ink; }
    Footer { background: $panel; color: $muted; }
    Footer > .footer--highlight { background: $accent; color: white; }

    #frame { height: 1fr; padding: 1 2; }
    #main-layout { height: 1fr; }
    #right { height: 1fr; }

    /* Left rail: where am I, what device is this. */
    #sidebar {
        width: 32; height: 1fr; margin-right: 2; padding: 2 2;
        background: $panel; border: round $line;
    }
    .kicker { color: $accent; text-style: bold; margin: 1 0 1 0; }
    #flow-steps { color: $ink; margin-bottom: 1; }
    #flow-context { margin-top: 1; }
    #app-version { color: $accent; text-style: bold; margin-top: 1; }
    .fieldset { height: auto; border: round $line; background: $panel-2; padding: 1 2; margin-bottom: 1; }
    .fieldset-title { color: $accent; text-style: bold; }
    .field-hint { color: $muted; margin-bottom: 1; }
    .field-row { height: 3; margin-bottom: 1; align: left middle; }
    .field-row > Label { width: 32; height: 3; content-align: right middle; padding-right: 2; color: $ink; text-style: bold; }
    .field-row > Input { width: 1fr; height: 3; margin-bottom: 0; }
    .field-row > Select { width: 1fr; margin-bottom: 0; }
    .field-row > Button.mini { min-width: 12; width: auto; }
    .kv-row { layout: grid; grid-size: 2 1; grid-columns: 1fr 1fr; grid-gutter: 0 2; height: auto; margin-bottom: 1; }
    .kv { height: auto; }
    .kv > Label { color: $ink; text-style: bold; height: 1; }
    .kv > Input { width: 1fr; height: 3; margin-bottom: 1; }
    .kv > Select { width: 1fr; margin-bottom: 1; }
    .kv > Button { width: 1fr; margin-bottom: 0; }
    .btn-row { height: 3; margin-top: 1; align: left middle; }
    .btn-row > Button { margin-right: 1; }
    #log-view { background: $panel-2; border: round $line; padding: 1 2; height: 18; max-height: 32; color: $ink; }
    .side-note { color: $muted; }
    .status-card {
        margin-top: 2; padding: 1 2; height: auto;
        background: $panel-2; border: round $line; color: $ink;
    }

    /* Main work area. */
    #content { height: 1fr; background: $panel; border: round $line; padding: 2 3; }
    #page-title { color: $ink; text-style: bold; height: 2; }
    #page-copy { color: $muted; height: auto; margin-bottom: 1; }
    #form-area { height: auto; }
    #actions { height: 3; margin-top: 2; align: left middle; }

    /* Forms: label above, field below, accent only on focus. */
    .section-label { color: $ink; text-style: bold; margin: 1 0 0 0; }
    .hint, .description { color: $muted; }
    .readonly-value { color: $accent-hover; text-style: bold; margin: 0 0 1 0; }
    Input { background: $panel-2; border: tall $line; margin-bottom: 1; }
    Input:focus { border: tall $accent; }
    Select { margin-bottom: 1; }
    Select:focus > SelectCurrent { border: tall $accent; }
    Input.-invalid { border: tall $accent; }

    /* Buttons: one primary action per screen. */
    Button {
        min-width: 18; height: 3; margin-right: 1;
        background: $panel-2; color: $ink; border: round $line;
    }
    Button:hover { background: #23232c; border: round $muted; }
    Button:focus { border: heavy $accent; text-style: bold; }
    Button.-primary { background: $accent; color: white; border: round $accent; text-style: bold; }
    Button.-primary:hover { background: $accent-hover; }
    Button.-error { background: $accent; color: white; border: round white; text-style: bold; }
    Button:disabled { background: $panel; color: $muted; border: round $line; text-style: none; }

    .warning-card {
        background: #2a1216; color: $ink;
        border: round $accent; padding: 1 2; margin: 1 0;
    }

    /* Flash progress: full-width bar, big percentage, phase checklist. */
    #flash-phase { color: $ink; text-style: bold; height: 2; }
    #flash-detail-wrap {
        height: 3; margin: 1 0; padding: 0 1;
        background: $panel-2; border: round $line;
    }
    #flash-detail { color: $muted; height: auto; }
    #flash-progress-row { height: 1; margin: 1 0; align: left middle; }
    #flash-progress { width: 1fr; height: 1; }
    #flash-progress Bar > .bar--bar { background: $accent; color: $accent; }
    #flash-progress Bar > .bar--complete { background: $ok; color: $ok; }
    #flash-percent { width: 7; color: $ink; text-style: bold; text-align: right; }
    #provisioning-log { color: $muted; margin-top: 1; }
    """
    BINDINGS = [("q", "quit", "Sair"), ("escape", "back", "Voltar")]

    active_target_disk: str = ""
    active_image_source: str = ""
    active_image_variant: str = ""
    active_target_model: str = ""
    active_target_size: int = 0

    def on_mount(self) -> None:
        self.push_screen(HomeScreen())

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        wid = event.button.id
        if wid == "go-card":
            self.push_screen(CardScreen())
        elif wid == "go-ssh":
            self.push_screen(SshScreen())
        elif wid == "card-next":
            try:
                select = self.screen.query_one("#select-disk", Select)
                target = select.value
                if target:
                    selected_disk = next((item for item in get_removable_disks() if item.device == str(target)), None)
                    if selected_disk is None:
                        self.notify("O cartão deixou de estar disponível. Atualize a lista antes de continuar.", severity="error")
                        return
                    source_choice = self.screen.query_one("#os-choice", Select).value
                    if source_choice == "custom":
                        image_path = Path(self.screen.query_one("#custom-image", Input).value.strip()).expanduser()
                        if not image_path.is_file() or not image_path.name.lower().endswith((".img", ".img.xz")):
                            self.notify("Selecione um arquivo .img ou .img.xz válido.", severity="error")
                            return
                        self.active_image_source = str(image_path.resolve())
                        self.active_image_variant = ""
                    else:
                        self.active_image_source = ""
                        self.active_image_variant = str(source_choice).removeprefix("bookworm-")
                    self.active_target_disk = str(target)
                    self.active_target_model = selected_disk.model
                    self.active_target_size = selected_disk.size_bytes

                    def on_modal_result(confirmed: bool) -> None:
                        if confirmed:
                            self.push_screen(ProfileScreen())

                    self.push_screen(
                        ConfirmWipeModal(
                            self.active_target_disk,
                            f"{selected_disk.model} · {selected_disk.size_gb:.1f} GB"
                            + (f" · montado em {', '.join(selected_disk.mountpoints)}" if selected_disk.mountpoints else ""),
                        ),
                        on_modal_result,
                    )
                else:
                    self.notify("Por favor, selecione um cartão de destino.", severity="error")
            except Exception:
                self.notify("Nenhum disco disponível para gravação.", severity="error")
        elif wid == "refresh-disks":
            self.pop_screen()
            self.push_screen(CardScreen())
        elif wid == "start-flash":
            hostname = self.screen.query_one("#hostname", Input).value
            username = self.screen.query_one("#username", Input).value
            password = self.screen.query_one("#linux-password", Input).value
            nexus_url = self.screen.query_one("#nexus-url", Input).value
            timezone = self.screen.query_one("#timezone", Input).value
            rotation = self.screen.query_one("#rotation", Select).value
            wifi_ssid = self.screen.query_one("#wifi-ssid", Input).value
            wifi_password = self.screen.query_one("#wifi-password", Input).value

            if not password:
                self.notify("Defina a senha Linux deste Totem antes de continuar.", severity="error")
                return

            cfg = TotemConfig(
                hostname=hostname or "totem-rpi5",
                username=username or "usinagem",
                password=password,
                nexus_url=nexus_url or "http://192.168.202.110:3001/auth",
                timezone=timezone or "America/Fortaleza",
                screen_rotation=str(rotation or "left"),
                wifi_ssid=wifi_ssid or None,
                wifi_password=wifi_password or None,
            )
            try:
                validate_config(cfg)
            except ValueError as exc:
                self.notify(str(exc), severity="error")
                return
            if os.name == "nt" and not is_elevated():
                self.notify("No Windows, abra o terminal como Administrador para gravar o cartão.", severity="error")
            elif os.name != "nt" and not is_elevated():
                self.push_screen(
                    SudoPasswordModal(),
                    lambda password: self._start_flash(cfg, password) if password else None,
                )
            else:
                self._start_flash(cfg)
        elif wid == "done-flash":
            if isinstance(self.screen, ProgressFlashScreen) and self.screen.succeeded:
                # Only a successful flash ("Concluir") returns to the start.
                while len(self.screen_stack) > 1 and not isinstance(self.screen, HomeScreen):
                    await self.pop_screen()
            else:
                # "Voltar" after a failure just goes back one step.
                self.pop_screen()
        elif wid == "cancel" and isinstance(self.screen, SshInventoryScreen):
            # Clear connection identity when returning from the connected Totem.
            ssh_screen = self.screen_stack[-2]
            ssh_screen.query_one("#ssh-host", Input).value = ""
            ssh_screen.query_one("#ssh-user", Input).value = ""
            self.pop_screen()
        elif wid in {"cancel", "review"}:
            self.pop_screen()
        elif wid == "ssh-connect":
            host = self.screen.query_one("#ssh-host", Input).value
            user = self.screen.query_one("#ssh-user", Input).value
            password = self.screen.query_one("#ssh-password", Input).value
            key_file = self.screen.query_one("#ssh-key", Input).value
            self.notify(f"Conectando a {user}@{host}...", title="SSH Client")
            event.button.loading = True
            self.run_worker(self._connect_ssh(host, user, password, key_file), exclusive=True)
        elif wid == "stage-hostname":
            value = self.screen.query_one("#new-hostname", Input).value
            if value.strip() == self.screen.state.hostname:
                self.notify("Hostname já é o atual; nenhuma alteração detectada.", severity="warning")
            else:
                self._stage_validated("hostname", "Hostname", value, lambda v: validate_hostname(v))
        elif wid == "stage-password":
            value = self.screen.query_one("#new-password", Input).value
            if not value or any(ch in value for ch in "\r\n\x00"):
                self.notify("Defina uma senha válida.", severity="error")
            else:
                self.screen.stage_change("password", "Senha do usuário", "nova senha definida", value)
        elif wid == "stage-url":
            value = self.screen.query_one("#new-nexus-url", Input).value
            if value.strip() == self.screen.state.nexus_url:
                self.notify("URL ja e a atual; nenhuma alteracao detectada.", severity="warning")
            else:
                self._stage_validated("nexus_url", "URL do Nexus", value, _validate_nexus_url)
        elif wid == "stage-rotation":
            value = str(self.screen.query_one("#new-rotation", Select).value)
            if value not in VALID_ROTATIONS:
                self.notify("Rotação inválida.", severity="error")
            elif value == self.screen.state.rotation:
                self.notify("Rotação já é a atual; nenhuma alteração detectada.", severity="warning")
            else:
                self.screen.stage_change("rotation", "Rotação da tela", value, value)
        elif wid == "stage-wifi-on":
            self.screen.stage_change("wifi", "Wi-Fi", "ativar", True)
        elif wid == "stage-wifi-off":
            self.screen.stage_change("wifi", "Wi-Fi", "desativar", False)
        elif wid == "stage-wifi-config":
            ssid = self.screen.query_one("#new-wifi-ssid", Input).value
            psk = self.screen.query_one("#new-wifi-password", Input).value
            try:
                validate_wifi_credentials(ssid, psk)
            except SshClientError as exc:
                self.notify(f"{exc}", severity="error")
            else:
                self.screen.stage_change("wifi_config", "Rede Wi-Fi", ssid, (ssid.strip(), psk))
        elif wid == "apply-staged":
            await self._confirm_and_apply_staged()
        elif wid == "refresh-status":
            await self._with_loading("refresh-status", self._refresh_inventory_status())
        elif wid == "restart-gfx":
            await self._with_loading("restart-gfx", self._restart_remote_graphics())
        elif wid == "view-logs":
            await self._with_loading("view-logs", self._ssh_read_logs())
        elif wid == "upload-send":
            await self._with_loading("upload-send", self._ssh_upload())
        elif wid == "reboot-system":
            screen = self.screen
            client = self._ssh_client_for_screen(screen)
            event.button.loading = True
            try:
                await asyncio.to_thread(client.connect)
                await asyncio.to_thread(client.reboot)
                self.notify("Reiniciando o Totem. A conexão será encerrada.", title="Totem")
            except Exception as exc:
                self.notify(f"Não foi possível reiniciar: {exc}", title="Falha SSH", severity="error")
            finally:
                await asyncio.to_thread(client.close)
                event.button.loading = False

    def _start_flash(self, config: TotemConfig, sudo_password: Optional[str] = None) -> None:
        self.push_screen(
            ProgressFlashScreen(
                self.active_target_disk,
                config,
                self.active_image_source,
                self.active_image_variant,
                self.active_target_model,
                self.active_target_size,
                sudo_password,
            )
        )

    async def _connect_ssh(self, host: str, user: str, password: str,
                           key_file: str, expected_fingerprint: Optional[str] = None) -> None:
        client = None
        try:
            client = TotemSshClient(
                host=host.strip(),
                user=user.strip(),
                password=password or None,
                key_filename=str(Path(key_file).expanduser()) if key_file.strip() else None,
                timeout=8,
                expected_host_fingerprint=expected_fingerprint,
            )
            await asyncio.to_thread(client.connect)
            state = await asyncio.to_thread(client.inspect_totem)
            self.push_screen(SshInventoryScreen(state, host.strip(), user.strip(), password, key_file or None))
        except UnknownHostKeyError as exc:
            fingerprint = exc.fingerprint
            key_type = exc.key_type
            def on_trust_result(confirmed: bool) -> None:
                if confirmed:
                    self.run_worker(
                        self._connect_ssh(host, user, password, key_file, fingerprint),
                        exclusive=True,
                    )
            self.push_screen(TrustHostKeyModal(host.strip(), key_type, fingerprint), on_trust_result)
        except Exception as exc:
            self.notify(f"Falha SSH: {exc}. Nenhum inventário foi apresentado.", title="Conexão falhou", severity="error")
        finally:
            if client:
                await asyncio.to_thread(client.close)
            try:
                self.screen.query_one("#ssh-connect", Button).loading = False
            except Exception:
                pass

    def _ssh_client_for_screen(self, screen) -> TotemSshClient:
        return TotemSshClient(
            screen.host, screen.user,
            password=screen.password, key_filename=screen.key_file, timeout=8,
        )

    def _stage_validated(self, kind: str, label: str, value: str, validator) -> None:
        try:
            clean = validator(value)
        except SshClientError as exc:
            self.notify(f"{exc}", severity="error")
            return
        detail = value.strip() if isinstance(value, str) else str(clean)
        self.screen.stage_change(kind, label, detail, clean)

    async def _confirm_and_apply_staged(self) -> None:
        screen = self.screen
        if not screen.pending:
            self.notify("Nenhuma alteração pendente.", severity="warning")
            return
        changes = [f"{item['label']} → {item['detail']}" for item in screen.pending]

        def on_confirm(confirmed: bool) -> None:
            if confirmed:
                try:
                    screen.query_one("#apply-staged", Button).loading = True
                except Exception:
                    pass
                self.run_worker(self._apply_staged(), exclusive=True)

        self.push_screen(ApplyConfirmModal(changes), on_confirm)

    async def _apply_staged(self) -> None:
        screen = self.screen
        staged = sorted(screen.pending, key=lambda item: screen.STAGE_ORDER.index(item["kind"]))
        screen.pending = []
        screen._render_pending()
        client = self._ssh_client_for_screen(screen)
        lines: list[str] = []

        def show() -> None:
            screen.op_status(lines)

        def work():
            client.connect()
            try:
                pending = list(staged)
                while pending:
                    item = pending.pop(0)
                    lines.append(f"● {item['label']}…")
                    show()
                    try:
                        result = self._apply_staged_item(client, item)
                    except WifiLinkDroppedError as exc:
                        lines[-1] = f"✓ {item['label']} — sessão caiu como esperado"
                        show()
                        for skipped in pending:
                            lines.append(f"○ {skipped['label']} (não executado)")
                            show()
                        return None, exc, True
                    lines[-1] = f"✓ {item['label']}" + (f" ({result})" if result else "")
                    show()
                state = client.inspect_totem()
                lines.append("● Reiniciando o Totem…")
                show()
                client.reboot()
                return state, None, False
            except Exception as exc:
                return None, exc, False
            finally:
                try:
                    client.close()
                except Exception:
                    pass

        state, error, link_dropped = await asyncio.to_thread(work)
        try:
            self.screen.query_one("#apply-staged", Button).loading = False
        except Exception:
            pass
        if link_dropped:
            self.notify(f"{error}", title="Wi-Fi desativado", severity="warning")
            return
        if error is not None:
            lines.append(f"✗ {error}")
            show()
            self.notify("Aplicação interrompida antes do reinício. Verifique os passos.", title="Falha SSH", severity="error")
            return
        screen.refresh_from(state)
        lines.append("✓ Totem reiniciando. Aguarde voltar à rede.")
        show()
        self.notify("Alterações aplicadas. O Totem está reiniciando.", title="Concluído")

    def _apply_staged_item(self, client: TotemSshClient, item: dict):
        kind, payload = item["kind"], item["payload"]
        if kind == "hostname":
            return client.set_hostname(payload)
        if kind == "timezone":
            return client.set_timezone(payload)
        if kind == "password":
            return client.change_password(payload)
        if kind == "nexus_url":
            return client.update_nexus_url(payload)
        if kind == "rotation":
            return client.set_rotation(payload)
        if kind == "wifi":
            return client.set_wifi_enabled(bool(payload))
        if kind == "wifi_config":
            ssid, psk = payload
            return client.configure_wifi(ssid, psk)
        raise SshClientError(f"Alteração desconhecida: {kind}.")

    async def _refresh_inventory_status(self) -> None:
        screen = self.screen
        client = self._ssh_client_for_screen(screen)
        try:
            await asyncio.to_thread(client.connect)
            state = await asyncio.to_thread(client.inspect_totem)
            screen.refresh_from(state)
            screen.op_status(["✓ Status atual lido do Totem."])
            self.notify("Status atualizado.", title="Totem")
        except Exception as exc:
            self.notify(f"Não foi possível atualizar: {exc}", title="Falha SSH", severity="error")
        finally:
            await asyncio.to_thread(client.close)

    async def _with_loading(self, wid: str, coro):
        """Show a spinner on an action button while its work runs."""
        try:
            button = self.screen.query_one(f"#{wid}", Button)
        except Exception:
            button = None
        if button is not None:
            button.loading = True
        try:
            return await coro
        finally:
            try:
                if button is not None:
                    self.screen.query_one(f"#{wid}", Button).loading = False
            except Exception:
                pass

    async def _ssh_read_logs(self) -> None:
        screen = self.screen
        source = str(screen.query_one("#log-source", Select).value)
        client = self._ssh_client_for_screen(screen)
        try:
            await asyncio.to_thread(client.connect)
            text = await asyncio.to_thread(client.read_logs, source)
            screen.query_one("#log-view", TextArea).load_text(text or "(log vazio)")
        except Exception as exc:
            self.notify(f"Não foi possível ler o log: {exc}", title="Falha SSH", severity="error")
        finally:
            await asyncio.to_thread(client.close)

    async def _ssh_upload(self) -> None:
        screen = self.screen
        target = str(screen.query_one("#upload-target", Select).value)
        path = screen.query_one("#upload-path", Input).value.strip()
        if not path:
            self.notify("Informe o caminho local do arquivo.", severity="error")
            return
        client = self._ssh_client_for_screen(screen)
        try:
            await asyncio.to_thread(client.connect)
            dest = await asyncio.to_thread(client.upload_file, path, target)
            self.notify(f"Arquivo enviado para {dest}", title="Alteração aplicada")
        except Exception as exc:
            self.notify(f"Não foi possível enviar: {exc}", title="Falha SSH", severity="error")
        finally:
            await asyncio.to_thread(client.close)

    async def _restart_remote_graphics(self) -> None:
        screen = self.screen
        client = TotemSshClient(screen.host, screen.user, password=screen.password, key_filename=screen.key_file, timeout=8)
        try:
            client.connect()
            client.restart_graphics()
            self.notify("Solicitação de reinício enviada ao Totem.", title="Serviço remoto")
        except Exception as exc:
            self.notify(f"Não foi possível reiniciar o ambiente: {exc}", title="Falha SSH", severity="error")
        finally:
            client.close()

    def action_back(self) -> None:
        if isinstance(self.screen, ProgressFlashScreen) and not self.screen.operation_finished:
            self.notify("Aguarde o Imager terminar a gravação antes de sair desta etapa.", severity="warning")
            return
        if len(self.screen_stack) > 1:
            self.pop_screen()

    def action_quit(self) -> None:
        if isinstance(self.screen, ProgressFlashScreen) and not self.screen.operation_finished:
            self.notify("Aguarde o Imager terminar a gravação antes de sair da CLI.", severity="warning")
            return
        self.exit()


def main() -> None:
    TotemApp().run()


if __name__ == "__main__":
    main()
