"""Unit tests for frozen-aware resource paths."""
import unittest

from totem_cli.resources import package_dir


class TestResources(unittest.TestCase):
    def test_package_dir_resolves_templates_and_assets(self):
        base = package_dir()
        self.assertTrue((base / "templates" / "firstboot.sh.j2").is_file())
        self.assertTrue((base / "assets" / "ascii-art.txt").is_file())


if __name__ == "__main__":
    unittest.main()
