"""SSH remote administration and inventory client for active Totems."""
from __future__ import annotations

import re
import shlex
import time
import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Any

try:
    import paramiko
except ImportError:
    paramiko = None

_MissingHostKeyPolicy = paramiko.MissingHostKeyPolicy if paramiko is not None else object


@dataclass
class RemoteTotemState:
    hostname: str
    autologin_user: str
    nexus_url: str
    app_host: str
    app_port: str
    rotation: str
    lightdm_active: bool
    chromium_running: bool
    monitor_service_active: bool
    timezone: str = "desconhecido"
    wifi_ssid: str = "—"
    wifi_enabled: bool = False
    wifi_managed: bool = True
    provisioned: bool = False


WIFI_OFF_FLAG = ".config/nexus-wifi-disabled"
WIFI_GUARD_SNIPPET = 'if os.path.exists(HOME + "/.config/nexus-wifi-disabled"):'


VALID_ROTATIONS = {"left", "right", "normal", "inverted"}

# Arquivos que a CLI pode enviar ao Totem: nome lógico -> (destino na home, modo).
UPLOAD_TARGETS = {
    "offline.html": ("offline.html", 0o644),
    "splash": ("Nexus_Boot_Splashscreen.mp4", 0o644),
}

# Logs legíveis remotamente: nome lógico -> comando que só lê e limita linhas.
LOG_SOURCES = {
    "firstboot": "/var/log/totem-firstboot.log",
    "monitor": "monitor.log",
    "chromium": "chromium.log",
    "lightdm": None,  # via journalctl, tratado à parte
}

_HOSTNAME_RE = re.compile(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?")
_TIMEZONE_RE = re.compile(r"[A-Za-z0-9_./+-]+")


def validate_hostname(value: str) -> str:
    name = (value or "").strip()
    if not _HOSTNAME_RE.fullmatch(name or ""):
        raise SshClientError("Hostname inválido: use letras, números e hífens (máx. 63).")
    return name


def validate_timezone(value: str) -> str:
    zone = (value or "").strip()
    if not _TIMEZONE_RE.fullmatch(zone or "") or "/" not in zone:
        raise SshClientError("Fuso horário inválido (ex.: America/Fortaleza).")
    return zone


def validate_wifi_credentials(ssid: str, password: str) -> tuple[str, str]:
    ssid = (ssid or "").strip()
    if not ssid or any(ch in ssid for ch in "\r\n\x00") or len(ssid.encode()) > 32:
        raise SshClientError("SSID inválido.")
    encoded = (password or "").encode()
    valid = 8 <= len(encoded) <= 63 or (
        len(encoded) == 64 and re.fullmatch(rb"[0-9a-fA-F]{64}", encoded)
    )
    if any(ch in (password or "") for ch in "\r\n\x00") or not valid:
        raise SshClientError("Senha Wi-Fi inválida (8–63 caracteres).")
    return ssid, password or ""


class SshClientError(Exception):
    """Raised when an error occurs during SSH interaction."""


class UnknownHostKeyError(SshClientError):
    def __init__(self, host: str, key_type: str, fingerprint: str) -> None:
        self.host = host
        self.key_type = key_type
        self.fingerprint = fingerprint
        super().__init__(f"Host SSH ainda não confiável: {host} ({key_type}, {fingerprint})")


class HostKeyMismatchError(SshClientError):
    pass


class HostKeyChangedError(SshClientError):
    """Known host key no longer matches the server (e.g. after a new firstboot)."""

    def __init__(
        self,
        host: str,
        key_type: str,
        fingerprint: str,
        expected_fingerprint: Optional[str] = None,
    ) -> None:
        self.host = host
        self.key_type = key_type
        self.fingerprint = fingerprint
        self.expected_fingerprint = expected_fingerprint
        super().__init__(
            f"Chave SSH de {host} mudou (provável novo firstboot). "
            f"Nova impressão digital: {fingerprint}"
            + (f" (antes: {expected_fingerprint})" if expected_fingerprint else "")
        )


class WifiLinkDroppedError(SshClientError):
    """Radio turned off while this SSH session itself used Wi-Fi."""

    def __init__(self) -> None:
        super().__init__(
            "Wi-Fi desativado no Totem, mas esta sessão SSH caiu junto "
            "(você estava conectado pelo Wi-Fi). Confira pelo cabo Ethernet."
        )


def remove_known_host_key(host: str, known_hosts: Optional[Path] = None) -> bool:
    """Remove a stale known_hosts entry for *host* (equivalent to ssh-keygen -R).

    Uses paramiko's HostKeys so hashed (HMAC) entries are removed too, not
    just plain-text lines. Returns True when an entry was removed.
    """
    if paramiko is None:
        return False
    path = known_hosts or (Path.home() / ".ssh" / "known_hosts")
    try:
        if not path.exists():
            return False
        keys = paramiko.HostKeys()
        keys.load(str(path))
        if keys.lookup(host) is None:
            return False
        del keys[host]
        keys.save(str(path))
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return True
    except Exception:
        return False


def _fingerprint(key: Any) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


class _AskHostKeyPolicy(_MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key) -> None:
        raise UnknownHostKeyError(hostname, key.get_name(), _fingerprint(key))


class _ExpectedHostKeyPolicy(_MissingHostKeyPolicy):
    def __init__(self, client, expected_fingerprint: str) -> None:
        self.client = client
        self.expected_fingerprint = expected_fingerprint

    def missing_host_key(self, client, hostname, key) -> None:
        actual = _fingerprint(key)
        if actual != self.expected_fingerprint:
            raise HostKeyMismatchError(
                f"A chave de {hostname} mudou durante a confirmação. Esperada {self.expected_fingerprint}, recebida {actual}."
            )
        client.get_host_keys().add(hostname, key.get_name(), key)


class TotemSshClient:
    def __init__(
        self,
        host: str,
        user: str,
        password: Optional[str] = None,
        key_filename: Optional[str] = None,
        port: int = 22,
        timeout: int = 10,
        expected_host_fingerprint: Optional[str] = None,
    ) -> None:
        self.host = host
        self.user = user
        self.password = password
        self.key_filename = key_filename
        self.port = port
        self.timeout = timeout
        self.expected_host_fingerprint = expected_host_fingerprint
        self._client: Optional[paramiko.SSHClient] = None
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}\$?", user):
            raise SshClientError("Nome de usuário SSH inválido.")

    def connect(self) -> None:
        """Establish SSH connection to the remote Totem."""
        if paramiko is None:
            raise SshClientError(
                "O pacote 'paramiko' não está instalado. "
                "Execute 'pip install paramiko' para habilitar recursos de gerenciamento SSH."
            )
        client = None
        try:
            client = paramiko.SSHClient()
            client.load_system_host_keys()
            known_hosts = Path.home() / ".ssh" / "known_hosts"
            if known_hosts.exists():
                client.load_host_keys(str(known_hosts))
            if self.expected_host_fingerprint:
                client.set_missing_host_key_policy(_ExpectedHostKeyPolicy(client, self.expected_host_fingerprint))
            else:
                client.set_missing_host_key_policy(_AskHostKeyPolicy())
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.user,
                password=self.password,
                key_filename=self.key_filename,
                timeout=self.timeout,
            )
            if self.expected_host_fingerprint:
                known_hosts.parent.mkdir(parents=True, exist_ok=True)
                client.save_host_keys(str(known_hosts))
                try:
                    known_hosts.chmod(0o600)
                except OSError:
                    pass
            self._client = client
        except Exception as e:
            if client:
                try:
                    client.close()
                except Exception:
                    pass
            if self._client:
                self._client.close()
                self._client = None
            if isinstance(e, SshClientError):
                raise
            # BadHostKeyException = known key does not match the server
            # (regenerated by a new firstboot). Surface it as a recoverable
            # changed-key error with the new fingerprint. Do NOT fall
            # through to the generic SshClientError — otherwise the UI
            # cannot offer to update known_hosts.
            if paramiko is not None:
                try:
                    from paramiko.ssh_exception import BadHostKeyException

                    if isinstance(e, BadHostKeyException):
                        new_key = getattr(e, "key", None)
                        expected_key = getattr(e, "expected_key", None)
                        if new_key is not None:
                            raise HostKeyChangedError(
                                self.host,
                                new_key.get_name(),
                                _fingerprint(new_key),
                                _fingerprint(expected_key) if expected_key is not None else None,
                            ) from e
                        raise HostKeyChangedError(self.host, "ssh-rsa", "desconhecida") from e
                except SshClientError:
                    raise
                except Exception:
                    pass
            raise SshClientError(f"Falha ao conectar via SSH a {self.user}@{self.host}: {e}") from e

    def close(self) -> None:
        """Close the active SSH connection."""
        if self._client:
            self._client.close()
            self._client = None

    def run_cmd(self, command: str, sudo: bool = False, extra_stdin: str = "") -> Tuple[int, str, str]:
        """Execute a command remotely and return (exit_code, stdout, stderr)."""
        if not self._client:
            raise SshClientError("SSH client não está conectado.")
        try:
            final_cmd = f"sudo -S -p '' {command}" if sudo else command
            stdin, stdout, stderr = self._client.exec_command(final_cmd, timeout=self.timeout)
            if sudo and self.password:
                stdin.write(self.password + "\n")
                stdin.flush()
            if extra_stdin:
                stdin.write(extra_stdin)
                stdin.flush()
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            exit_code = stdout.channel.recv_exit_status()
            return exit_code, out, err
        except Exception as e:
            raise SshClientError(f"Erro ao executar comando remoto '{command}': {e}") from e

    def inspect_totem(self) -> RemoteTotemState:
        """Read and inspect the active configuration of the remote Totem."""
        # 1. Hostname
        code, out, _ = self.run_cmd("hostname")
        hostname = out.strip() if code == 0 else "desconhecido"

        # 2. Autologin User from LightDM
        code, out, _ = self.run_cmd("grep -E '^autologin-user=' /etc/lightdm/lightdm.conf", sudo=True)
        autologin_user = "não configurado"
        if code == 0:
            m = re.search(r"autologin-user=([^\s]+)", out)
            if m:
                autologin_user = m.group(1)

        # 3. Nexus URL, APP_HOST and APP_PORT from monitor_network.py (v2.0.0)
        monitor_path = f"/home/{self.user}/monitor_network.py"
        code, out, _ = self.run_cmd(f"cat {monitor_path}")
        nexus_url = "não encontrada"
        app_host = "não encontrado"
        app_port = "não encontrado"
        if code == 0:
            m_url = re.search(r'ONLINE_URL\s*=\s*["\']([^"\']+)["\']', out)
            if m_url:
                nexus_url = m_url.group(1)
            m_host = re.search(r'APP_HOST\s*=\s*["\']([^"\']+)["\']', out)
            if m_host:
                app_host = m_host.group(1)
            m_port = re.search(r'APP_PORT\s*=\s*(\d+)', out)
            if m_port:
                app_port = m_port.group(1)

        # 4. Screen rotation from autostart
        code_auto, out_auto, _ = self.run_cmd("cat /etc/xdg/openbox/autostart", sudo=True)
        rotation = "normal"
        if code_auto == 0:
            rot_match = re.search(r"--rotate\s+([a-zA-Z]+)", out_auto)
            if rot_match:
                rotation = rot_match.group(1)

        # 5. LightDM service status
        code, _, _ = self.run_cmd("systemctl is-active lightdm")
        lightdm_active = code == 0

        # 6. Chromium process check
        code, _, _ = self.run_cmd("pgrep -a chromium")
        chromium_running = code == 0

        # 7. nexus-monitor.service status
        code, _, _ = self.run_cmd("systemctl is-active nexus-monitor")
        monitor_service_active = code == 0

        # 8. Timezone
        code, out, _ = self.run_cmd("timedatectl show -p Timezone --value")
        timezone = out.strip() if code == 0 and out.strip() else "desconhecido"

        # 9. First-boot provisioning marker
        code, _, _ = self.run_cmd("test -f /etc/totem-provisioned")
        provisioned = code == 0

        # 10. Wi-Fi radio state and active connection
        code, out, _ = self.run_cmd("nmcli radio wifi")
        wifi_enabled = code == 0 and out.strip().lower() == "enabled"
        wifi_ssid = "—"
        code, out, _ = self.run_cmd("nmcli -t -f NAME,DEVICE connection show --active")
        if code == 0:
            for line in out.splitlines():
                name, _, device = line.partition(":")
                if device.strip().startswith("wl"):
                    wifi_ssid = name.strip() or wifi_ssid
                    break

        # 11. Operator opt-out: when present, the Nexus monitor skips Wi-Fi repair.
        code, _, _ = self.run_cmd(f"test -f /home/{self.user}/{WIFI_OFF_FLAG}")
        wifi_managed = code != 0

        return RemoteTotemState(
            hostname=hostname,
            autologin_user=autologin_user,
            nexus_url=nexus_url,
            app_host=app_host,
            app_port=app_port,
            rotation=rotation,
            lightdm_active=lightdm_active,
            chromium_running=chromium_running,
            monitor_service_active=monitor_service_active,
            timezone=timezone,
            wifi_ssid=wifi_ssid,
            wifi_enabled=wifi_enabled,
            wifi_managed=wifi_managed,
            provisioned=provisioned,
        )

    def _sftp_write(self, remote_temp: str, content: str) -> None:
        """Write content to a temp file on the Totem via SFTP."""
        if not self._client:
            raise SshClientError("SSH client não conectado.")
        sftp = self._client.open_sftp()
        try:
            with sftp.file(remote_temp, "w") as remote_file:
                remote_file.write(content)
        finally:
            sftp.close()

    def _backup_remote(self, path: str) -> str:
        quoted = shlex.quote(path)
        ts = int(time.time())
        backup = f"{path}.bak-{ts}"
        code, _, err = self.run_cmd(f"cp -- {quoted} {shlex.quote(backup)}", sudo=True)
        if code:
            raise SshClientError(f"Não foi possível criar backup: {err.strip()}")
        return backup

    def _read_remote(self, path: str, what: str) -> str:
        code, content, err = self.run_cmd(f"cat -- {shlex.quote(path)}")
        if code != 0:
            raise SshClientError(f"Não foi possível ler {what}: {err.strip()}")
        return content

    def _install_remote(self, temp_file: str, dest: str, mode: int = 0o644) -> None:
        code, _, err = self.run_cmd(
            f"install -o {shlex.quote(self.user)} -g {shlex.quote(self.user)} "
            f"-m {mode:o} {shlex.quote(temp_file)} {shlex.quote(dest)} "
            f"&& rm -f {shlex.quote(temp_file)}",
            sudo=True,
        )
        if code:
            raise SshClientError(f"Falha ao aplicar arquivo atualizado: {err.strip()}")

    def set_hostname(self, new_hostname: str) -> None:
        """Change the Totem hostname and keep /etc/hosts consistent."""
        name = validate_hostname(new_hostname)
        quoted = shlex.quote(name)
        code, _, err = self.run_cmd(f"hostnamectl set-hostname {quoted}", sudo=True)
        if code:
            raise SshClientError(f"Falha ao trocar hostname: {err.strip()}")
        fix_hosts = (
            "if grep -qE '^127\\.0\\.1\\.1[[:space:]]' /etc/hosts; then "
            f"sed -i 's/^127\\.0\\.1\\.1[[:space:]].*/127.0.1.1\\t{name}/' /etc/hosts; "
            f"else printf '127.0.1.1\\t{name}\\n' >> /etc/hosts; fi"
        )
        code, _, err = self.run_cmd(fix_hosts, sudo=True)
        if code:
            raise SshClientError(f"Hostname aplicado, mas /etc/hosts falhou: {err.strip()}")

    def set_timezone(self, timezone: str) -> None:
        """Change the Totem timezone."""
        zone = validate_timezone(timezone)
        code, _, err = self.run_cmd(f"timedatectl set-timezone {shlex.quote(zone)}", sudo=True)
        if code:
            raise SshClientError(f"Falha ao trocar fuso horário: {err.strip()}")

    def set_rotation(self, rotation: str) -> str:
        """Rewrite the xrandr rotation in the Openbox autostart with backup."""
        if rotation not in VALID_ROTATIONS:
            raise SshClientError(f"Rotação inválida: {rotation}.")
        autostart = "/etc/xdg/openbox/autostart"
        backup = self._backup_remote(autostart)
        content = self._read_remote(autostart, "o autostart do Openbox")
        updated, count = re.subn(
            r"(--rotate\s+)[a-zA-Z]+",
            lambda m: m.group(1) + rotation,
            content,
            count=1,
        )
        if not count:
            raise SshClientError("Nenhuma diretiva --rotate encontrada no autostart.")
        temp_file = f"/tmp/totem_autostart_{int(time.time())}"
        self._sftp_write(temp_file, updated)
        self._install_remote(temp_file, autostart)
        return backup

    def _wifi_flag(self, create: bool) -> None:
        flag = f"/home/{self.user}/{WIFI_OFF_FLAG}"
        if create:
            code, _, err = self.run_cmd(f"touch -- {shlex.quote(flag)}")
            if code:
                raise SshClientError(f"Falha ao travar o reparo Wi-Fi: {err.strip()}")
        else:
            self.run_cmd(f"rm -f -- {shlex.quote(flag)}")

    def _wifi_autoconnect(self, enabled: bool) -> None:
        state = "yes" if enabled else "no"
        code, _, _ = self.run_cmd("nmcli connection show totem-wifi")
        if code == 0:
            code, _, err = self.run_cmd(
                f"nmcli connection modify totem-wifi connection.autoconnect {state}",
                sudo=True,
            )
            if code:
                raise SshClientError(f"Falha ao ajustar autoconnect: {err.strip()}")

    def ensure_monitor_wifi_guard(self) -> str:
        """Patch a running monitor to honor the Wi-Fi opt-out flag.

        Totems provisioned before the guard existed would otherwise repair
        (re-enable) a Wi-Fi the operator turned off. Returns 'pronto'
        when the guard is present, or the backup path when patched.
        """
        monitor_path = f"/home/{self.user}/monitor_network.py"
        content = self._read_remote(monitor_path, "o monitor do Nexus")
        if WIFI_GUARD_SNIPPET in content:
            return "pronto"
        anchor = '"""Escada de reparo Wi-Fi (requer sudoers do monitor). Retorna descricao."""'
        if anchor not in content:
            raise SshClientError("Monitor incompatível: função wifi_repair não encontrada.")
        updated = content.replace(
            anchor,
            anchor + "\n"
            "    if os.path.exists(HOME + \"/.config/nexus-wifi-disabled\"):\n"
            '        return "disabled-by-operator"',
            1,
        )
        backup = self._backup_remote(monitor_path)
        temp_file = f"/tmp/monitor_network_{int(time.time())}.py"
        self._sftp_write(temp_file, updated)
        self._install_remote(temp_file, monitor_path, 0o755)
        return backup

    def set_wifi_enabled(self, enabled: bool) -> None:
        """Turn the Wi-Fi radio on or off via NetworkManager.

        Turning off also disables autoconnect and locks the Nexus monitor
        repair, otherwise the monitor would reconnect on its own.
        """
        if enabled:
            self._wifi_flag(create=False)
            self._wifi_autoconnect(True)
            code, _, err = self.run_cmd("nmcli radio wifi on", sudo=True)
            if code:
                raise SshClientError(f"Falha ao ativar o Wi-Fi: {err.strip()}")
            return
        # Lock everything that survives a reboot BEFORE cutting the radio:
        # autoconnect off, monitor repair locked, old monitor patched.
        self._wifi_autoconnect(False)
        self._wifi_flag(create=True)
        self.ensure_monitor_wifi_guard()
        try:
            code, _, err = self.run_cmd("nmcli radio wifi off", sudo=True)
        except SshClientError as exc:
            if "Erro ao executar comando remoto" in str(exc):
                raise WifiLinkDroppedError() from exc
            raise
        if code:
            raise SshClientError(f"Falha ao desativar o Wi-Fi: {err.strip()}")
        try:
            self.run_cmd("echo ok")
        except SshClientError as exc:
            if "Erro ao executar comando remoto" in str(exc):
                raise WifiLinkDroppedError() from exc
            raise

    def configure_wifi(self, ssid: str, password: str) -> None:
        """Create or update the totem-wifi NetworkManager connection."""
        ssid, password = validate_wifi_credentials(ssid, password)
        self._wifi_flag(create=False)
        q_ssid, q_psk = shlex.quote(ssid), shlex.quote(password)
        code, _, _ = self.run_cmd("nmcli connection show totem-wifi")
        if code == 0:
            cmd = (
                "nmcli connection modify totem-wifi "
                f"802-11-wireless.ssid {q_ssid} wifi-sec.key-mgmt wpa-psk wifi-sec.psk {q_psk}"
            )
        else:
            cmd = (
                "nmcli connection add type wifi ifname wlan0 con-name totem-wifi "
                f"802-11-wireless.ssid {q_ssid} wifi-sec.key-mgmt wpa-psk wifi-sec.psk {q_psk} "
                "connection.autoconnect yes"
            )
        code, _, err = self.run_cmd(cmd, sudo=True)
        if code:
            raise SshClientError(f"Falha ao configurar o Wi-Fi: {err.strip()}")
        code, _, err = self.run_cmd("nmcli connection up totem-wifi", sudo=True)
        if code:
            raise SshClientError(f"Wi-Fi configurado, mas a conexão falhou: {err.strip()}")

    def change_password(self, new_password: str) -> None:
        """Change the Linux password of the connected user.

        On success the in-memory credential is updated so subsequent sudo
        commands and reboots keep working in the same session. A new SSH
        connection must use the new password.
        """
        if not new_password or any(ch in new_password for ch in "\r\n\x00"):
            raise SshClientError("Defina uma senha válida (sem quebras de linha).")
        code, _, err = self.run_cmd(
            "chpasswd",
            sudo=True,
            extra_stdin=f"{self.user}:{new_password}\n",
        )
        if code:
            raise SshClientError(f"Falha ao trocar a senha: {err.strip()}")
        self.password = new_password

    def read_logs(self, source: str, lines: int = 60) -> str:
        """Return the tail of a Totem log. Only allowlisted sources, capped."""
        if source not in LOG_SOURCES:
            raise SshClientError(f"Log desconhecido: {source}.")
        try:
            count = int(lines)
        except (TypeError, ValueError):
            raise SshClientError("Número de linhas inválido.") from None
        count = max(10, min(count, 300))
        if source == "lightdm":
            cmd: str = f"journalctl -u lightdm --no-pager -n {count}"
        elif source == "firstboot":
            cmd = f"tail -n {count} -- {shlex.quote(LOG_SOURCES['firstboot'] or '')}"
        else:
            cmd = f"tail -n {count} -- /home/{self.user}/{LOG_SOURCES[source]}"
        code, out, err = self.run_cmd(cmd, sudo=(source in {"firstboot", "lightdm"}))
        if code != 0:
            raise SshClientError(f"Não foi possível ler o log: {(err or out).strip()[-500:]}")
        return out.strip() or "(log vazio)"

    def upload_file(self, local_path: str, target: str) -> str:
        """Send a file to the Totem home dir. Only allowlisted targets/sizes."""
        if target not in UPLOAD_TARGETS:
            raise SshClientError(f"Destino não permitido: {target}.")
        from pathlib import Path as _Path

        src = _Path(local_path).expanduser()
        if not src.is_file():
            raise SshClientError("Arquivo local não encontrado.")
        filename, mode = UPLOAD_TARGETS[target]
        if src.suffix.lower() != _Path(filename).suffix.lower():
            raise SshClientError(f"O arquivo deve ter a extensão {_Path(filename).suffix}.")
        if src.stat().st_size > 50 * 1024 * 1024:
            raise SshClientError("Arquivo excede 50 MB.")
        dest = f"/home/{self.user}/{filename}"
        temp_file = f"/tmp/totem_upload_{int(time.time())}_{filename}"
        if not self._client:
            raise SshClientError("SSH client não conectado.")
        sftp = self._client.open_sftp()
        try:
            sftp.put(str(src), temp_file)
        finally:
            sftp.close()
        self._install_remote(temp_file, dest, mode)
        return dest

    def update_nexus_url(self, new_url: str) -> str:
        """Atomically update ONLINE_URL, APP_HOST and APP_PORT in monitor_network.py with timestamped backup."""
        from urllib.parse import urlparse
        parsed = urlparse(new_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or any(ch in new_url for ch in "\r\n\x00"):
            raise SshClientError("Informe uma URL Nexus HTTP ou HTTPS válida.")
        new_host = parsed.hostname
        new_port = parsed.port or (443 if parsed.scheme == "https" else 80)

        monitor_path = f"/home/{self.user}/monitor_network.py"
        ts = int(time.time())
        backup_path = f"{monitor_path}.bak-{ts}"

        # 1. Create backup if exists
        quoted_path = shlex.quote(monitor_path)
        quoted_backup = shlex.quote(backup_path)
        code, _, err = self.run_cmd(f"cp -- {quoted_path} {quoted_backup}", sudo=True)
        if code:
            raise SshClientError(f"Não foi possível criar backup: {err.strip()}")

        # 2. Read existing content
        code, content, err = self.run_cmd(f"cat -- {quoted_path}")
        if code != 0:
            raise SshClientError(f"Não foi possível ler a configuração Nexus: {err.strip()}")
        if "ONLINE_URL" in content:
            # Replace ONLINE_URL
            updated_content, count_url = re.subn(
                r'ONLINE_URL\s*=\s*["\'][^"\']+["\']',
                lambda _: "ONLINE_URL = " + repr(new_url),
                content,
                count=1,
            )
            # Replace APP_HOST
            updated_content, count_host = re.subn(
                r'APP_HOST\s*=\s*["\'][^"\']+["\']',
                lambda _: "APP_HOST = " + repr(new_host),
                updated_content,
                count=1,
            )
            # Replace APP_PORT
            updated_content, count_port = re.subn(
                r'APP_PORT\s*=\s*\d+',
                f'APP_PORT = {new_port}',
                updated_content,
                count=1,
            )
            if not (count_url and count_host and count_port):
                raise SshClientError("Arquivo encontrado, mas não contém todas as configurações Nexus esperadas.")
            temp_file = f"/tmp/monitor_network_{ts}.py"
            if not self._client:
                raise SshClientError("SSH client não conectado.")
            sftp = self._client.open_sftp()
            try:
                with sftp.file(temp_file, "w") as remote_file:
                    remote_file.write(updated_content)
            finally:
                sftp.close()
            code, _, err = self.run_cmd(
                f"install -o {shlex.quote(self.user)} -g {shlex.quote(self.user)} -m 755 "
                f"{shlex.quote(temp_file)} {quoted_path} && rm -f {shlex.quote(temp_file)}",
                sudo=True,
            )
            if code:
                raise SshClientError(f"Falha ao aplicar arquivo atualizado: {err.strip()}")
        else:
            raise SshClientError("A configuração Nexus não foi encontrada no arquivo remoto.")

        return backup_path

    def restart_graphics(self) -> None:
        """Restart LightDM; Openbox autostart owns the kiosk process on Totem images."""
        command = "systemctl restart lightdm"
        code, _, err = self.run_cmd(command, sudo=True)
        if code:
            raise SshClientError(f"Falha ao executar '{command}': {err.strip()}")

    def reboot(self) -> None:
        """Reboot the Totem. The SSH session will drop; hostname changes apply."""
        code, _, err = self.run_cmd("reboot", sudo=True)
        if code:
            raise SshClientError(f"Falha ao reiniciar: {err.strip()}")
