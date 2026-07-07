"""Pure parsing, path, time, and formatting helpers."""

from datetime import datetime, timezone

from .models import RemoteFile, RemoteQuotaFile


ALLOWED_UPLOAD_COMMANDS = {"copy", "sync", "move"}


def parse_size_to_bytes(size_text: str) -> int:
    """Convert sizes like 500M, 50G, and 1T into binary bytes."""
    size_text = size_text.strip().upper()
    units = {
        "B": 1,
        "K": 1024,
        "KB": 1024,
        "M": 1024**2,
        "MB": 1024**2,
        "G": 1024**3,
        "GB": 1024**3,
        "T": 1024**4,
        "TB": 1024**4,
    }

    number_part = ""
    unit_part = ""
    for char in size_text:
        if char.isdigit() or char == ".":
            number_part += char
        else:
            unit_part += char

    if not number_part:
        raise ValueError(f"Invalid size: {size_text}")
    if not unit_part:
        unit_part = "B"
    if unit_part not in units:
        raise ValueError(f"Invalid size unit: {unit_part}")

    return int(float(number_part) * units[unit_part])


def validate_upload_command(command: str) -> str:
    command = command.strip().lower()
    if command not in ALLOWED_UPLOAD_COMMANDS:
        raise ValueError(
            f"Invalid upload_command '{command}'. "
            f"Allowed commands: {', '.join(sorted(ALLOWED_UPLOAD_COMMANDS))}"
        )
    return command


def remote_name_from_path(remote_path: str) -> str:
    safe_name = remote_path.replace(":", "_")
    safe_name = safe_name.replace("/", "_")
    return safe_name.strip("_")


def join_rclone_remote_path(remote_root: str, relative_path: str) -> str:
    remote_root = remote_root.rstrip("/")
    relative_path = relative_path.strip("/")
    if not relative_path:
        return f"{remote_root}/"
    return f"{remote_root}/{relative_path}"


def join_relative_path(base: str, child: str) -> str:
    base = base.strip("/")
    child = child.strip("/")
    if not base:
        return child
    if not child:
        return base
    return f"{base}/{child}"


def normalize_relative_path(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def parse_rclone_modtime(modified: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(modified.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"Invalid rclone ModTime {modified!r}: {error}") from error

    if parsed.tzinfo is None:
        raise ValueError(f"rclone ModTime has no timezone offset: {modified!r}")
    return parsed.astimezone(timezone.utc)


def remote_file_oldest_sort_key(
    file: RemoteFile | RemoteQuotaFile,
) -> tuple[datetime, str]:
    return parse_rclone_modtime(file.modified), file.path


def format_bytes(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{size_bytes} B"
