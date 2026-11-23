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
                patch.object(imager, "find_imager", side_effect=[None, FAKE_EXE]) as finder, \
                patch("subprocess.run", return_value=_completed(0)) as mock_run, \
                patch("urllib.request.urlopen") as mock_urlopen:
            self.assertEqual(imager.ensure_imager(), FAKE_EXE)
            self.assertEqual(finder.call_count, 2)
            winget_calls = [c.args[0] for c in mock_run.call_args_list if "winget" in str(c.args[0][0])]
            self.assertEqual(len(winget_calls), 1)
            mock_urlopen.assert_not_called()

    def test_winget_failure_falls_back_to_official_installer(self):
        active = []
        self._patch_platform(active)
        messages = []
        with patch("shutil.which", return_value="C:\\winget.exe"), \
                patch.object(imager, "find_imager", side_effect=[None, FAKE_EXE]), \
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
                patch.object(imager, "find_imager", side_effect=[None, FAKE_EXE]), \
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
                patch.object(imager, "find_imager", return_value=None), \
                patch("urllib.request.urlopen",
                      side_effect=[_api_response(["imager-v2.0.12.AppImage"]), _download_response()]), \
                patch("subprocess.run") as mock_run:
            with self.assertRaises(imager.ImagerError):
                imager.ensure_imager()
            self.assertFalse(any("rpi-imager-setup.exe" in str(c.args[0][0])
                                 for c in mock_run.call_args_list))

    def test_not_elevated_aborts_before_any_install_attempt(self):
        active = []
        self._patch_platform(active)
        with patch.object(imager, "is_elevated", return_value=False), \
                patch.object(imager, "find_imager", return_value=None), \
                patch("subprocess.run") as mock_run, \
                patch("urllib.request.urlopen") as mock_urlopen:
            with self.assertRaises(imager.ImagerError):
                imager.ensure_imager()
            mock_run.assert_not_called()
            mock_urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
