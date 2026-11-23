"""Boot partition injector for offline first-boot Totem provisioning."""
from __future__ import annotations

import shutil
import base64
import io
import json
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
import jinja2
from passlib.hash import sha512_crypt

from totem_cli.resources import package_dir

TEMPLATES_DIR = package_dir() / "templates"
ASSETS_DIR = package_dir() / "assets"


@dataclass
class TotemConfig:
    hostname: str
    username: str
    password: str
    nexus_url: str
    timezone: str = "America/Fortaleza"
    screen_rotation: str = "left"  # left, right, inverted, normal
    display_output: str = "HDMI-1"
    wifi_ssid: Optional[str] = None
    wifi_password: Optional[str] = None


def generate_sha512_crypt(password: str) -> str:
    """Generate SHA-512 crypt hash ($6$salt$...) compatible with Linux shadow/userconf."""
    return sha512_crypt.using(rounds=5000).hash(password)


def validate_config(config: TotemConfig) -> None:
    """Reject values that would create invalid names or unsafe boot scripts."""
    if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", config.hostname):
        raise ValueError("Hostname inválido: use letras, números e hífens (máximo 63 caracteres).")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,30}", config.username):
        raise ValueError("Usuário Linux inválido.")
    if not config.password or any(ch in config.password for ch in "\r\n\x00"):
        raise ValueError("Defina uma senha Linux válida.")
    if len(config.password.encode("utf-8")) > 512:
        raise ValueError("A senha Linux excede o limite suportado.")
    parsed = urlparse(config.nexus_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or any(ord(ch) < 32 or ch.isspace() for ch in config.nexus_url)
    ):
        raise ValueError("A URL do Nexus precisa começar com http:// ou https:// e conter um host.")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("A URL do Nexus contém uma porta inválida.") from exc
    if config.screen_rotation not in {"left", "right", "inverted", "normal"}:
        raise ValueError("Rotação de tela inválida.")
    if not re.fullmatch(r"HDMI-[A-Za-z0-9-]+", config.display_output):
        raise ValueError("Saída de vídeo inválida.")
    if not re.fullmatch(r"[A-Za-z0-9_./+-]+", config.timezone):
        raise ValueError("Fuso horário inválido.")
    if bool(config.wifi_ssid) != bool(config.wifi_password):
        raise ValueError("Informe o SSID e a senha do Wi-Fi juntos.")
    if config.wifi_ssid and any(ch in config.wifi_ssid for ch in "\r\n\x00"):
        raise ValueError("O nome da rede Wi-Fi não pode conter quebras de linha.")
    if config.wifi_password and any(ch in config.wifi_password for ch in "\r\n\x00"):
        raise ValueError("A senha do Wi-Fi não pode conter quebras de linha.")
    if config.wifi_ssid and len(config.wifi_ssid.encode("utf-8")) > 32:
        raise ValueError("O nome da rede Wi-Fi excede 32 bytes.")
    if config.wifi_password:
        encoded_password = config.wifi_password.encode("utf-8")
        valid_wpa = 8 <= len(encoded_password) <= 63 or (
            len(encoded_password) == 64 and re.fullmatch(rb"[0-9a-fA-F]{64}", encoded_password)
        )
        if not valid_wpa:
            raise ValueError("A senha Wi-Fi WPA precisa ter de 8 a 63 caracteres ou 64 dígitos hexadecimais.")


def parse_nexus_url(nexus_url: str) -> tuple[str, int]:
    """Parse Nexus URL and return (app_host, app_port).

    Defaults to port 443 when no port is specified (HTTPS) or 80 for HTTP.
    """
    parsed = urlparse(nexus_url)
    app_host = parsed.hostname or nexus_url
    if parsed.port:
        app_port = parsed.port
    else:
        app_port = 443 if parsed.scheme == "https" else 80
    return app_host, app_port


def render_template(template_name: str, **context) -> str:
    """Render a Jinja2 template from the templates directory."""
    loader = jinja2.FileSystemLoader(str(TEMPLATES_DIR))
    env = jinja2.Environment(loader=loader, autoescape=False)
    template = env.get_template(template_name)
    return template.render(**context)


def build_first_run_script(config: TotemConfig) -> str:
    """Build the Bookworm first-run script supported by Raspberry Pi Imager."""
    validate_config(config)
    app_host, app_port = parse_nexus_url(config.nexus_url)
    files: dict[str, bytes] = {
        "totem_autostart": render_template(
            "autostart.j2", username=config.username, nexus_url=config.nexus_url,
            screen_rotation=config.screen_rotation, display_output=config.display_output,
        ).encode(),
        "monitor_network.py": render_template(
            "monitor_network.py.j2", username=config.username,
            nexus_url_json=json.dumps(config.nexus_url), app_host_json=json.dumps(app_host), app_port=app_port,
        ).encode(),
        "offline.html": render_template("offline.html.j2").encode(),
        "totem_sudoers": render_template("sudoers_totem.j2", username=config.username).encode(),
        "xbindkeysrc": render_template("xbindkeysrc.j2").encode(),
        "nexus_manifest.json": (TEMPLATES_DIR / "manifest.json").read_bytes(),
        "nexus_content.js": (TEMPLATES_DIR / "content.js").read_bytes(),
        "config_rpi5.txt": render_template("config_rpi5.j2").encode(),
    }
    splash = ASSETS_DIR / "Nexus_Boot_Splashscreen.mp4"
    if splash.is_file():
        files["Nexus_Boot_Splashscreen.mp4"] = splash.read_bytes()

    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as payload:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o644
            payload.addfile(info, io.BytesIO(content))

    password_hash = generate_sha512_crypt(config.password)
    return render_template(
        "firstboot.sh.j2",
        username=config.username,
        hostname=config.hostname,
        timezone=config.timezone,
        password_hash=password_hash,
        wifi_ssid_b64=base64.b64encode((config.wifi_ssid or "").encode()).decode(),
        wifi_password_b64=base64.b64encode((config.wifi_password or "").encode()).decode(),
        payload_b64=base64.b64encode(archive.getvalue()).decode("ascii"),
    )


def inject_boot_files(boot_dir: str | Path, config: TotemConfig) -> list[str]:
    r"""Inject offline provisioning files into the mounted boot/firmware partition.

    Args:
        boot_dir: Path to the mounted FAT32 boot partition (e.g. /media/user/bootfs or D:\\).
        config: TotemConfig dataclass containing user and totem settings.

    Returns:
        List of file paths created or updated inside boot_dir.
    """
    boot_path = Path(boot_dir)
    if not boot_path.exists():
        raise FileNotFoundError(f"Diretório de boot não encontrado: {boot_dir}")
    validate_config(config)

    created_files: list[str] = []
    app_host, app_port = parse_nexus_url(config.nexus_url)

    # The first-run script receives a password hash and an embedded config payload.
    # 2. Append RPi5 KMS settings to config.txt
    config_txt_file = boot_path / "config.txt"
    rpi5_settings = render_template("config_rpi5.j2")
    if config_txt_file.exists():
        current_config = config_txt_file.read_text(encoding="utf-8", errors="ignore")
        if "dtoverlay=vc4-kms-v3d" not in current_config:
            with open(config_txt_file, "a", encoding="utf-8", newline="\n") as f:
                f.write("\n" + rpi5_settings)
    else:
        config_txt_file.write_text(rpi5_settings, encoding="utf-8", newline="\n")
    created_files.append(str(config_txt_file))

    # 3. totem_autostart (Openbox Kiosk script stored on boot partition for firstboot copy)
    autostart_content = render_template(
        "autostart.j2",
        username=config.username,
        nexus_url=config.nexus_url,
        screen_rotation=config.screen_rotation,
        display_output=config.display_output,
    )
    autostart_file = boot_path / "totem_autostart"
    autostart_file.write_text(autostart_content, encoding="utf-8", newline="\n")
    created_files.append(str(autostart_file))

    # 4. monitor_network.py (Full production v2.0.0 script with Nexus URL, host and port)
    monitor_content = render_template(
        "monitor_network.py.j2",
        username=config.username,
        nexus_url_json=json.dumps(config.nexus_url),
        app_host_json=json.dumps(app_host),
        app_port=app_port,
    )
    monitor_file = boot_path / "monitor_network.py"
    monitor_file.write_text(monitor_content, encoding="utf-8", newline="\n")
    created_files.append(str(monitor_file))

    # 5. offline.html (Nexus offline page — served by monitor_network.py local HTTP server)
    offline_content = render_template("offline.html.j2")
    offline_file = boot_path / "offline.html"
    offline_file.write_text(offline_content, encoding="utf-8", newline="\n")
    created_files.append(str(offline_file))

    # 7. sudoers entry for nmcli / reboot without password
    sudoers_content = render_template("sudoers_totem.j2", username=config.username)
    sudoers_file = boot_path / "totem_sudoers"
    sudoers_file.write_text(sudoers_content, encoding="utf-8", newline="\n")
    created_files.append(str(sudoers_file))

    # 8. xbindkeysrc (Block escape shortcut combinations)
    xbindkeys_content = render_template("xbindkeysrc.j2")
    xbindkeys_file = boot_path / "xbindkeysrc"
    xbindkeys_file.write_text(xbindkeys_content, encoding="utf-8", newline="\n")
    created_files.append(str(xbindkeys_file))

    # 9. Nexus Extension files
    manifest_src = TEMPLATES_DIR / "manifest.json"
    content_src = TEMPLATES_DIR / "content.js"
    if manifest_src.exists():
        shutil.copy(manifest_src, boot_path / "nexus_manifest.json")
        created_files.append(str(boot_path / "nexus_manifest.json"))
    if content_src.exists():
        shutil.copy(content_src, boot_path / "nexus_content.js")
        created_files.append(str(boot_path / "nexus_content.js"))

    # 10. Default Splashscreen video asset
    splash_src = ASSETS_DIR / "Nexus_Boot_Splashscreen.mp4"
    if splash_src.exists():
        shutil.copy(splash_src, boot_path / "Nexus_Boot_Splashscreen.mp4")
        created_files.append(str(boot_path / "Nexus_Boot_Splashscreen.mp4"))

    # 11. firstboot.sh
    firstboot_content = build_first_run_script(config)
    firstboot_file = boot_path / "firstboot.sh"
    firstboot_file.write_text(firstboot_content, encoding="utf-8", newline="\n")
    created_files.append(str(firstboot_file))

    # 12. Silent OEM boot flags + systemd.run in cmdline.txt
    cmdline_file = boot_path / "cmdline.txt"
    if cmdline_file.exists():
        tokens = cmdline_file.read_text(encoding="utf-8", errors="ignore").split()
        tokens = [token for token in tokens if not token.startswith("systemd.run=")]
        for flag in ("quiet", "splash", "loglevel=0", "logo.nologo", "vt.global_cursor_default=0"):
            if flag not in tokens:
                tokens.append(flag)
        tokens.append("systemd.run=/boot/firmware/firstboot.sh")
        cmdline_text = " ".join(tokens)
        cmdline_file.write_text(f"{cmdline_text}\n", encoding="utf-8", newline="\n")
        created_files.append(str(cmdline_file))

    return created_files
