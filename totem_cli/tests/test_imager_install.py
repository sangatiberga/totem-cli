"""Unit tests for Raspberry Pi Imager installation with Windows fallback."""
import io
import json
import unittest
from unittest.mock import MagicMock, patch

from totem_cli.core import imager


FAKE_EXE = "C:\\Program Files\\Raspberry Pi Imager\\rpi-imager.exe"


def _api_response(names):
    body = json.dumps({"assets": [
        {"name": name, "browser_download_url": f"https://example.com/{name}"}
        for name in names
    ]}).encode("utf-8")
    response = MagicMock()
    response.read.side_effect = [body, b""]
    response.__enter__.return_value = response
    return response


def _download_response(size=2_000_000):
    response = MagicMock()
    response.read.side_effect = [b"x" * size, b""]
    response.__enter__.return_value = response
    return response


def _completed(returncode=0, stdout="", stderr=""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


class TestEnsureImagerWindows(unittest.TestCase):
    def _patch_platform(self, active):
        active.append(patch("platform.system", return_value="Windows").start())
        active.append(patch.object(imager, "is_elevated", return_value=True).start())
        active.append(patch.object(imager, "_require_first_run_support").start())

    def tearDown(self):
        patch.stopall()

    def test_winget_success_skips_download(self):
        active = []
        self._patch_platform(active)
        with patch("shutil.which", return_value="C:\\winget.exe"), \
                patch.object(imager, "find_all_imagers", side_effect=[[], [], [FAKE_EXE]]) as finder, \
                patch("subprocess.run", return_value=_completed(0)) as mock_run, \
                patch("urllib.request.urlopen") as mock_urlopen:
            self.assertEqual(imager.ensure_imager(), FAKE_EXE)
            self.assertEqual(finder.call_count, 3)
            winget_calls = [c.args[0] for c in mock_run.call_args_list if "winget" in str(c.args[0][0])]
            self.assertEqual(len(winget_calls), 1)
            mock_urlopen.assert_not_called()

    def test_winget_failure_falls_back_to_official_installer(self):
        active = []
        self._patch_platform(active)
        messages = []
        with patch("shutil.which", return_value="C:\\winget.exe"), \
                patch.object(imager, "find_all_imagers", side_effect=[[], [], [FAKE_EXE]]), \
                patch("urllib.request.urlopen",
                      side_effect=[_api_response(["imager-v2.0.12.exe"]), _download_response()]), \
                patch("subprocess.run") as mock_run:
            def run(cmd, **kwargs):
                if "winget" in str(cmd[0]):
                    return _completed(1, "", "Failed when searching source: winget")
                return _completed(0)
            mock_run.side_effect = run
            self.assertEqual(imager.ensure_imager(messages.append), FAKE_EXE)
            installer_calls = [c.args[0] for c in mock_run.call_args_list
                               if str(c.args[0][0]).endswith("rpi-imager-setup.exe")]
            self.assertEqual(len(installer_calls), 1)
            self.assertIn("/VERYSILENT", installer_calls[0])
            self.assertIn("/SUPPRESSMSGBOXES", installer_calls[0])
            self.assertIn("/NORESTART", installer_calls[0])
            self.assertTrue(any("winget" in message for message in messages))

    def test_no_winget_goes_straight_to_official_installer(self):
        active = []
        self._patch_platform(active)
        with patch("shutil.which", return_value=None), \
                patch.object(imager, "find_all_imagers", side_effect=[[], [], [FAKE_EXE]]), \
                patch("urllib.request.urlopen",
                      side_effect=[_api_response(["imager-v2.0.12.exe"]), _download_response()]), \
                patch("subprocess.run", return_value=_completed(0)) as mock_run:
            self.assertEqual(imager.ensure_imager(), FAKE_EXE)
            self.assertTrue(any("rpi-imager-setup.exe" in str(c.args[0][0])
                                for c in mock_run.call_args_list))

    def test_missing_exe_asset_raises_before_install(self):
        active = []
        self._patch_platform(active)
        with patch("shutil.which", return_value=None), \
                patch.object(imager, "find_all_imagers", return_value=[]), \
                patch("urllib.request.urlopen",
                      side_effect=[_api_response(["imager-v2.0.12.AppImage"]), _download_response()]), \
                patch("subprocess.run") as mock_run:
            with self.assertRaises(imager.ImagerError):
                imager.ensure_imager()
            self.assertFalse(any("rpi-imager-setup.exe" in str(c.args[0][0])
                                 for c in mock_run.call_args_list))

    def test_outdated_imager_upgrades_via_official_installer(self):
        active = []
        self._patch_platform(active)
        old_exe = "C:\\Program Files\\Raspberry Pi Imager\\rpi-imager.exe"
        new_exe = "C:\\Program Files\\Raspberry Pi Ltd\\Imager\\rpi-imager.exe"
        with patch.object(imager, "find_all_imagers", side_effect=[[old_exe], [old_exe], [old_exe, new_exe]]), \
                patch.object(imager, "_require_first_run_support",
                             side_effect=[imager.ImagerError("old"), imager.ImagerError("old"), None]), \
                patch("urllib.request.urlopen",
                      side_effect=[_api_response(["imager-v2.0.12.exe"]), _download_response()]), \
                patch("subprocess.run", return_value=_completed(0)) as mock_run:
            self.assertEqual(imager.ensure_imager(), new_exe)
            installer_calls = [c.args[0] for c in mock_run.call_args_list
                               if str(c.args[0][0]).endswith("rpi-imager-setup.exe")]
            self.assertEqual(len(installer_calls), 1)
            self.assertIn("/VERYSILENT", installer_calls[0])

    def test_usable_copy_beside_outdated_one_needs_no_upgrade(self):
        active = []
        self._patch_platform(active)
        old_exe = "C:\\Program Files\\Raspberry Pi Imager\\rpi-imager.exe"
        new_exe = "C:\\Program Files\\Raspberry Pi Ltd\\Imager\\rpi-imager.exe"
        with patch.object(imager, "find_all_imagers", return_value=[old_exe, new_exe]), \
                patch.object(imager, "_require_first_run_support",
                             side_effect=[imager.ImagerError("old"), None]), \
                patch("subprocess.run") as mock_run, \
                patch("urllib.request.urlopen") as mock_urlopen:
            self.assertEqual(imager.ensure_imager(), new_exe)
            mock_run.assert_not_called()
            mock_urlopen.assert_not_called()

    def test_flash_command_includes_log_file_and_log_tail(self):
        with patch.object(imager, "is_elevated", return_value=True):
            command = imager.build_flash_command(
                "rpi-imager", "https://example.com/img.xz", "dest",
                "a" * 64, "script.sh", "imager.log",
            )
        self.assertIn("--log-file", command)
        self.assertIn("imager.log", command)
        self.assertEqual(command[-2:], ["https://example.com/img.xz", "dest"])
        import tempfile as _tf
        with _tf.NamedTemporaryFile("w", suffix=".log", delete=False) as fh:
            fh.write("\n".join(f"linha {i}" for i in range(30)))
            name = fh.name
        try:
            tail = imager.read_imager_log_tail(name)
            self.assertIn("linha 29", tail)
            self.assertNotIn("linha 0", tail)
        finally:
            import os as _os
            _os.unlink(name)
        self.assertEqual(imager.read_imager_log_tail("/nao/existe.log"), "")

    def test_wait_imager_log_waits_for_late_lines(self):
        import tempfile as _tf, threading, time as _time
        with _tf.NamedTemporaryFile("w", suffix=".log", delete=False) as fh:
            fh.write("*** Raspberry Pi Imager started ***\n")
            name = fh.name

        def writer():
            _time.sleep(0.6)
            with open(name, "a") as fh:
                fh.write("Error: drive is locked\n")

        import os as _os
        try:
            threading.Thread(target=writer).start()
            tail = imager.wait_imager_log_tail(name, timeout_s=5)
            self.assertIn("drive is locked", tail)
        finally:
            _os.unlink(name)

    def test_windows_probe_accepts_v2_despite_empty_help(self):
        def run(cmd, **kwargs):
            if cmd[0] == "powershell":
                return _completed(0, "2.0.12\n", "")
            return _completed(0, "", "")
        with patch("os.name", "nt"), patch("subprocess.run", side_effect=run):
            imager._require_first_run_support("C:\\Program Files\\Raspberry Pi Ltd\\Imager\\rpi-imager.exe")

    def test_windows_probe_rejects_old_version(self):
        def run(cmd, **kwargs):
            if cmd[0] == "powershell":
                return _completed(0, "1.8.5\n", "")
            return _completed(0, "", "")
        with patch("os.name", "nt"), patch("subprocess.run", side_effect=run):
            with self.assertRaises(imager.ImagerError):
                imager._require_first_run_support("C:\\old\\rpi-imager.exe")

    def test_flash_command_includes_debug_flag(self):
        with patch.object(imager, "is_elevated", return_value=True):
            command = imager.build_flash_command(
                "rpi-imager", "https://example.com/img.xz", "dest",
                None, None, None,
            )
        self.assertIn("--debug", command)

    def test_windows_flash_gate_requires_real_elevation(self):
        args = ("rpi-imager.exe", "https://example.com/img.xz", "dest", None, None, None)
        with patch("os.name", "nt"), \
                patch.object(imager, "is_process_elevated", return_value=False):
            with self.assertRaises(imager.ImagerError):
                imager.build_flash_command(*args)
        with patch("os.name", "nt"), \
                patch.object(imager, "is_process_elevated", return_value=True):
            command = imager.build_flash_command(*args)
        self.assertIn("--debug", command)

    def test_strict_elevation_matches_euid_off_windows(self):
        import os as _os
        with patch("os.name", "posix"):
            self.assertEqual(
                imager.is_process_elevated(),
                hasattr(_os, "geteuid") and _os.geteuid() == 0,
            )

    def test_strict_elevation_reads_token_on_windows(self):
        from unittest.mock import MagicMock as _MagicMock
        fake_windll = _MagicMock()
        fake_windll.kernel32.GetCurrentProcess.return_value = 1234
        fake_windll.advapi32.OpenProcessToken.return_value = True

        def _elevated(token, cls, buf, size, ret):
            buf._obj.value = 1
            return True

        def _filtered(token, cls, buf, size, ret):
            buf._obj.value = 0
            return True

        with patch("os.name", "nt"), \
                patch.object(imager.ctypes, "windll", fake_windll, create=True):
            fake_windll.advapi32.GetTokenInformation.side_effect = _elevated
            self.assertTrue(imager.is_process_elevated())
            fake_windll.advapi32.GetTokenInformation.side_effect = _filtered
            self.assertFalse(imager.is_process_elevated())
            fake_windll.advapi32.OpenProcessToken.return_value = False
            fake_windll.shell32.IsUserAnAdmin.return_value = True
            self.assertTrue(imager.is_process_elevated())

    def test_not_elevated_aborts_before_any_install_attempt(self):
        active = []
        self._patch_platform(active)
        with patch.object(imager, "is_elevated", return_value=False), \
                patch.object(imager, "find_all_imagers", return_value=[]), \
                patch("subprocess.run") as mock_run, \
                patch("urllib.request.urlopen") as mock_urlopen:
            with self.assertRaises(imager.ImagerError):
                imager.ensure_imager()
            mock_run.assert_not_called()
            mock_urlopen.assert_not_called()


class TestFlashFailureDetail(unittest.TestCase):
    def test_empty_outputs_still_identify_command_and_script(self):
        detail = imager.describe_flash_failure(
            return_code=1,
            console_tail="",
            last_output="",
            log_tail="",
            command=[FAKE_EXE, "--cli", "--first-run-script", "C:\\Temp\\totem-first-run.sh",
                     "--log-file", "C:\\Temp\\totem-imager.log",
                     "https://example.com/image.img.xz", "\\\\.\\PhysicalDrive1"],
            executable=FAKE_EXE,
            first_run_script="C:\\Temp\\totem-first-run.sh",
            console_log_path="C:\\Temp\\totem-imager-console.log",
            log_path="C:\\Temp\\totem-imager.log",
        )
        self.assertIn("código 1", detail)
        self.assertIn(FAKE_EXE, detail)
        self.assertIn("--first-run-script", detail)
        self.assertIn("C:\\Temp\\totem-first-run.sh", detail)
        self.assertIn("\\\\.\\PhysicalDrive1", detail)
        self.assertIn("C:\\Temp\\totem-imager-console.log", detail)
        self.assertIn("C:\\Temp\\totem-imager.log", detail)

    def test_tails_are_included_when_present(self):
        detail = imager.describe_flash_failure(
            return_code=1,
            console_tail="some console line",
            last_output="",
            log_tail="some log line",
            command=[FAKE_EXE, "--cli"],
            executable=FAKE_EXE,
            first_run_script=None,
            console_log_path="C:\\Temp\\totem-imager-console.log",
            log_path="C:\\Temp\\totem-imager.log",
        )
        self.assertIn("some console line", detail)
        self.assertIn("some log line", detail)

    def test_detail_reports_uac_elevation_state(self):
        kwargs = dict(
            return_code=1,
            console_tail="",
            last_output="",
            log_tail="",
            command=[FAKE_EXE, "--cli"],
            executable=FAKE_EXE,
            first_run_script=None,
            console_log_path="C:\\Temp\\totem-imager-console.log",
            log_path="C:\\Temp\\totem-imager.log",
        )
        with patch("os.name", "nt"), \
                patch.object(imager, "is_process_elevated", return_value=False):
            self.assertIn(
                "Processo elevado (UAC): não", imager.describe_flash_failure(**kwargs)
            )
        with patch("os.name", "nt"), \
                patch.object(imager, "is_process_elevated", return_value=True):
            self.assertIn(
                "Processo elevado (UAC): sim", imager.describe_flash_failure(**kwargs)
            )
        with patch("os.name", "posix"):
            self.assertNotIn("Processo elevado", imager.describe_flash_failure(**kwargs))

    def test_missing_script_is_flagged(self):
        with patch("pathlib.Path.is_file", return_value=False):
            detail = imager.describe_flash_failure(
                return_code=1,
                console_tail="",
                last_output="",
                log_tail="",
                command=[FAKE_EXE, "--cli"],
                executable=FAKE_EXE,
                first_run_script="C:\\Temp\\missing.sh",
                console_log_path="C:\\Temp\\totem-imager-console.log",
                log_path="C:\\Temp\\totem-imager.log",
            )
        self.assertIn("C:\\Temp\\missing.sh", detail)
        self.assertIn("não encontrado", detail)


if __name__ == "__main__":
    unittest.main()
