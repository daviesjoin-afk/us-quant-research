from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
from pathlib import Path
import tempfile


class CredentialStoreError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


_CRYPTPROTECT_UI_FORBIDDEN = 0x01
_ENTROPY = b"USQuantResearch:finnhub:v1"


class WindowsCredentialStore:
    """Current-user DPAPI encrypted local credential blobs."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save_secret(self, name: str, secret: str) -> Path:
        cleaned_name = _clean_name(name)
        if not secret:
            raise ValueError("secret cannot be empty")
        encrypted = _protect(secret.encode("utf-8"))
        target = self.root / f"{cleaned_name}.dpapi"
        content = base64.b64encode(encrypted)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            temporary_path = Path(temporary.name)
        temporary_path.replace(target)
        return target

    def load_secret(self, name: str) -> str | None:
        target = self.root / f"{_clean_name(name)}.dpapi"
        if not target.exists():
            return None
        try:
            encrypted = base64.b64decode(
                target.read_bytes(), validate=True
            )
            return _unprotect(encrypted).decode("utf-8")
        except Exception as error:
            raise CredentialStoreError(
                "Windows 加密凭据无法读取；可能属于其他用户"
            ) from error

    def delete_secret(self, name: str) -> None:
        target = self.root / f"{_clean_name(name)}.dpapi"
        if target.exists():
            target.unlink()


def _clean_name(name: str) -> str:
    cleaned = name.strip().lower()
    if not cleaned or not all(
        character.isalnum() or character in {"_", "-"}
        for character in cleaned
    ):
        raise ValueError("invalid credential name")
    return cleaned


def _protect(data: bytes) -> bytes:
    return _crypt(data, decrypt=False)


def _unprotect(data: bytes) -> bytes:
    return _crypt(data, decrypt=True)


def _crypt(data: bytes, *, decrypt: bool) -> bytes:
    if not hasattr(ctypes, "windll"):
        raise CredentialStoreError(
            "DPAPI credential storage is only available on Windows"
        )
    input_blob, input_buffer = _blob(data)
    entropy_blob, entropy_buffer = _blob(_ENTROPY)
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    if decrypt:
        succeeded = crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            None,
            ctypes.byref(entropy_blob),
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_blob),
        )
    else:
        succeeded = crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            None,
            ctypes.byref(entropy_blob),
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_blob),
        )
    if not succeeded:
        raise CredentialStoreError(
            f"Windows DPAPI error {ctypes.GetLastError()}"
        )
    try:
        # Keep the input buffers alive until DPAPI has returned.
        del input_buffer, entropy_buffer
        return ctypes.string_at(
            output_blob.pbData, output_blob.cbData
        )
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    blob = _DataBlob(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    return blob, buffer
