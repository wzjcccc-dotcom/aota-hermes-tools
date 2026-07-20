"""Bounded, read-only access to Hermes WebUI text attachments.

This is intentionally separate from the registered-workspace filesystem tools:
WebUI attachments are transient conversation context, never project source.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import PurePosixPath
from urllib.parse import unquote


TOOL_NAME = "aota_webui_attachment_read"
TOOLSET_NAME = "aota_webui_attachment_read"

MAX_FILE_BYTES = 1024 * 1024
DEFAULT_MAX_CHARS = 20_000
HARD_MAX_CHARS = 50_000
_DEFAULT_RUNTIME_ROOT = "/home/hermes/.hermes/webui/attachments"
_DEFAULT_WEBUI_REFERENCE_ROOT = "/home/hermeswebui/.hermes/webui/attachments"
_ALLOWED_EXTENSIONS = {".md", ".txt"}

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Read one Hermes WebUI Markdown or text attachment that was explicitly "
        "provided in the current conversation. This is read-only, does not list "
        "or search attachments, and accepts no workspace or filesystem root. "
        "Use attachment_ref from the WebUI message when available."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "attachment_ref": {
                "type": "string",
                "description": "WebUI-provided attachment reference/path for this conversation",
            },
            "attachment_path": {
                "type": "string",
                "description": "Fallback WebUI-provided attachment path; not an arbitrary filesystem path",
            },
            "max_chars": {
                "type": "integer",
                "description": f"Characters to return (default {DEFAULT_MAX_CHARS}, hard maximum {HARD_MAX_CHARS})",
            },
            "offset_chars": {
                "type": "integer",
                "description": "Zero-based Unicode character offset (default 0)",
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}


class AttachmentReadError(Exception):
    """Stable, non-path-leaking error for a rejected attachment request."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _runtime_root() -> str:
    return os.environ.get("AOTA_WEBUI_ATTACHMENT_ROOT", _DEFAULT_RUNTIME_ROOT).strip()


def _reference_roots() -> tuple[str, ...]:
    configured = os.environ.get("AOTA_WEBUI_ATTACHMENT_REF_ROOT", "").strip()
    roots = (_runtime_root(), configured or _DEFAULT_WEBUI_REFERENCE_ROOT)
    return tuple(root.rstrip("/") for root in roots if root)


def _reject_unsafe_reference(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise AttachmentReadError("ATTACHMENT_REFERENCE_REQUIRED", "attachment_ref is required")
    if "\x00" in value or "\\" in value:
        raise AttachmentReadError("ATTACHMENT_REFERENCE_INVALID", "attachment reference is invalid")
    decoded = unquote(value)
    if decoded != value:
        raise AttachmentReadError("ATTACHMENT_REFERENCE_INVALID", "encoded attachment reference is not allowed")
    if "//" in value:
        raise AttachmentReadError("ATTACHMENT_REFERENCE_INVALID", "attachment reference is not canonical")


def _relative_reference(value: str) -> tuple[str, ...]:
    _reject_unsafe_reference(value)
    for root in _reference_roots():
        if value == root:
            raise AttachmentReadError("ATTACHMENT_NOT_FILE", "attachment reference is not a file")
        prefix = root + "/"
        if value.startswith(prefix):
            relative = value[len(prefix) :]
            break
    else:
        # A relative WebUI reference is safe only when it names a child, never a root.
        if value.startswith("/"):
            raise AttachmentReadError("ATTACHMENT_ROOT_REJECTED", "attachment reference is outside the attachment root")
        relative = value

    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise AttachmentReadError("ATTACHMENT_TRAVERSAL_REJECTED", "attachment reference is invalid")
    return path.parts


def _open_regular_no_symlink(parts: tuple[str, ...]) -> tuple[int, int]:
    root_value = _runtime_root()
    root = os.path.abspath(root_value)
    if not os.path.isabs(root_value) or os.path.islink(root):
        raise AttachmentReadError("ATTACHMENT_ROOT_UNAVAILABLE", "attachment root is unavailable")
    try:
        root_stat = os.lstat(root)
    except OSError:
        raise AttachmentReadError("ATTACHMENT_ROOT_UNAVAILABLE", "attachment root is unavailable") from None
    if not stat.S_ISDIR(root_stat.st_mode):
        raise AttachmentReadError("ATTACHMENT_ROOT_UNAVAILABLE", "attachment root is unavailable")
    # This realpath/commonpath assertion protects a misconfigured env root as well
    # as documenting the containment boundary before anchored opens begin.
    root_real = os.path.realpath(root)
    candidate = os.path.join(root_real, *parts)
    if os.path.commonpath((root_real, candidate)) != root_real:
        raise AttachmentReadError("ATTACHMENT_ROOT_REJECTED", "attachment reference is outside the attachment root")

    directory_fd = None
    opened_fds: list[int] = []
    try:
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened_fds.append(directory_fd)
        for component in parts[:-1]:
            try:
                st = os.stat(component, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                raise AttachmentReadError("ATTACHMENT_NOT_FOUND", "attachment does not exist") from None
            except OSError:
                raise AttachmentReadError("ATTACHMENT_REJECTED", "attachment cannot be opened") from None
            if stat.S_ISLNK(st.st_mode):
                raise AttachmentReadError("ATTACHMENT_SYMLINK_REJECTED", "attachment path contains a symlink")
            if not stat.S_ISDIR(st.st_mode):
                raise AttachmentReadError("ATTACHMENT_NOT_FILE", "attachment reference is not a file")
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
            opened_fds.append(next_fd)
            directory_fd = next_fd

        name = parts[-1]
        try:
            entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            raise AttachmentReadError("ATTACHMENT_NOT_FOUND", "attachment does not exist") from None
        except OSError:
            raise AttachmentReadError("ATTACHMENT_REJECTED", "attachment cannot be opened") from None
        if stat.S_ISLNK(entry_stat.st_mode):
            raise AttachmentReadError("ATTACHMENT_SYMLINK_REJECTED", "attachment is a symlink")
        if not stat.S_ISREG(entry_stat.st_mode):
            raise AttachmentReadError("ATTACHMENT_NOT_FILE", "attachment is not a regular file")
        if entry_stat.st_size > MAX_FILE_BYTES:
            raise AttachmentReadError("ATTACHMENT_TOO_LARGE", "attachment exceeds the size limit")
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        final_stat = os.fstat(fd)
        if not stat.S_ISREG(final_stat.st_mode):
            os.close(fd)
            raise AttachmentReadError("ATTACHMENT_NOT_FILE", "attachment is not a regular file")
        if final_stat.st_size > MAX_FILE_BYTES:
            os.close(fd)
            raise AttachmentReadError("ATTACHMENT_TOO_LARGE", "attachment exceeds the size limit")
        return fd, final_stat.st_size
    finally:
        for fd in reversed(opened_fds):
            os.close(fd)


def _read_text(parts: tuple[str, ...]) -> tuple[str, int]:
    fd, size_bytes = _open_regular_no_symlink(parts)
    try:
        with os.fdopen(fd, "rb", closefd=True) as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
    except OSError:
        raise AttachmentReadError("ATTACHMENT_READ_FAILED", "attachment could not be read") from None
    if len(raw) > MAX_FILE_BYTES:
        raise AttachmentReadError("ATTACHMENT_TOO_LARGE", "attachment exceeds the size limit")
    if b"\x00" in raw:
        raise AttachmentReadError("ATTACHMENT_BINARY_REJECTED", "binary attachment is not supported")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise AttachmentReadError("ATTACHMENT_BINARY_REJECTED", "attachment is not valid UTF-8 text") from None
    if any(ord(char) < 32 and char not in "\n\r\t\f\b" for char in text):
        raise AttachmentReadError("ATTACHMENT_BINARY_REJECTED", "binary attachment is not supported")
    return text, size_bytes


def _positive_int(value: object, default: int, maximum: int, name: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AttachmentReadError("ATTACHMENT_ARGUMENT_INVALID", f"{name} must be a non-negative integer")
    return min(value, maximum)


def handle(args: dict, **_kwargs) -> str:
    try:
        if not isinstance(args, dict) or set(args) - {"attachment_ref", "attachment_path", "max_chars", "offset_chars"}:
            raise AttachmentReadError("ATTACHMENT_ARGUMENT_INVALID", "unknown attachment reader argument")
        attachment_ref = args.get("attachment_ref")
        attachment_path = args.get("attachment_path")
        if attachment_ref and attachment_path:
            raise AttachmentReadError("ATTACHMENT_ARGUMENT_INVALID", "provide attachment_ref or attachment_path, not both")
        reference = attachment_ref or attachment_path
        parts = _relative_reference(reference)
        extension = PurePosixPath(parts[-1]).suffix.lower()
        if extension not in _ALLOWED_EXTENSIONS:
            raise AttachmentReadError("ATTACHMENT_EXTENSION_REJECTED", "only .md and .txt attachments are supported")
        max_chars = _positive_int(args.get("max_chars"), DEFAULT_MAX_CHARS, HARD_MAX_CHARS, "max_chars")
        if max_chars == 0:
            raise AttachmentReadError("ATTACHMENT_ARGUMENT_INVALID", "max_chars must be greater than zero")
        offset_chars = _positive_int(args.get("offset_chars"), 0, 2**63 - 1, "offset_chars")
        text, size_bytes = _read_text(parts)
        total_chars = len(text)
        content = text[offset_chars : offset_chars + max_chars]
        next_offset = offset_chars + len(content)
        truncated = next_offset < total_chars
        payload = {
            "schema_version": 1,
            "attachment_name": parts[-1],
            "extension": extension,
            "size_bytes": size_bytes,
            "total_chars": total_chars,
            "offset_chars": offset_chars,
            "returned_chars": len(content),
            "truncated": truncated,
            "next_offset": next_offset if truncated else None,
            "content": content,
        }
        return json.dumps(payload, sort_keys=True)
    except AttachmentReadError as exc:
        return json.dumps({"error": exc.code, "message": exc.message}, sort_keys=True)
