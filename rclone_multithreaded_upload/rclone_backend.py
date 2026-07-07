"""rclone backend detection and backend-specific hard-delete flags."""

from functools import lru_cache
import json
import re

from .commands import run_command
from .models import CleanupTarget, UploadDirectory


@lru_cache(maxsize=1)
def get_rclone_config_dump() -> dict:
    """Read rclone's configured remotes as JSON without printing its content."""
    result = run_command(["rclone", "config", "dump"], capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown rclone config dump error").strip()
        raise RuntimeError(f"Failed reading rclone config for backend detection: {detail}")

    try:
        config = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"rclone config dump returned invalid JSON: {error}") from error
    if not isinstance(config, dict):
        raise RuntimeError("rclone config dump did not return a JSON object")
    return config


@lru_cache(maxsize=64)
def resolve_underlying_backend_type(remote_path: str) -> str | None:
    """Resolve wrappers such as crypt to the configured storage backend."""
    if ":" not in remote_path:
        return None

    remote_name = remote_path.split(":", 1)[0]
    config = get_rclone_config_dump()
    seen: set[str] = set()
    wrapper_types = {"alias", "chunker", "compress", "crypt", "hasher"}

    for _ in range(16):
        if remote_name in seen:
            raise RuntimeError(f"rclone remote wrapper loop detected at: {remote_name}")
        seen.add(remote_name)
        remote_config = config.get(remote_name)
        if not isinstance(remote_config, dict):
            return None

        backend_type = remote_config.get("type")
        if not isinstance(backend_type, str) or not backend_type:
            return None
        backend_type = backend_type.lower()
        if backend_type not in wrapper_types:
            return backend_type

        wrapped_remote = remote_config.get("remote")
        if not isinstance(wrapped_remote, str) or not wrapped_remote:
            return backend_type
        if wrapped_remote.startswith(":"):
            connection = wrapped_remote[1:]
            backend_name = re.split(r"[:,]", connection, maxsplit=1)[0]
            return backend_name.lower() if backend_name else None
        if ":" not in wrapped_remote:
            return None
        remote_name = wrapped_remote.split(":", 1)[0]

    raise RuntimeError(f"Too many rclone wrapper layers while resolving: {remote_path}")


def get_delete_mode_options(
    target: CleanupTarget | UploadDirectory,
) -> list[str]:
    """Return a verified backend-specific hard-delete option when requested."""
    if target.delete_to_trash:
        return []

    remote_path = target.path if isinstance(target, CleanupTarget) else target.remote_path
    backend_type = resolve_underlying_backend_type(remote_path)
    hard_delete_options = {
        "drive": ["--drive-use-trash=false"],
        "mega": ["--mega-hard-delete"],
        "onedrive": ["--onedrive-hard-delete"],
    }
    return hard_delete_options.get(backend_type or "", [])


def get_delete_mode_text(target: CleanupTarget | UploadDirectory) -> str:
    if target.delete_to_trash:
        return "trash/backend default"
    return "hard/direct delete requested where backend supports it"
