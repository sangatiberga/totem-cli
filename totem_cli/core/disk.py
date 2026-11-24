"""Disk discovery and safe removable storage filtering for Linux and Windows."""
from __future__ import annotations

import json
import os
import platform
import subprocess
from dataclasses import dataclass
from typing import List, Optional
from rich.markup import escape


@dataclass
class DiskDrive:
    device: str
    name: str
    size_bytes: int
    size_gb: float
    model: str
    is_removable: bool
    is_system: bool
    mountpoints: List[str]

    def display_label(self) -> str:
        points = f" [{', '.join(self.mountpoints)}]" if self.mountpoints else ""
        # Textual's Select renders labels as Rich markup. Mount paths and model
        # names may contain square brackets, which must be displayed literally.
        return escape(f"{self.device} — {self.model or 'Drive'} ({self.size_gb:.1f} GB){points}")


def is_system_mountpoint(mountpoint: Optional[str]) -> bool:
    """Check if a mountpoint belongs to the host OS system."""
    if not mountpoint:
        return False
    mp = mountpoint.strip().lower()
    # Linux system root and critical directories
    if mp in {"/", "/boot", "/boot/efi", "/home", "/etc", "/var", "/usr"}:
        return True
    # Windows system drive
    if mp.startswith("c:"):
        return True
    return False


def get_removable_disks_linux() -> List[DiskDrive]:
    """Discover removable block devices using lsblk on Linux."""
    cmd = [
        "lsblk",
        "-J",
        "-b",
        "-o",
        "NAME,SIZE,TYPE,TRAN,RM,HOTPLUG,MOUNTPOINTS,MODEL",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(proc.stdout)
    except Exception:
        return []

    drives: List[DiskDrive] = []

    def inspect_device(node: dict) -> None:
        if node.get("type") != "disk":
            return

        name = node.get("name", "")
        device_path = f"/dev/{name}" if not name.startswith("/dev/") else name
        size_bytes = int(node.get("size") or 0)
        size_gb = size_bytes / (1024**3)
        model = (node.get("model") or "").strip()
        tran = (node.get("tran") or "").lower()
        rm = bool(node.get("rm"))
        hotplug = bool(node.get("hotplug"))

        # Collect all mountpoints from the disk and its children partitions
        mountpoints: List[str] = []
        if node.get("mountpoints"):
            mountpoints.extend([m for m in node["mountpoints"] if m])

        def harvest_children(child_nodes: list) -> None:
            for child in child_nodes:
                if child.get("mountpoints"):
                    mountpoints.extend([m for m in child["mountpoints"] if m])
                if child.get("children"):
                    harvest_children(child["children"])

        if node.get("children"):
            harvest_children(node["children"])

        is_system = any(is_system_mountpoint(mp) for mp in mountpoints)
        is_removable = rm or hotplug or (tran in {"usb", "mmc"})

        # Show mounted removable media as Raspberry Pi Imager itself does. The
        # Imager owns unmounting/locking at write time; hiding these devices
        # makes desktop auto-mounted SD cards appear to be missing. System disks
        # remain excluded even when they have removable child partitions.
        if is_removable and not is_system and size_bytes > 0:
            drives.append(
                DiskDrive(
                    device=device_path,
                    name=name,
                    size_bytes=size_bytes,
                    size_gb=size_gb,
                    model=model or "Cartão SD / USB",
                    is_removable=True,
                    is_system=False,
                    mountpoints=mountpoints,
                )
            )

    for item in data.get("blockdevices", []):
        inspect_device(item)

    return drives


def canonicalize_windows_device(device_id: str) -> str:
    """Normalize a Win32_DiskDrive DeviceID to the Imager's own spelling.

    WMI reports the device as all caps (e.g. PhysicalDrive1 in upper case)
    while ``rpi-imager --cli`` enumerates the mixed-case form and compares
    the destination with a case-sensitive ``==``. Passing the WMI spelling
    makes the Imager refuse the drive with exit code 1 and no message, so
    rewrite the known pattern to the canonical mixed-case form. Anything
    else passes through untouched.
    """
    if not device_id:
        return device_id
    prefix = "\\\\.\\physicaldrive"
    if not device_id.lower().startswith(prefix):
        return device_id
    number = device_id[len(prefix):]
    if not number.isdigit():
        return device_id
    return "\\\\" + ".\\" + "PhysicalDrive" + number


def get_removable_disks_windows() -> List[DiskDrive]:
    """Discover removable USB/SD drives on Windows using PowerShell."""
    ps_cmd = (
        "Get-CimInstance Win32_DiskDrive | Where-Object { $_.InterfaceType -eq 'USB' } | "
        "Select-Object DeviceID, Index, Model, Size, MediaType | ConvertTo-Json -Compress"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            check=True,
        )
        output = proc.stdout.strip()
        if not output:
            return []
        data = json.loads(output)
        if isinstance(data, dict):
            data = [data]
    except Exception:
        return []

    drives: List[DiskDrive] = []
    for item in data:
        device_id = canonicalize_windows_device(item.get("DeviceID", ""))
        size_bytes = int(item.get("Size") or 0)
        size_gb = size_bytes / (1024**3)
        model = (item.get("Model") or "").strip()

        # Query partitions and drive letters for this disk index
        idx = item.get("Index")
        mountpoints: List[str] = []
        is_system = False

        if idx is not None:
            part_cmd = (
                f"Get-Partition -DiskNumber {idx} | Get-Volume | "
                "Select-Object -ExpandProperty DriveLetter | ConvertTo-Json -Compress"
            )
            try:
                part_proc = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", part_cmd],
                    capture_output=True,
                    text=True,
                )
                part_out = part_proc.stdout.strip()
                if part_out:
                    letters = json.loads(part_out)
                    if isinstance(letters, str):
                        letters = [letters]
                    mountpoints = [f"{l}:" for l in letters if l]
            except Exception:
                pass

        is_system = any(is_system_mountpoint(mp) for mp in mountpoints)

        if not is_system and size_bytes > 0:
            drives.append(
                DiskDrive(
                    device=device_id,
                    name=f"Drive {idx}",
                    size_bytes=size_bytes,
                    size_gb=size_gb,
                    model=model or "Removable Storage",
                    is_removable=True,
                    is_system=False,
                    mountpoints=mountpoints,
                )
            )

    return drives


def get_removable_disks() -> List[DiskDrive]:
    """Retrieve filtered safe removable drives for the current operating system."""
    current_os = platform.system().lower()
    if current_os == "linux":
        return get_removable_disks_linux()
    elif current_os == "windows":
        return get_removable_disks_windows()
    return []
