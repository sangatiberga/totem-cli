"""Official Raspberry Pi Imager discovery and Bookworm image catalog access."""
from __future__ import annotations

import ctypes
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


OS_MANIFEST_URL = "https://downloads.raspberrypi.com/os_list_imagingutility_v4.json"
OFFICIAL_LINUX_DEB_URL = "https://downloads.raspberrypi.com/imager/imager_latest_amd64.deb"
RPI_IMAGER_RELEASES_API = "https://api.github.com/repos/raspberrypi/rpi-imager/releases/latest"
WINGET_PACKAGE_ID = "RaspberryPiFoundation.RaspberryPiImager"


class ImagerError(RuntimeError):
    pass


@dataclass(frozen=True)
class OfficialImage:
    name: str
    description: str
    url: str
    sha256: str
    size_bytes: int
    release_date: str


def _fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "TotemSetup/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read(8 * 1024 * 1024 + 1)
    if len(payload) > 8 * 1024 * 1024:
        raise ImagerError("O catálogo oficial excedeu o tamanho esperado.")
    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, dict):
        raise ImagerError("Formato inesperado no catálogo oficial do Raspberry Pi Imager.")
    return data


def _manifest_entries(
    url: str = OS_MANIFEST_URL,
    seen: Optional[set[str]] = None,
    inherited_devices: Optional[list[str]] = None,
) -> list[dict]:
    seen = seen or set()
    if url in seen or len(seen) >= 20:
        return []
    seen.add(url)
    data = _fetch_json(url)
    return _manifest_entries_from_data(data, url, seen, inherited_devices)


def _manifest_entries_from_data(
    data: dict,
    base_url: str,
    seen: set[str],
    inherited_devices: Optional[list[str]] = None,
) -> list[dict]:
    entries = data.get("os_list", [])
    if not isinstance(entries, list):
        return []
    result: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        own_devices = entry.get("devices", [])
        if isinstance(own_devices, str):
            own_devices = [own_devices]
        effective_devices = own_devices or inherited_devices or []
        if entry.get("url") and entry.get("extract_sha256"):
            result.append({**entry, "_effective_devices": effective_devices})
        subitems_url = entry.get("subitems_url")
        if subitems_url:
            absolute = urllib.parse.urljoin(base_url, str(subitems_url))
            result.extend(_manifest_entries(absolute, seen, effective_devices))
        # Current Imager manifests may embed category contents directly instead
        # of linking to a separate sublist file.
        inline = entry.get("subitems")
        inline_json = entry.get("subitems_json")
        if inline_json:
            try:
                inline = json.loads(inline_json) if isinstance(inline_json, str) else inline_json
            except (TypeError, json.JSONDecodeError):
                inline = None
        if isinstance(inline, list):
            result.extend(
                _manifest_entries_from_data({"os_list": inline}, base_url, seen, effective_devices)
            )
        elif isinstance(inline, dict):
            result.extend(_manifest_entries_from_data(inline, base_url, seen, effective_devices))
    return result


def resolve_bookworm_image(variant: str = "lite") -> OfficialImage:
    """Select a Pi 5 compatible Bookworm image from Raspberry Pi's live manifest."""
    if variant not in {"lite", "desktop"}:
        raise ValueError("A variante deve ser 'lite' ou 'desktop'.")
    entries = _manifest_entries()
    matches: list[dict] = []
    for entry in entries:
        name = str(entry.get("name", ""))
        description = str(entry.get("description", ""))
        image_url = str(entry.get("url", ""))
        searchable = f"{name} {description} {image_url}".lower()
        if "bookworm" not in searchable:
            continue
        devices = entry.get("_effective_devices", entry.get("devices", []))
        if isinstance(devices, str):
            devices = [devices]
        normalized_devices = {
            "".join(ch for ch in str(device).lower() if ch.isalnum()) for device in devices
        }
        pi5_supported = bool(normalized_devices & {"pi5", "rpi5", "raspberrypi5"})
        # Official 64-bit Raspberry Pi OS Bookworm images support Pi 5. This
        # fallback also covers catalog entries whose device metadata is stale
        # or inherited only by the parent category.
        official_pi_os_64bit = "raspberry pi os" in searchable and (
            "64-bit" in searchable or "arm64" in searchable
        )
        pi5_supported = pi5_supported or official_pi_os_64bit
        if not pi5_supported:
            continue
        is_lite = "lite" in searchable or "no desktop environment" in searchable
        if variant == "lite" and not is_lite:
            continue
        if variant == "desktop" and (is_lite or "full" in name.lower()):
            continue
        matches.append(entry)
    if not matches:
        bookworm_candidates = [
            f"{item.get('name', '(sem nome)')} [{', '.join(item.get('_effective_devices', []))}]"
            for item in entries
            if "bookworm" in f"{item.get('name', '')} {item.get('description', '')} {item.get('url', '')}".lower()
        ]
        detail = " Candidatos encontrados: " + "; ".join(bookworm_candidates[:6]) if bookworm_candidates else ""
        raise ImagerError(
            f"O catálogo oficial não apresentou uma imagem Bookworm {variant} compatível com Pi 5. "
            f"Nenhuma imagem foi gravada.{detail}"
        )
    item = max(matches, key=lambda value: str(value.get("release_date", "")))
    sha256 = str(item["extract_sha256"]).lower()
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise ImagerError("O catálogo retornou um SHA-256 inválido; nenhuma imagem foi gravada.")
    return OfficialImage(
        name=str(item.get("name", "Raspberry Pi OS Bookworm")),
        description=str(item.get("description", "")),
        url=str(item["url"]),
        sha256=sha256,
        size_bytes=int(item.get("extract_size") or 0),
        release_date=str(item.get("release_date", "")),
    )


def is_elevated() -> bool:
    if os.name == "nt":
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return hasattr(os, "geteuid") and os.geteuid() == 0


def check_device_writable(device: str) -> None:
    """Fail fast when the card refuses writes, before invoking the Imager.

    The Imager reports a raw write denial only as exit code 1 buried in its
    log. Probing the device first turns the common cases into a clear
    message: a card that answers "access denied" is usually behind the
    physical Lock switch of the SD adapter or a software read-only flag,
    while "sharing violation" means another program is holding the drive.
    Anything unexpected (or any probe failure) lets the flash proceed so the
    Imager itself remains the final reporter. Windows only.
    """
    if os.name != "nt" or not device:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateFileW.restype = ctypes.c_void_p
        handle = kernel32.CreateFileW(
            device,
            0x80000000 | 0x40000000,  # GENERIC_READ | GENERIC_WRITE
            0,  # no sharing: also detects drives held by other programs
            None,
            3,  # OPEN_EXISTING
            0,
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        if handle != invalid:
            kernel32.CloseHandle(handle)
            return
        error = kernel32.GetLastError()
    except Exception:
        return
    if error == 5:  # ERROR_ACCESS_DENIED
        raise ImagerError(
            "O cartão recusou a escrita (acesso negado). Verifique a trava "
            "física Lock do adaptador SD e a flag somente-leitura "
            "(diskpart > attributes disk). Nenhum dado foi escrito."
        )
    if error == 32:  # ERROR_SHARING_VIOLATION
        raise ImagerError(
            "Outro programa está usando o cartão. Feche o Explorer e o "
            "antivírus, reinsira o leitor e tente de novo. "
            "Nenhum dado foi escrito."
        )


def is_process_elevated() -> bool:
    """True only when the process token is actually elevated (UAC-aware).

    IsUserAnAdmin reports membership in Administrators, which stays true for
    a filtered (non-elevated) UAC token. Raw disk access needs real elevation,
    so query TokenElevation instead, falling back to IsUserAnAdmin only when
    the token cannot be inspected.
    """
    if os.name != "nt":
        return hasattr(os, "geteuid") and os.geteuid() == 0
    try:
        token = ctypes.c_void_p()
        proc = ctypes.windll.kernel32.GetCurrentProcess()
        if not ctypes.windll.advapi32.OpenProcessToken(proc, 0x0008, ctypes.byref(token)):
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        elevation = ctypes.c_ulong(0)
        size = ctypes.c_ulong(ctypes.sizeof(elevation))
        ok = ctypes.windll.advapi32.GetTokenInformation(
            token, 20, ctypes.byref(elevation), ctypes.sizeof(elevation), ctypes.byref(size)
        )
        ctypes.windll.kernel32.CloseHandle(token)
        if not ok:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return bool(elevation.value)
    except Exception:
        return False


def find_all_imagers() -> list[str]:
    """Every rpi-imager candidate on this machine, in lookup order."""
    found: list[str] = []
    for name in ("rpi-imager", "rpi-imager.exe", "Raspberry Pi Imager.exe"):
        path = shutil.which(name)
        if path and path not in found:
            found.append(path)
    if os.name == "nt":
        roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")]
        for root in roots:
            if root:
                for relative in (
                    Path("Raspberry Pi Imager") / "rpi-imager.exe",
                    Path("Raspberry Pi Ltd") / "Imager" / "rpi-imager.exe",
                    Path("Programs") / "Raspberry Pi Imager" / "rpi-imager.exe",
                ):
                    candidate = str(Path(root) / relative)
                    if candidate not in found and Path(candidate).is_file():
                        found.append(candidate)
    return found


def find_imager() -> Optional[str]:
    candidates = find_all_imagers()
    return candidates[0] if candidates else None


def _first_supported_imager() -> Optional[str]:
    """First installed Imager that supports --first-run-script, if any.

    An upgrade may land beside an old copy instead of replacing it, so every
    candidate is probed instead of trusting lookup order alone.
    """
    for candidate in find_all_imagers():
        try:
            _require_first_run_support(candidate)
            return candidate
        except ImagerError:
            continue
    return None


def ensure_imager(progress: Optional[Callable[[str], None]] = None) -> str:
    """Find or install Raspberry Pi Imager using the host's native package tool."""
    supported = _first_supported_imager()
    if supported:
        return supported
    system = platform.system().lower()
    outdated = bool(find_imager())
    if system == "windows":
        if not is_elevated():
            action = "atualizar" if outdated else "instalar"
            raise ImagerError(f"Abra a CLI em um terminal como Administrador para {action} o Raspberry Pi Imager.")
        if outdated:
            if progress:
                progress("A versão do Imager é antiga; atualizando pelo instalador oficial...")
            _install_official_windows_imager(progress)
        else:
            _install_windows_imager(progress)
    elif system == "linux" and shutil.which("apt-get"):
        if outdated:
            if progress:
                progress("A versão do Imager é antiga; baixando a versão oficial atualizada...")
            _install_official_linux_imager()
        else:
            command = ["apt-get", "update"]
            if progress:
                progress("Atualizando índice de pacotes para instalar Raspberry Pi Imager...")
            _run_install(command)
            command = ["apt-get", "install", "-y", "rpi-imager"]
            if progress:
                progress("Instalando Raspberry Pi Imager...")
            _run_install(command)
    else:
        raise ImagerError("Instalação automática disponível apenas via apt no Debian/Ubuntu ou winget no Windows.")
    supported = _first_supported_imager()
    if not supported:
        raise ImagerError(
            "A instalação/atualização terminou, mas nenhum executável rpi-imager compatível foi localizado. "
            "Instale manualmente pelo site https://www.raspberrypi.com/software/ e tente de novo."
        )
    return supported


def _install_windows_imager(progress: Optional[Callable[[str], None]] = None) -> None:
    """Install on Windows via winget, falling back to the official installer.

    Corporate machines often block the winget/msstore source ("Failed when
    searching source"), so a winget failure downloads the official Inno Setup
    installer from the rpi-imager GitHub release and installs it silently.
    """
    winget = shutil.which("winget")
    if winget:
        try:
            if progress:
                progress("Instalando Raspberry Pi Imager via winget...")
            _run_install([winget, "install", "--exact", "--id", WINGET_PACKAGE_ID,
                          "--silent", "--accept-package-agreements", "--accept-source-agreements"])
            return
        except ImagerError as exc:
            if progress:
                progress(f"O winget falhou ({exc}); tentando o instalador oficial...")
    _install_official_windows_imager(progress)


def _install_official_windows_imager(progress: Optional[Callable[[str], None]] = None) -> None:
    """Download the official rpi-imager Inno Setup installer and run it silently."""
    try:
        data = _fetch_json(RPI_IMAGER_RELEASES_API)
    except ImagerError as exc:
        raise ImagerError(
            "Não foi possível consultar as releases oficiais do Raspberry Pi Imager. "
            "Instale manualmente pelo site https://www.raspberrypi.com/software/ e tente de novo."
        ) from exc
    assets = data.get("assets", []) if isinstance(data, dict) else []
    installer_asset = next((
        asset for asset in assets
        if isinstance(asset, dict)
        and str(asset.get("name", "")).lower().endswith(".exe")
        and asset.get("browser_download_url")
    ), None)
    if installer_asset is None:
        raise ImagerError(
            "A release oficial não traz instalador Windows (.exe). "
            "Instale manualmente pelo site https://www.raspberrypi.com/software/ e tente de novo."
        )
    with tempfile.TemporaryDirectory(prefix="totem-imager-") as temp_dir:
        setup_path = Path(temp_dir) / "rpi-imager-setup.exe"
        request = urllib.request.Request(
            str(installer_asset["browser_download_url"]),
            headers={"User-Agent": "TotemSetup/0.1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response, setup_path.open("wb") as setup:
                shutil.copyfileobj(response, setup)
        except Exception as exc:
            raise ImagerError(f"Não foi possível baixar o instalador oficial: {exc}") from exc
        if setup_path.stat().st_size < 1_000_000:
            raise ImagerError("O instalador baixado está incompleto; a gravação não foi iniciada.")
        if progress:
            progress("Executando o instalador oficial em modo silencioso...")
        try:
            completed = subprocess.run(
                [str(setup_path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                text=True,
                capture_output=True,
                timeout=600,
            )
        except subprocess.TimeoutExpired as exc:
            raise ImagerError(
                "O instalador oficial demorou demais para concluir. "
                "Conclua a instalação manualmente e tente de novo."
            ) from exc
        if completed.returncode != 0:
            details = (completed.stderr or completed.stdout).strip()
            raise ImagerError(f"Falha instalando o Raspberry Pi Imager: {details[-1200:]}")


def _install_official_linux_imager() -> None:
    """Upgrade Ubuntu's often-stale repository package from Raspberry Pi's official DEB."""
    if platform.machine().lower() not in {"x86_64", "amd64"}:
        raise ImagerError(
            "O pacote oficial atualizado está automatizado para Linux x86_64. "
            "Instale a versão atual do Raspberry Pi Imager manualmente neste computador."
        )
    if not shutil.which("sudo"):
        raise ImagerError("sudo não está disponível para atualizar o Raspberry Pi Imager.")
    with tempfile.TemporaryDirectory(prefix="totem-imager-") as temp_dir:
        package_path = Path(temp_dir) / "rpi-imager-latest.deb"
        request = urllib.request.Request(
            OFFICIAL_LINUX_DEB_URL,
            headers={"User-Agent": "TotemSetup/0.1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response, package_path.open("wb") as package:
                shutil.copyfileobj(response, package)
        except Exception as exc:
            raise ImagerError(f"Não foi possível baixar o Imager oficial atualizado: {exc}") from exc
        if package_path.stat().st_size < 1_000_000:
            raise ImagerError("O pacote do Imager baixado está incompleto; a gravação não foi iniciada.")
        completed = subprocess.run(
            ["sudo", "-n", "apt-get", "install", "-y", str(package_path)],
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            details = (completed.stderr or completed.stdout).strip()
            raise ImagerError(f"Falha atualizando o Raspberry Pi Imager: {details[-1200:]}")


def _windows_file_version(executable: str) -> Optional[tuple[int, int, int]]:
    """Read the PE file version of a Windows executable via PowerShell."""
    escaped = executable.replace("'", "''")
    script = f"(Get-Item -LiteralPath '{escaped}').VersionInfo.FileVersion"
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return None
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", completed.stdout or "")
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def _require_first_run_support(executable: str) -> None:
    result = subprocess.run([executable, "--cli", "--help"], capture_output=True, text=True, timeout=15)
    help_text = (result.stdout or "") + (result.stderr or "")
    if "--first-run-script" in help_text:
        return
    if os.name == "nt":
        # The Windows GUI binary does not write CLI help to a captured pipe
        # (upstream: "Standard out empty in CLI mode (Windows)"), so the help
        # probe false-negatives there. Fall back to the file version:
        # --first-run-script exists since Imager 1.9.
        version = _windows_file_version(executable)
        if version is not None and version >= (1, 9, 0):
            return
    raise ImagerError(
        "A versão instalada do Raspberry Pi Imager não oferece --first-run-script. "
        "Atualize o Imager antes de gravar; nenhum dado foi escrito."
    )


def _run_install(command: list[str]) -> None:
    if platform.system().lower() == "linux" and not is_elevated():
        command = ["sudo", "-n", *command]
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout).strip()
        raise ImagerError(f"Falha instalando Raspberry Pi Imager: {details[-1200:]}")


def authorize_sudo(password: str) -> None:
    """Validate a user-entered sudo password without putting it in argv or a file."""
    if not shutil.which("sudo"):
        raise ImagerError("O comando sudo não está disponível neste Linux.")
    try:
        result = subprocess.run(
            ["sudo", "-S", "-p", "", "-v"], input=password + "\n",
            text=True, capture_output=True, timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise ImagerError("Tempo esgotado ao validar a senha sudo.") from exc
    if result.returncode != 0:
        raise ImagerError("A senha sudo foi recusada. Nenhum dado foi escrito.")


def _device_partitions(device: str) -> list[dict]:
    """List partitions of exactly one block device via lsblk (Linux only)."""
    if platform.system().lower() != "linux":
        raise ImagerError("A preparação explícita do cartão é suportada apenas no Linux.")
    completed = subprocess.run(
        ["lsblk", "-J", "-p", "-o", "PATH,TYPE,FSTYPE,LABEL,MOUNTPOINTS", device],
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout).strip()
        raise ImagerError(f"Não foi possível inspecionar {device}: {details[-500:]}")
    try:
        nodes = json.loads(completed.stdout).get("blockdevices", [])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ImagerError(f"Resposta inesperada do lsblk para {device}.") from exc
    found: list[dict] = []

    def walk(entries: list) -> None:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") == "part":
                found.append(entry)
            children = entry.get("children") or []
            if children:
                walk(children)

    walk(nodes)
    return found


def _partition_mountpoints(partition: dict) -> list[str]:
    points = partition.get("mountpoints") or []
    if isinstance(points, str):
        points = [points]
    return [str(point) for point in points if point]


def unmount_device_partitions(device: str) -> None:
    """Unmount every mounted partition belonging to exactly `device`.

    Repeat flashes fail silently when the desktop auto-mounted bootfs/rootfs:
    the writer cannot replace a busy device. Only mountpoints reported by
    lsblk under this device are ever unmounted; nothing else is touched.
    """
    for partition in _device_partitions(device):
        for mountpoint in _partition_mountpoints(partition):
            completed = subprocess.run(
                [*_sudo_prefix(), "umount", mountpoint],
                text=True,
                capture_output=True,
            )
            if completed.returncode != 0:
                details = (completed.stderr or completed.stdout).strip()
                raise ImagerError(
                    f"Não foi possível desmontar {mountpoint} ({device}). "
                    f"Feche os arquivos abertos no cartão e tente de novo: {details[-500:]}"
                )
    remaining = [
        mountpoint
        for partition in _device_partitions(device)
        for mountpoint in _partition_mountpoints(partition)
    ]
    if remaining:
        raise ImagerError(
            f"O cartão {device} continua montado em {', '.join(remaining)}. "
            "Nenhum dado foi escrito."
        )


_FAT_TYPES = {"vfat", "fat", "fat32", "fat16", "fat12"}


def _wait_for_boot_partition(device: str, attempts: int = 20, delay: float = 1.0) -> Optional[dict]:
    """Wait for the kernel/udev to expose the FAT boot partition after a flash.

    Right after rpi-imager finishes, the partition table may not have been
    re-read yet, so the first lsblk can show no partitions at all. Ask the
    kernel to re-read the table and poll briefly instead of failing at once.
    """
    for attempt in range(attempts):
        boot = next(
            (
                partition
                for partition in _device_partitions(device)
                if str(partition.get("fstype", "")).lower() in _FAT_TYPES
            ),
            None,
        )
        if boot is not None:
            return boot
        if attempt + 1 < attempts:
            subprocess.run(
                [*_sudo_prefix(), "partprobe", device],
                text=True,
                capture_output=True,
            )
            subprocess.run(["udevadm", "settle"], text=True, capture_output=True)
            time.sleep(delay)
    return None


def wipe_device_signatures(device: str) -> None:
    """Erase partition/filesystem signatures so a repeat flash starts blank.

    Runs after unmounting and before the Imager write, on exactly `device`.
    The image write itself repartitions the card; this step makes the erase
    explicit and visible instead of relying on the writer alone.
    """
    completed = subprocess.run(
        [*_sudo_prefix(), "wipefs", "-a", "--", device],
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout).strip()
        raise ImagerError(
            f"Não foi possível apagar o conteúdo anterior de {device}. "
            f"Nenhuma gravação foi iniciada: {details[-500:]}"
        )


def verify_first_run_script(device: str, hostname: str) -> None:
    """Prove the flash actually rewrote this device with this run's config.

    Mounts the first FAT partition read-only and requires firstrun.sh to
    carry this run's hostname. A repeat flash that left stale content behind
    fails here instead of being reported as success.
    """
    boot = _wait_for_boot_partition(device)
    if boot is None:
        raise ImagerError(
            f"A partição de boot FAT não foi encontrada em {device} após a gravação. "
            "O cartão pode não ter sido regravado."
        )
    with tempfile.TemporaryDirectory(prefix="totem-verify-") as mount_dir:
        completed = subprocess.run(
            [*_sudo_prefix(), "mount", "-o", "ro", str(boot.get("path", "")), mount_dir],
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            details = (completed.stderr or completed.stdout).strip()
            raise ImagerError(f"Não foi possível conferir o cartão gravado: {details[-500:]}")
        try:
            script = Path(mount_dir) / "firstrun.sh"
            if not script.is_file():
                raise ImagerError(
                    "O firstrun.sh não foi encontrado no cartão gravado. "
                    "O cartão pode não ter sido regravado."
                )
            if hostname not in script.read_text(encoding="utf-8", errors="replace"):
                raise ImagerError(
                    "O cartão gravado ainda contém a configuração anterior "
                    f"(hostname {hostname!r} não encontrado). Nada foi aplicado."
                )
        finally:
            subprocess.run(
                [*_sudo_prefix(), "umount", mount_dir],
                text=True,
                capture_output=True,
            )


def _sudo_prefix() -> list[str]:
    if platform.system().lower() == "linux" and not is_elevated():
        return ["sudo", "-n"]
    return []


def build_flash_command(executable: str, image_uri: str, target_device: str,
                        expected_sha256: Optional[str] = None,
                        first_run_script: Optional[str] = None,
                        log_file: Optional[str] = None) -> list[str]:
    if os.name == "nt" and not is_process_elevated():
        raise ImagerError(
            "A gravação no Windows exige terminal com elevação real de Administrador "
            "(UAC): pertencer ao grupo Administradores sem elevar não basta."
        )
    args = [executable, "--cli", "--debug"]
    if platform.system().lower() == "linux" and not is_elevated():
        args = ["sudo", "-n", *args]
    if expected_sha256:
        args.extend(["--sha256", expected_sha256])
    if first_run_script:
        args.extend(["--first-run-script", first_run_script])
    if log_file:
        args.extend(["--log-file", log_file])
    args.extend([image_uri, target_device])
    return args


def read_imager_log_tail(log_file: str, max_chars: int = 900) -> str:
    """Last lines of the Imager --log-file, for surfacing real failures.

    On Windows the CLI writes progress/errors to the attached console rather
    than the pipe we capture, so this file is the reliable error channel.
    """
    try:
        text = Path(log_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-12:])[-max_chars:]


def wait_imager_log_tail(log_file: str, timeout_s: float = 6.0, max_chars: int = 900) -> str:
    """Poll the log briefly after a failure until its size stops changing.

    The Imager can exit while its log writer is still flushing, so reading
    once immediately can catch only the header line.
    """
    deadline = time.monotonic() + timeout_s
    last_size = -1
    stable_reads = 0
    tail = ""
    while time.monotonic() < deadline:
        try:
            size = Path(log_file).stat().st_size
        except OSError:
            size = 0
        tail = read_imager_log_tail(log_file, max_chars)
        if size == last_size and tail:
            stable_reads += 1
            if stable_reads >= 2:
                break
        else:
            stable_reads = 0
        last_size = size
        time.sleep(0.4)
    return tail


def describe_flash_failure(return_code: int, console_tail: str, last_output: str,
                           log_tail: str, command: list[str], executable: str,
                           first_run_script: Optional[str],
                           console_log_path: str, log_path: str) -> str:
    """Build the flash error detail shown when rpi-imager exits non-zero.

    On Windows the Imager often exits with an empty captured stdout and an
    empty --log-file, leaving only "code 1" behind. The exact command line,
    the executable, and the first-run script state are always included so a
    silent exit stays diagnosable.
    """
    details = [f"Raspberry Pi Imager encerrou com código {return_code}."]
    if console_tail:
        details.append(f"Saída do processo:\n{console_tail}")
    elif last_output:
        details.append(f"Última saída: {last_output}")
    if log_tail:
        details.append(f"Log do Imager:\n{log_tail}")
    if not console_tail and not last_output and not log_tail:
        details.append(
            "O Imager não registrou saída ou log: verifique o comando e o "
            "script de primeiro boot abaixo."
        )
    details.append(f"Executável: {executable}")
    if os.name == "nt":
        details.append(
            f"Processo elevado (UAC): {'sim' if is_process_elevated() else 'não'}"
        )
    details.append("Comando executado:\n" + " ".join(command))
    if first_run_script:
        if Path(first_run_script).is_file():
            details.append(f"Script de primeiro boot: {first_run_script} (presente)")
        else:
            details.append(
                f"Script de primeiro boot: {first_run_script} (não encontrado)"
            )
    details.extend((f"Saída completa em: {console_log_path}", f"Log completo em: {log_path}"))
    return "\n\n".join(details)
