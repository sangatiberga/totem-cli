"""Streaming flasher with real-time .xz decompression and SHA-256 verification."""
from __future__ import annotations

import hashlib
import lzma
import os
from typing import Callable, Optional

DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024  # 4MB blocks for fast block device I/O


class FlasherError(Exception):
    """Raised when an error occurs during the flashing process."""


def flash_image(
    image_path: str,
    target_device: str,
    progress_callback: Optional[Callable[[int, Optional[int]], None]] = None,
    verify_sha256: Optional[str] = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> str:
    """Flash a raw .img or compressed .img.xz to a target device/file.

    Args:
        image_path: Path to the input image (.img or .img.xz).
        target_device: Path to the target block device or output file.
        progress_callback: Optional callback receiving (bytes_written, total_source_bytes).
        verify_sha256: Expected SHA-256 hash of the written uncompressed stream.
        chunk_size: Size of read/write chunks (default 4MB).

    Returns:
        The calculated SHA-256 hash of the written uncompressed stream.
    """
    if not os.path.exists(image_path):
        raise FlasherError(f"Arquivo de imagem não encontrado: {image_path}")

    is_xz = image_path.endswith(".xz")
    source_total_bytes = os.path.getsize(image_path) if os.path.isfile(image_path) else None

    hasher = hashlib.sha256()
    bytes_written = 0

    try:
        with open(target_device, "wb") as dest:
            if is_xz:
                with open(image_path, "rb") as src:
                    decompressor = lzma.LZMADecompressor()
                    while True:
                        compressed_chunk = src.read(chunk_size)
                        if not compressed_chunk:
                            break
                        uncompressed_chunk = decompressor.decompress(compressed_chunk)
                        if uncompressed_chunk:
                            dest.write(uncompressed_chunk)
                            hasher.update(uncompressed_chunk)
                            bytes_written += len(uncompressed_chunk)
                            if progress_callback:
                                progress_callback(bytes_written, None)
                    # Flush any remaining uncompressed data
                    try:
                        rest = decompressor.flush()
                        if rest:
                            dest.write(rest)
                            hasher.update(rest)
                            bytes_written += len(rest)
                    except Exception:
                        pass
            else:
                with open(image_path, "rb") as src:
                    while True:
                        chunk = src.read(chunk_size)
                        if not chunk:
                            break
                        dest.write(chunk)
                        hasher.update(chunk)
                        bytes_written += len(chunk)
                        if progress_callback:
                            progress_callback(bytes_written, source_total_bytes)

            dest.flush()
            os.fsync(dest.fileno())
    except PermissionError as pe:
        raise FlasherError(
            f"Permissão negada ao gravar em {target_device}. "
            "Certifique-se de executar com privilégios de Administrador ou sudo."
        ) from pe
    except Exception as e:
        raise FlasherError(f"Falha durante a gravação da imagem: {e}") from e

    calculated_hash = hasher.hexdigest().lower()
    if verify_sha256 and calculated_hash != verify_sha256.strip().lower():
        raise FlasherError(
            f"Falha na verificação de integridade SHA-256. "
            f"Esperado: {verify_sha256.lower()}, Calculado: {calculated_hash}"
        )

    return calculated_hash
