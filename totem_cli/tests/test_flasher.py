"""Unit tests for the flasher module."""
import hashlib
import lzma
import os
import tempfile
import unittest
from totem_cli.core.flasher import flash_image, FlasherError


class TestFlasher(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_flash_raw_image_with_sha256_verification(self):
        payload = b"Totem Linux Raspberry Pi 5 Raw Image Payload" * 1024
        expected_hash = hashlib.sha256(payload).hexdigest()

        src_path = os.path.join(self.temp_dir.name, "test.img")
        dest_path = os.path.join(self.temp_dir.name, "dest_device.img")

        with open(src_path, "wb") as f:
            f.write(payload)

        progress_calls = []

        def on_progress(written, total):
            progress_calls.append((written, total))

        calc_hash = flash_image(
            src_path,
            dest_path,
            progress_callback=on_progress,
            verify_sha256=expected_hash,
            chunk_size=1024,
        )

        self.assertEqual(calc_hash, expected_hash)
        self.assertTrue(len(progress_calls) > 0)
        with open(dest_path, "rb") as f:
            written_content = f.read()
        self.assertEqual(written_content, payload)

    def test_flash_xz_compressed_image(self):
        payload = b"Compressed Totem Factory OS Payload" * 2048
        expected_hash = hashlib.sha256(payload).hexdigest()

        src_path = os.path.join(self.temp_dir.name, "test.img.xz")
        dest_path = os.path.join(self.temp_dir.name, "dest_device.img")

        # Compress into .xz format
        with lzma.open(src_path, "wb") as f:
            f.write(payload)

        progress_calls = []

        def on_progress(written, total):
            progress_calls.append(written)

        calc_hash = flash_image(
            src_path,
            dest_path,
            progress_callback=on_progress,
            verify_sha256=expected_hash,
            chunk_size=512,
        )

        self.assertEqual(calc_hash, expected_hash)
        self.assertTrue(len(progress_calls) > 0)
        with open(dest_path, "rb") as f:
            self.assertEqual(f.read(), payload)

    def test_fails_on_hash_mismatch(self):
        payload = b"Arbitrary payload"
        src_path = os.path.join(self.temp_dir.name, "bad.img")
        dest_path = os.path.join(self.temp_dir.name, "bad_dest.img")

        with open(src_path, "wb") as f:
            f.write(payload)

        with self.assertRaises(FlasherError) as ctx:
            flash_image(src_path, dest_path, verify_sha256="0000000000000000000000000000000000000000000000000000000000000000")

        self.assertIn("Falha na verificação de integridade", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
