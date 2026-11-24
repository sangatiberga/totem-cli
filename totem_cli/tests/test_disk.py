"""Unit tests for disk detection and safety filtering."""
import json
import unittest
from unittest.mock import MagicMock, patch
from totem_cli.core.disk import (
    canonicalize_windows_device,
    is_system_mountpoint,
    get_removable_disks_linux,
    get_removable_disks_windows,
)


class TestDiskSafety(unittest.TestCase):
    def test_system_mountpoints_detected(self):
        # Linux system roots and vital mounts
        self.assertTrue(is_system_mountpoint("/"))
        self.assertTrue(is_system_mountpoint("/boot"))
        self.assertTrue(is_system_mountpoint("/boot/efi"))
        self.assertTrue(is_system_mountpoint("/home"))
        self.assertTrue(is_system_mountpoint("/var"))
        # Windows system disk
        self.assertTrue(is_system_mountpoint("C:"))
        self.assertTrue(is_system_mountpoint("c:/"))
        self.assertTrue(is_system_mountpoint("c:\\Windows"))

    def test_non_system_mountpoints_allowed(self):
        self.assertFalse(is_system_mountpoint("/media/usb"))
        self.assertFalse(is_system_mountpoint("/mnt/sdcard"))
        self.assertFalse(is_system_mountpoint("D:"))
        self.assertFalse(is_system_mountpoint("E:"))
        self.assertFalse(is_system_mountpoint(None))
        self.assertFalse(is_system_mountpoint(""))

    @patch("subprocess.run")
    def test_linux_blocks_system_drive_and_allows_removable(self, mock_run):
        mock_lsblk_output = {
            "blockdevices": [
                {
                    "name": "nvme0n1",
                    "size": 512110190592,
                    "type": "disk",
                    "tran": "nvme",
                    "rm": False,
                    "hotplug": False,
                    "model": "Samsung SSD 970",
                    "mountpoints": [],
                    "children": [
                        {"name": "nvme0n1p1", "mountpoints": ["/boot/efi"]},
                        {"name": "nvme0n1p2", "mountpoints": ["/"]},
                    ],
                },
                {
                    "name": "sdb",
                    "size": 31914983424,
                    "type": "disk",
                    "tran": "usb",
                    "rm": True,
                    "hotplug": True,
                    "model": "SD Card Reader",
                    "mountpoints": [],
                    "children": [{"name": "sdb1", "mountpoints": []}],
                },
            ]
        }
        mock_proc = MagicMock()
        mock_proc.stdout = json_str = import_json_dumps(mock_lsblk_output)
        mock_run.return_value = mock_proc

        disks = get_removable_disks_linux()
        self.assertEqual(len(disks), 1)
        self.assertEqual(disks[0].device, "/dev/sdb")
        self.assertEqual(disks[0].model, "SD Card Reader")
        self.assertTrue(disks[0].is_removable)
        self.assertFalse(disks[0].is_system)

    @patch("subprocess.run")
    def test_linux_lists_mounted_removable_media(self, mock_run):
        # Desktop auto-mounts SD partitions; they must stay selectable because
        # the flash flow unmounts exactly this device before writing.
        mock_run.return_value.stdout = import_json_dumps({
            "blockdevices": [{
                "name": "sdc", "size": 32000000000, "type": "disk", "tran": "usb",
                "rm": True, "hotplug": True, "model": "SD Reader", "mountpoints": [],
                "children": [{"name": "sdc1", "mountpoints": ["/media/user/BOOT"]}],
            }]
        })
        disks = get_removable_disks_linux()
        self.assertEqual(len(disks), 1)
        self.assertEqual(disks[0].device, "/dev/sdc")
        self.assertEqual(disks[0].mountpoints, ["/media/user/BOOT"])



class TestWindowsDeviceCanonicalization(unittest.TestCase):
    BS = chr(92)

    def test_all_caps_wmi_spelling_is_rewritten(self):
        wmi = self.BS * 2 + "." + self.BS + "PHYSICALDRIVE1"
        self.assertEqual(
            canonicalize_windows_device(wmi),
            self.BS * 2 + "." + self.BS + "PhysicalDrive1",
        )

    def test_already_canonical_and_other_values_pass_through(self):
        canon = self.BS * 2 + "." + self.BS + "PhysicalDrive2"
        self.assertEqual(canonicalize_windows_device(canon), canon)
        self.assertEqual(canonicalize_windows_device("/dev/sda"), "/dev/sda")
        self.assertEqual(canonicalize_windows_device(""), "")
        self.assertIsNone(canonicalize_windows_device(None))
        bare = self.BS * 2 + "." + self.BS + "PhysicalDrive"
        self.assertEqual(canonicalize_windows_device(bare), bare)

    @patch("subprocess.run")
    def test_windows_listing_normalizes_device_id(self, mock_run):
        wmi = self.BS * 2 + "." + self.BS + "PHYSICALDRIVE1"
        payload = [{"DeviceID": wmi, "Index": 1, "Model": "SD Reader",
                    "Size": 32000000000}]
        first = MagicMock()
        first.stdout = json.dumps(payload)
        second = MagicMock()
        second.stdout = ""
        mock_run.side_effect = [first, second]
        disks = get_removable_disks_windows()
        self.assertEqual(len(disks), 1)
        self.assertEqual(disks[0].device,
                         self.BS * 2 + "." + self.BS + "PhysicalDrive1")


def import_json_dumps(data):
    import json
    return json.dumps(data)


if __name__ == "__main__":
    unittest.main()
