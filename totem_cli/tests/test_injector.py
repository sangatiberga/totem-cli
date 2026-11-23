"""Unit tests for the boot partition injector module."""
import os
import tempfile
import unittest
from pathlib import Path
from totem_cli.core.injector import (
    TotemConfig,
    generate_sha512_crypt,
    inject_boot_files,
    parse_nexus_url,
)


class TestBootInjector(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.boot_path = Path(self.temp_dir.name)

        # Mock an existing cmdline.txt as found on standard Raspberry Pi OS images
        (self.boot_path / "cmdline.txt").write_text(
            "console=serial0,115200 console=tty1 root=PARTUUID=12345678-02 rootfstype=ext4 fsck.repair=yes rootwait",
            encoding="utf-8",
        )
        # Mock an existing config.txt
        (self.boot_path / "config.txt").write_text(
            "# Base Raspberry Pi OS config\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_generate_sha512_crypt_format(self):
        hash_val = generate_sha512_crypt("secret123")
        self.assertTrue(hash_val.startswith("$6$"))
        parts = hash_val.split("$")
        self.assertEqual(len(parts), 4)
        self.assertEqual(len(parts[2]), 16)

    def test_parse_nexus_url_with_port(self):
        host, port = parse_nexus_url("http://192.168.202.110:3001/auth")
        self.assertEqual(host, "192.168.202.110")
        self.assertEqual(port, 3001)

    def test_parse_nexus_url_https_default_port(self):
        host, port = parse_nexus_url("https://nexus.sangatiberga.com.br")
        self.assertEqual(host, "nexus.sangatiberga.com.br")
        self.assertEqual(port, 443)

    def test_parse_nexus_url_http_default_port(self):
        host, port = parse_nexus_url("http://nexus.local/app")
        self.assertEqual(host, "nexus.local")
        self.assertEqual(port, 80)

    def test_inject_boot_files_complete(self):
        config = TotemConfig(
            hostname="totem-qualidade-01",
            username="qualidade",
            password="qualidade_password",
            nexus_url="http://192.168.202.110:3001/auth",
            timezone="America/Fortaleza",
            screen_rotation="left",
            wifi_ssid="Industrial_WiFi",
            wifi_password="safe_password",
        )

        injected = inject_boot_files(self.boot_path, config)
        self.assertTrue(len(injected) >= 8)

        # 1. Bookworm flow: no legacy userconf.txt; the SHA-512 password hash
        # travels inside the first-run script instead.
        self.assertFalse((self.boot_path / "userconf.txt").exists())
        firstboot_early = (self.boot_path / "firstboot.sh").read_text(encoding="utf-8")
        self.assertIn("PASSWORD_HASH='$6$", firstboot_early)

        # 2. config.txt (KMS + hardware acceleration flags)
        cfg = (self.boot_path / "config.txt").read_text(encoding="utf-8")
        self.assertIn("dtoverlay=vc4-kms-v3d", cfg)
        self.assertIn("disable_splash=1", cfg)
        self.assertIn("disable_fw_kms_setup=1", cfg)

        # 3. totem_autostart (Dynamic screen rotation and seamless mpv loop splashscreen)
        autostart = (self.boot_path / "totem_autostart").read_text(encoding="utf-8")
        self.assertIn('xrandr --output "$OUT" --rotate left', autostart)
        self.assertIn("Nexus_Boot_Splashscreen.mp4", autostart)
        self.assertIn("monitor_network.py", autostart)

        # 4. monitor_network.py (Full production v2.0.0)
        monitor = (self.boot_path / "monitor_network.py").read_text(encoding="utf-8")
        self.assertIn('ONLINE_URL = "http://192.168.202.110:3001/auth"', monitor)
        self.assertIn('APP_HOST = "192.168.202.110"', monitor)
        self.assertIn('APP_PORT = 3001', monitor)
        self.assertIn("check_link", monitor)
        self.assertIn("check_app", monitor)
        self.assertIn("wifi_repair", monitor)
        self.assertIn("MONITOR_VERSION", monitor)

        # 5. offline.html
        offline = (self.boot_path / "offline.html").read_text(encoding="utf-8")
        self.assertIn("Nexus", offline)
        self.assertIn("/api/status", offline)
        self.assertIn("/api/retry", offline)

        # 6. No nexus-monitor systemd unit: the monitor starts from the
        # Openbox autostart inside the user session.
        self.assertFalse((self.boot_path / "nexus_monitor.service").exists())
        self.assertIn("monitor_network.py", autostart)

        # 7. totem_sudoers (nmcli + reboot without password)
        sudoers = (self.boot_path / "totem_sudoers").read_text(encoding="utf-8")
        self.assertIn("qualidade", sudoers)
        self.assertIn("nmcli", sudoers)
        self.assertIn("reboot", sudoers)

        # 8. firstboot.sh (Bookworm first-run provisioning)
        firstboot = (self.boot_path / "firstboot.sh").read_text(encoding="utf-8")
        self.assertIn("qualidade", firstboot)
        self.assertIn("totem-qualidade-01", firstboot)
        self.assertIn("99-vc4.conf", firstboot)
        self.assertIn(".xbindkeysrc", firstboot)
        self.assertIn("nexus-extension", firstboot)
        self.assertIn("offline.html", firstboot)
        self.assertIn("totem_sudoers", firstboot)
        # Mouse stays visible and Chromium never offers to save passwords.
        self.assertIn("xserver-command=X\n", firstboot)
        self.assertNotIn("nocursor", firstboot)
        self.assertIn("credentials_enable_service", firstboot)
        self.assertIn("password_manager_enabled", firstboot)

        # 9. cmdline.txt with silent OEM boot flags
        cmdline = (self.boot_path / "cmdline.txt").read_text(encoding="utf-8")
        self.assertIn("quiet splash loglevel=0 logo.nologo vt.global_cursor_default=0", cmdline)
        self.assertIn("systemd.run=/boot/firmware/firstboot.sh", cmdline)

        # 10. xbindkeysrc
        xbind = (self.boot_path / "xbindkeysrc").read_text(encoding="utf-8")
        self.assertIn("control+t", xbind)
        self.assertIn("Alt+F4", xbind)

        # 11. Bookworm Wi-Fi goes through NetworkManager in the first-run
        # script (base64), never through a legacy wpa_supplicant.conf file.
        import base64
        self.assertFalse((self.boot_path / "wpa_supplicant.conf").exists())
        self.assertIn(
            "WIFI_SSID_B64='" + base64.b64encode(b"Industrial_WiFi").decode() + "'",
            firstboot,
        )
        self.assertIn("nmcli connection add type wifi", firstboot)


if __name__ == "__main__":
    unittest.main()
