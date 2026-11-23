"""Unit tests for explicit erase/verify steps of the repeat-flash flow (Linux)."""
import unittest
from unittest.mock import MagicMock, patch

from totem_cli.core import imager


def _lsblk_result(stdout):
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = stdout
    proc.stderr = ""
    return proc


MOUNTED_CARD = """{"blockdevices": [{"name": "sda", "type": "disk", "children": [
  {"name": "sda1", "type": "part", "fstype": "vfat", "mountpoints": ["/media/user/bootfs"]},
  {"name": "sda2", "type": "part", "fstype": "ext4", "mountpoints": ["/media/user/rootfs"]}
]}]}"""

CLEAN_CARD = """{"blockdevices": [{"name": "sda", "type": "disk", "children": [
  {"name": "sda1", "type": "part", "fstype": "vfat", "mountpoints": []},
  {"name": "sda2", "type": "part", "fstype": "ext4", "mountpoints": []}
]}]}"""


class TestRepeatFlashErase(unittest.TestCase):
    @patch("subprocess.run")
    def test_unmount_targets_only_selected_device_partitions(self, mock_run):
        mock_run.side_effect = [
            _lsblk_result(MOUNTED_CARD),
            _lsblk_result("ok-umount-1"),
            _lsblk_result("ok-umount-2"),
            _lsblk_result(CLEAN_CARD),
        ]
        imager.unmount_device_partitions("/dev/sda")

        commands = [" ".join(call.args[0]) for call in mock_run.call_args_list]
        umounts = [cmd for cmd in commands if "umount" in cmd]
        self.assertEqual(len(umounts), 2)
        self.assertTrue(any("/media/user/bootfs" in cmd for cmd in umounts))
        self.assertTrue(any("/media/user/rootfs" in cmd for cmd in umounts))
        # The wipe target itself must never be passed to umount.
        for cmd in umounts:
            self.assertNotIn("umount /dev/sda ", cmd + " ")

    @patch("subprocess.run")
    def test_unmount_fails_when_partition_stays_mounted(self, mock_run):
        mock_run.side_effect = [
            _lsblk_result(MOUNTED_CARD),
            _lsblk_result("ok-umount"),
            _lsblk_result("ok-umount"),
            _lsblk_result(MOUNTED_CARD),
        ]
        with self.assertRaises(imager.ImagerError):
            imager.unmount_device_partitions("/dev/sda")

    @patch("subprocess.run")
    def test_verify_confirms_new_first_run_script(self, mock_run):
        mock_run.side_effect = [
            _lsblk_result(CLEAN_CARD),
            _lsblk_result("ok-mount"),
            _lsblk_result("ok-umount"),
        ]
        with patch("pathlib.Path.read_text", return_value="HOSTNAME_VAL='totem-test-01'"):
            with patch("pathlib.Path.is_file", return_value=True):
                imager.verify_first_run_script("/dev/sda", "totem-test-01")

        commands = [" ".join(call.args[0]) for call in mock_run.call_args_list]
        mounts = [cmd for cmd in commands if " mount " in f" {cmd} "]
        self.assertTrue(any("ro" in cmd for cmd in mounts))

    @patch("subprocess.run")
    def test_verify_waits_for_partition_table_to_settle(self, mock_run):
        no_parts = '{"blockdevices": [{"name": "sda", "type": "disk", "children": []}]}'
        mock_run.side_effect = [
            _lsblk_result(no_parts),          # kernel has not re-read the table yet
            _lsblk_result("ok-partprobe"),
            _lsblk_result("ok-udevadm-settle"),
            _lsblk_result(CLEAN_CARD),        # second look: FAT partition is there
            _lsblk_result("ok-mount"),
            _lsblk_result("ok-umount"),
        ]
        with patch("pathlib.Path.read_text", return_value="HOSTNAME_VAL='totem-test-01'"):
            with patch("pathlib.Path.is_file", return_value=True):
                with patch.object(imager.time, "sleep", lambda seconds: None):
                    imager.verify_first_run_script("/dev/sda", "totem-test-01")

    @patch("subprocess.run")
    def test_wipe_targets_exact_device_and_fails_loudly(self, mock_run):
        mock_run.return_value = _lsblk_result("ok-wipefs")
        imager.wipe_device_signatures("/dev/sda")

        command = mock_run.call_args.args[0]
        self.assertIn("wipefs", command)
        self.assertIn("-a", command)
        self.assertEqual(command[-2:], ["--", "/dev/sda"])

        failing = MagicMock()
        failing.returncode = 1
        failing.stdout = ""
        failing.stderr = "device busy"
        mock_run.return_value = failing
        with self.assertRaises(imager.ImagerError):
            imager.wipe_device_signatures("/dev/sda")

    @patch("subprocess.run")
    def test_verify_rejects_missing_first_run_script(self, mock_run):
        mock_run.side_effect = [
            _lsblk_result(CLEAN_CARD),
            _lsblk_result("ok-mount"),
            _lsblk_result("ok-umount"),
        ]
        with patch("pathlib.Path.is_file", return_value=False):
            with self.assertRaises(imager.ImagerError):
                imager.verify_first_run_script("/dev/sda", "totem-test-01")

    @patch("subprocess.run")
    def test_verify_rejects_stale_first_run_script(self, mock_run):
        mock_run.side_effect = [
            _lsblk_result(CLEAN_CARD),
            _lsblk_result("ok-mount"),
            _lsblk_result("ok-umount"),
        ]
        with patch("pathlib.Path.read_text", return_value="HOSTNAME_VAL='totem-old-99'"):
            with patch("pathlib.Path.is_file", return_value=True):
                with self.assertRaises(imager.ImagerError):
                    imager.verify_first_run_script("/dev/sda", "totem-test-01")


if __name__ == "__main__":
    unittest.main()
