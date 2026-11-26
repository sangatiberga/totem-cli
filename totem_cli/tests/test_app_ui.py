"""Unit tests for flash-screen repaint throttling and SSH host input."""
import unittest

from totem_cli.app import FLASH_UI_REFRESH_INTERVAL, SshScreen, flash_ui_refresh_due


class TestFlashUiRefreshDue(unittest.TestCase):
    def test_burst_within_interval_is_swallowed(self):
        now = 100.0
        self.assertTrue(flash_ui_refresh_due(now, 0.0, 10, -1, False, False))
        for dt in (0.01, 0.05, 0.1, 0.2, 0.3):
            self.assertFalse(
                flash_ui_refresh_due(now + dt, now, 11, 10, False, False)
            )

    def test_interval_elapsed_repaints(self):
        self.assertTrue(
            flash_ui_refresh_due(100.0 + FLASH_UI_REFRESH_INTERVAL, 100.0,
                                None, -1, False, False)
        )

    def test_write_verify_switch_repaints_immediately(self):
        self.assertTrue(flash_ui_refresh_due(100.01, 100.0, 50, 50, True, False))
        self.assertTrue(flash_ui_refresh_due(100.01, 100.0, None, -1, True, False))

    def test_milestones_repaint_but_repeats_do_not(self):
        self.assertTrue(flash_ui_refresh_due(100.01, 100.0, 100, 99, False, False))
        self.assertTrue(flash_ui_refresh_due(100.01, 100.0, 0, -1, False, False))
        self.assertFalse(flash_ui_refresh_due(100.01, 100.0, 100, 100, False, False))
        self.assertFalse(flash_ui_refresh_due(100.01, 100.0, 50, 49, False, False))


class TestSshHostSanitize(unittest.TestCase):
    def test_plain_ip_preserved(self):
        self.assertEqual(SshScreen._sanitize_ssh_host("192.168.1.1"), "192.168.1.1")

    def test_trailing_dot_preserved(self):
        self.assertEqual(SshScreen._sanitize_ssh_host("192."), "192.")

    def test_digits_only_not_truncated(self):
        self.assertEqual(SshScreen._sanitize_ssh_host("192168"), "192168")

    def test_strips_invalid_and_truncates_octets(self):
        self.assertEqual(SshScreen._sanitize_ssh_host("192a168"), "192168")
        self.assertEqual(SshScreen._sanitize_ssh_host("192.168.1111"), "192.168.111")

    def test_collapses_double_dots_and_keeps_short_octets(self):
        self.assertEqual(SshScreen._sanitize_ssh_host("1.2.3.4"), "1.2.3.4")
        self.assertEqual(SshScreen._sanitize_ssh_host("192..168"), "192.168")


if __name__ == "__main__":
    unittest.main()
