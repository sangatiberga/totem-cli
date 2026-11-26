"""Unit tests for the SSH remote administration module using mocks."""
import unittest
from unittest.mock import MagicMock, patch
from totem_cli.core.ssh import TotemSshClient, RemoteTotemState, SshClientError


class TestSshClient(unittest.TestCase):
    def setUp(self):
        self.client = TotemSshClient(host="192.168.1.100", user="usinagem", password="secret_password")

    @patch("totem_cli.core.ssh.paramiko")
    def test_connect_and_inspect_totem(self, mock_paramiko):
        mock_ssh = MagicMock()
        mock_paramiko.SSHClient.return_value = mock_ssh

        self.client.connect()
        self.assertIsNotNone(self.client._client)

        def fake_run_cmd(cmd, sudo=False):
            if cmd == "hostname":
                return (0, "usinagem-rpi5-07\n", "")
            elif "grep -E '^autologin-user='" in cmd:
                return (0, "autologin-user=usinagem\n", "")
            elif "cat /home/usinagem/monitor_network.py" in cmd:
                return (
                    0,
                    'ONLINE_URL = "http://192.168.202.110:3001/auth"\n'
                    'APP_HOST = "192.168.202.110"\n'
                    'APP_PORT = 3001\n',
                    "",
                )
            elif "cat /etc/xdg/openbox/autostart" in cmd:
                autostart_content = (
                    'xrandr --output "$OUT" --rotate left\n'
                    'python3 "/home/usinagem/monitor_network.py" &\n'
                )
                return (0, autostart_content, "")
            elif cmd == "systemctl is-active lightdm":
                return (0, "active\n", "")
            elif cmd == "pgrep -a chromium":
                return (0, "1234 chromium --kiosk ...\n", "")
            elif cmd == "systemctl is-active nexus-monitor":
                return (0, "active\n", "")
            return (0, "", "")

        self.client.run_cmd = MagicMock(side_effect=fake_run_cmd)

        state = self.client.inspect_totem()
        self.assertEqual(state.hostname, "usinagem-rpi5-07")
        self.assertEqual(state.autologin_user, "usinagem")
        self.assertEqual(state.nexus_url, "http://192.168.202.110:3001/auth")
        self.assertEqual(state.app_host, "192.168.202.110")
        self.assertEqual(state.app_port, "3001")
        self.assertEqual(state.rotation, "left")
        self.assertTrue(state.lightdm_active)
        self.assertTrue(state.chromium_running)
        self.assertTrue(state.monitor_service_active)

    def test_update_nexus_url_creates_backup_and_replaces(self):
        commands_run = []
        mock_sftp = MagicMock()
        self.client._client = MagicMock()
        self.client._client.open_sftp.return_value = mock_sftp
        mock_remote_file = MagicMock()
        mock_sftp.file.return_value.__enter__.return_value = mock_remote_file

        def fake_run_cmd(cmd, sudo=False):
            commands_run.append(cmd)
            if "cat -- /home/usinagem/monitor_network.py" in cmd:
                return (
                    0,
                    'ONLINE_URL = "http://old-nexus:3000/auth"\n'
                    'APP_HOST = "old-nexus"\n'
                    'APP_PORT = 3000\n',
                    "",
                )
            return (0, "", "")

        self.client.run_cmd = MagicMock(side_effect=fake_run_cmd)

        backup_file = self.client.update_nexus_url("http://new-nexus:3001/auth")
        self.assertTrue(backup_file.startswith("/home/usinagem/monitor_network.py.bak-"))

        # Verify backup was created
        self.assertTrue(any("cp -- /home/usinagem/monitor_network.py /home/usinagem/monitor_network.py.bak-" in c for c in commands_run))
        # Verify new URL is applied
        self.assertIn("http://new-nexus:3001/auth", mock_remote_file.write.call_args.args[0])
        # Verify temp file is moved atomically
        self.assertTrue(any("install -o usinagem -g usinagem -m 755 /tmp/monitor_network_" in c for c in commands_run))
        self.assertTrue(mock_remote_file.write.called)

    def test_restart_graphics_restarts_lightdm_only(self):
        commands_run = []

        def fake_run_cmd(cmd, sudo=False):
            commands_run.append(cmd)
            return (0, "", "")

        self.client._client = MagicMock()
        self.client.run_cmd = MagicMock(side_effect=fake_run_cmd)

        self.client.restart_graphics()

        # The monitor runs from the Openbox session, so restarting LightDM
        # reloads the whole kiosk stack; there is no nexus-monitor service.
        self.assertTrue(any("lightdm" in c for c in commands_run))
        self.assertFalse(any("nexus-monitor" in c for c in commands_run))


class TestSshRemoteEditing(unittest.TestCase):
    def setUp(self):
        self.client = TotemSshClient(host="192.168.1.100", user="usinagem", password="secret_password")
        self.client._client = MagicMock()
        self.commands = []
        self.client.run_cmd = MagicMock(side_effect=self._fake)

    def _fake(self, cmd, sudo=False, extra_stdin=""):
        self.commands.append(cmd)
        if cmd == "hostname":
            return (0, "totem-old-01\n", "")
        if "timedatectl show -p Timezone" in cmd:
            return (0, "America/Fortaleza\n", "")
        if "test -f /etc/totem-provisioned" in cmd:
            return (0, "", "")
        if "nmcli -t -f NAME,DEVICE connection show --active" in cmd:
            return (0, "totem-wifi:wlan0\n", "")
        if "nmcli radio wifi" in cmd:
            return (0, "enabled\n", "")
        if cmd.startswith("test -f") and "nexus-wifi-disabled" in cmd:
            return (1, "", "")
        if "grep -E '^autologin-user='" in cmd:
            return (0, "autologin-user=usinagem\n", "")
        if "monitor_network.py" in cmd and cmd.startswith("cat"):
            return (0, 'ONLINE_URL = "http://x/auth"\nAPP_HOST = "x"\nAPP_PORT = 80\n'
                       '"""Escada de reparo Wi-Fi (requer sudoers do monitor). Retorna descricao."""\n', "")
        if "openbox/autostart" in cmd:
            return (0, 'xrandr --output "$OUT" --rotate left\n', "")
        if cmd == "systemctl is-active lightdm":
            return (0, "active\n", "")
        if cmd == "pgrep -a chromium":
            return (0, "1234 chromium\n", "")
        if cmd == "systemctl is-active nexus-monitor":
            return (1, "inactive\n", "")
        return (0, "", "")

    def test_inspect_reports_wifi_timezone_and_provisioning(self):
        state = self.client.inspect_totem()
        self.assertEqual(state.timezone, "America/Fortaleza")
        self.assertTrue(state.provisioned)
        self.assertTrue(state.wifi_enabled)
        self.assertTrue(state.wifi_managed)
        self.assertEqual(state.wifi_ssid, "totem-wifi")

    def test_set_hostname_updates_system_and_hosts(self):
        self.client.set_hostname("qualidade-rpi5-2")
        joined = "\n".join(self.commands)
        self.assertIn("hostnamectl set-hostname", joined)
        self.assertIn("qualidade-rpi5-2", joined)

    def test_set_hostname_rejects_invalid(self):
        with self.assertRaises(SshClientError):
            self.client.set_hostname("nome inválido!")
        with self.assertRaises(SshClientError):
            self.client.set_hostname("")

    def test_set_rotation_rewrites_autostart_value(self):
        written = {}
        mock_sftp = MagicMock()
        self.client._client.open_sftp.return_value = mock_sftp
        remote = MagicMock()
        mock_sftp.file.return_value.__enter__.return_value = remote
        written_holder = {}
        remote.write.side_effect = lambda content: written_holder.setdefault("c", content)
        self.client.set_rotation("right")
        self.assertIn("--rotate right", written_holder["c"])
        self.assertNotIn("--rotate left", written_holder["c"])

    def test_set_rotation_rejects_invalid(self):
        with self.assertRaises(SshClientError):
            self.client.set_rotation("diagonal")

    def test_set_timezone_applies(self):
        self.client.set_timezone("America/Sao_Paulo")
        self.assertTrue(any("timedatectl set-timezone" in c and "America/Sao_Paulo" in c for c in self.commands))

    def test_set_timezone_rejects_invalid(self):
        with self.assertRaises(SshClientError):
            self.client.set_timezone("Agora Mesmo")

    def test_wifi_toggle(self):
        self.client.set_wifi_enabled(False)
        self.assertTrue(any("nmcli radio wifi off" in c for c in self.commands))
        self.client.set_wifi_enabled(True)
        self.assertTrue(any("nmcli radio wifi on" in c for c in self.commands))

    def test_wifi_disable_locks_monitor_repair(self):
        mock_sftp = MagicMock()
        self.client._client.open_sftp.return_value = mock_sftp
        remote = MagicMock()
        mock_sftp.file.return_value.__enter__.return_value = remote
        written = {}
        remote.write.side_effect = lambda content: written.setdefault("c", content)
        self.client.set_wifi_enabled(False)
        joined = "\n".join(self.commands)
        self.assertIn("connection.autoconnect no", joined)
        self.assertIn("nexus-wifi-disabled", joined)
        # Old monitor without the guard gets patched atomically.
        self.assertIn("disabled-by-operator", written.get("c", ""))
        self.assertTrue(any(".bak-" in c for c in self.commands))

    def test_wifi_disable_locks_before_cutting_radio(self):
        self.client.set_wifi_enabled(False)
        order = [i for i, c in enumerate(self.commands) if "autoconnect no" in c or "radio wifi off" in c]
        auto = next(i for i, c in enumerate(self.commands) if "autoconnect no" in c)
        off = next(i for i, c in enumerate(self.commands) if "radio wifi off" in c)
        self.assertLess(auto, off)

    def test_wifi_disable_reports_dropped_link(self):
        from totem_cli.core.ssh import WifiLinkDroppedError

        def fake_drop(cmd, sudo=False, extra_stdin=""):
            self.commands.append(cmd)
            if cmd.strip() == "echo ok":
                raise SshClientError("Erro ao executar comando remoto 'echo ok': fechada")
            if "monitor_network.py" in cmd:
                return (0, 'ONLINE_URL = "x"\nif os.path.exists(HOME + "/.config/nexus-wifi-disabled"):\n', "")
            return (0, "", "")

        self.client.run_cmd = MagicMock(side_effect=fake_drop)
        mock_sftp = MagicMock()
        self.client._client.open_sftp.return_value = mock_sftp
        with self.assertRaises(WifiLinkDroppedError):
            self.client.set_wifi_enabled(False)

    def test_wifi_disable_skips_patch_when_guard_present(self):
        def fake_with_guard(cmd, sudo=False, extra_stdin=""):
            self.commands.append(cmd)
            if "cat -- /home/usinagem/monitor_network.py" in cmd:
                return (0, 'ONLINE_URL = "x"\nif os.path.exists(HOME + "/.config/nexus-wifi-disabled"):\n', "")
            return (0, "", "")
        self.client.run_cmd = MagicMock(side_effect=fake_with_guard)
        mock_sftp = MagicMock()
        self.client._client.open_sftp.return_value = mock_sftp
        self.client.set_wifi_enabled(False)
        self.assertFalse(any(".bak-" in c for c in self.commands))

    def test_wifi_configure_rejects_bad_secrets(self):
        with self.assertRaises(SshClientError):
            self.client.configure_wifi("Rede", "curta")
        with self.assertRaises(SshClientError):
            self.client.configure_wifi("", "senha-valida-123")

    def test_change_password_rejects_unsafe(self):
        with self.assertRaises(SshClientError):
            self.client.change_password("")
        with self.assertRaises(SshClientError):
            self.client.change_password("com\nquebra")

    def test_change_password_uses_chpasswd(self):
        self.client.change_password("nova-senha-123")
        self.assertTrue(any("chpasswd" in c for c in self.commands))

    def test_change_password_updates_credential(self):
        self.client.change_password("nova-senha-456")
        self.assertEqual(self.client.password, "nova-senha-456")

    def test_read_logs_rejects_unknown_source(self):
        with self.assertRaises(SshClientError):
            self.client.read_logs("/etc/shadow")

    def test_read_logs_tails_firstboot(self):
        text = self.client.read_logs("firstboot", lines=10)
        self.assertTrue(any("totem-firstboot.log" in c for c in self.commands))
        self.assertIsInstance(text, str)

    def test_remove_known_host_key(self):
        import paramiko
        import tempfile
        from pathlib import Path
        from totem_cli.core.ssh import remove_known_host_key, _fingerprint
        host = "192.168.1.99"
        key = paramiko.RSAKey.generate(2048)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "known_hosts"
            hk = paramiko.HostKeys()
            hk.add(host, key.get_name(), key)
            hk.save(str(path))
            self.assertTrue(remove_known_host_key(host, path))
            hk2 = paramiko.HostKeys()
            hk2.load(str(path))
            self.assertIsNone(hk2.lookup(host))
            self.assertFalse(remove_known_host_key(host, path))

    def test_bad_host_key_raises_changed(self):
        import paramiko
        from unittest.mock import patch as _patch
        from totem_cli.core.ssh import HostKeyChangedError, _fingerprint
        from paramiko.ssh_exception import BadHostKeyException
        host = "192.168.1.100"
        new_key = paramiko.RSAKey.generate(2048)
        old_key = paramiko.RSAKey.generate(2048)
        exc = BadHostKeyException(host, new_key, old_key)
        client = TotemSshClient(host=host, user="usinagem", password="secret")
        with _patch("totem_cli.core.ssh.paramiko.SSHClient") as MockClient:
            mock = MockClient.return_value
            mock.connect.side_effect = exc
            with self.assertRaises(HostKeyChangedError) as ctx:
                client.connect()
            self.assertEqual(ctx.exception.host, host)
            self.assertEqual(ctx.exception.fingerprint, _fingerprint(new_key))
            self.assertEqual(ctx.exception.expected_fingerprint, _fingerprint(old_key))

    def test_upload_rejects_disallowed_target(self):
        with self.assertRaises(SshClientError):
            self.client.upload_file("/tmp/x.html", "passwd")

    def test_reboot_issues_command(self):
        self.client.reboot()
        self.assertTrue(any(cmd.strip() == "reboot" for cmd in self.commands))

    def test_upload_offline_html(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as fh:
            fh.write("<html>nexus offline</html>")
            path = fh.name
        try:
            mock_sftp = MagicMock()
            self.client._client.open_sftp.return_value = mock_sftp
            dest = self.client.upload_file(path, "offline.html")
            self.assertTrue(dest.endswith("/offline.html"))
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
