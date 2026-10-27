"""External JSON configuration loading and validation."""

from dataclasses import fields
from datetime import datetime, timezone
import json
from pathlib import Path

from .mqtt_status import MQTTConfig
from . import APPLICATION_NAME
from .ha_live_preview import LiveConfig, make_device_id

from .models import DirectoryCleanupRule, UploadDirectory
from .state import STATE
from .utils import (
    normalize_relative_path,
    parse_rclone_age_cutoff,
    parse_size_to_bytes,
    validate_upload_command,
)


def load_json_config(config_path: Path) -> dict:
    """Load a JSON config file from any filename extension."""
    if not config_path.exists():
        raise FileNotFoundError(f"Config file does not exist: {config_path}")
    if not config_path.is_file():
        raise ValueError(f"Config path is not a file: {config_path}")

    try:
        with config_path.open("r", encoding="utf-8") as config_file:
            loaded_config = json.load(config_file)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in config file {config_path}: {error}") from error

    if not isinstance(loaded_config, dict):
        raise ValueError("Config root must be a JSON object")
    return loaded_config


def require_string(section_name: str, config: dict, key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{section_name}.{key} must be a non-empty string")
    return value


def optional_string(
    section_name: str,
    config: dict,
    key: str,
    default: str | None,
) -> str | None:
    if key not in config:
        return default
    value = config[key]
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{section_name}.{key} must be a non-empty string or null")
    return value


def optional_bool(section_name: str, config: dict, key: str, default: bool) -> bool:
    if key not in config:
        return default
    value = config[key]
    if not isinstance(value, bool):
        raise ValueError(f"{section_name}.{key} must be true or false")
    return value


def optional_bool_or_none(
    section_name: str,
    config: dict,
    key: str,
) -> bool | None:
    if key not in config or config[key] is None:
        return None
    value = config[key]
    if not isinstance(value, bool):
        raise ValueError(f"{section_name}.{key} must be true, false, or null")
    return value


def optional_non_negative_int(
    section_name: str,
    config: dict,
    key: str,
    default: int,
) -> int:
    if key not in config:
        return default
    value = config[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{section_name}.{key} must be a non-negative integer")
    return value


def optional_positive_int_or_none(section_name: str, config: dict, key: str) -> int | None:
    if key not in config or config[key] is None:
        return None
    value = config[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{section_name}.{key} must be a positive integer or null")
    return value


def optional_string_list(section_name: str, config: dict, key: str) -> list[str]:
    """Read an optional list of strings from a config object."""
    value = config.get(key, [])

    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{section_name}.{key} must be a list of strings")

    return value


ROOT_OPTIONS = {
    "script_name", "comment", "completion", "live", "upload_directories",
    "delete_min_age", "thread_limits", "lock_file", "delete_list_dir", "sleep_after_step",
}
THREAD_OPTIONS = {"upload_threads", "cleanup_threads", "remote_quota_cleanup_threads", "trash_cleanup_threads"}


def reject_unknown_options(section: str, raw: dict, allowed) -> None:
    """Reject unsupported keys so obsolete settings and typos cannot disable safeguards."""
    unknown = set(raw) - set(allowed)
    if unknown:
        raise ValueError(f"{section}: unknown option(s): {', '.join(sorted(unknown))}")


def _reporting_config(section: str, raw: dict, model, config_dir: Path):
    """Validate template settings and resolve TLS files beside the JSON config."""
    if not isinstance(raw, dict):
        raise ValueError(f"{section} must be an object")
    values = dict(raw)
    reject_unknown_options(section, values, (item.name for item in fields(model)))
    for key in ("ca_certs", "certfile", "keyfile"):
        value = values.get(key)
        if isinstance(value, str) and value:
            path = Path(value).expanduser()
            values[key] = str(path if path.is_absolute() else config_dir / path)
    try:
        return model(**values)
    except (ValueError, TypeError) as error:
        raise ValueError(f"{section}.{error}") from error


def parse_completion_config(config: dict, config_dir: Path = Path(".")) -> MQTTConfig:
    """Parse only the current completion section and resolve its TLS paths."""
    return _reporting_config("completion", config.get("completion", {}), MQTTConfig, config_dir)


def parse_live_config(config: dict, config_path: Path) -> LiveConfig:
    raw = config.get("live", {})
    if not isinstance(raw, dict):
        raise ValueError("live must be an object")
    raw = dict(raw)
    raw.setdefault("device_id", make_device_id(APPLICATION_NAME, str(config_path.resolve())))
    raw.setdefault("manufacturer", APPLICATION_NAME)
    return _reporting_config("live", raw, LiveConfig, config_path.resolve().parent)


def parse_cleanup_rules(
    section_name: str,
    raw_upload: dict,
) -> list[DirectoryCleanupRule]:
    """Parse cleanup_rules owned by one upload destination."""
    raw_rules = raw_upload.get("cleanup_rules", [])
    if not isinstance(raw_rules, list):
        raise ValueError(f"{section_name}.cleanup_rules must be a list")

    cleanup_rules: list[DirectoryCleanupRule] = []
    seen_paths: set[str] = set()

    for index, raw_rule in enumerate(raw_rules, start=1):
        rule_name = f"{section_name}.cleanup_rules[{index}]"
        if not isinstance(raw_rule, dict):
            raise ValueError(f"{rule_name} must be an object")

        reject_unknown_options(rule_name, raw_rule, (item.name for item in fields(DirectoryCleanupRule)))
        path = require_string(rule_name, raw_rule, "path")
        normalized_path = normalize_relative_path(path)
        if normalized_path in seen_paths:
            raise ValueError(
                f"{rule_name}.path duplicates another cleanup rule in "
                f"{section_name}: {path}"
            )
        seen_paths.add(normalized_path)

        max_files = optional_positive_int_or_none(rule_name, raw_rule, "max_files")
        max_size = optional_string(rule_name, raw_rule, "max_size", None)
        if max_size is not None:
            parse_size_to_bytes(max_size)

        cleanup_rules.append(
            DirectoryCleanupRule(
                path=path,
                max_files=max_files,
                max_size=max_size,
                delete_old_files=optional_bool_or_none(
                    rule_name, raw_rule, "delete_old_files"
                ),
                delete_excess_files=optional_bool_or_none(
                    rule_name, raw_rule, "delete_excess_files"
                ),
                delete_to_trash=optional_bool_or_none(
                    rule_name, raw_rule, "delete_to_trash"
                ),
            )
        )

    return cleanup_rules


def parse_upload_directories(config: dict) -> list[UploadDirectory]:
    """Convert upload_directories config objects into UploadDirectory instances."""
    raw_uploads = config.get("upload_directories")
    if not isinstance(raw_uploads, list) or not raw_uploads:
        raise ValueError("upload_directories must be a non-empty list")

    uploads: list[UploadDirectory] = []
    seen_remote_paths: dict[str, int] = {}
    for index, raw_upload in enumerate(raw_uploads, start=1):
        section_name = f"upload_directories[{index}]"
        if not isinstance(raw_upload, dict):
            raise ValueError(f"{section_name} must be an object")

        reject_unknown_options(section_name, raw_upload, (item.name for item in fields(UploadDirectory)))
        local_path = require_string(section_name, raw_upload, "local_path")
        remote_path = require_string(section_name, raw_upload, "remote_path")
        previous_index = seen_remote_paths.get(remote_path)
        if previous_index is not None:
            raise ValueError(
                "WARNING: duplicate upload destination detected: "
                f"{section_name}.remote_path exactly matches "
                f"upload_directories[{previous_index}].remote_path ({remote_path!r}). "
                "Refusing to continue because duplicate destinations can collide in "
                "per-remote runtime state and can run destructive operations concurrently."
            )
        seen_remote_paths[remote_path] = index

        name = optional_string(section_name, raw_upload, "name", None)
        upload_command = validate_upload_command(
            optional_string(section_name, raw_upload, "upload_command", "copy")  # type: ignore[arg-type]
        )
        delete_old_files = optional_bool(
            section_name, raw_upload, "delete_old_files", True
        )
        delete_excess_files = optional_bool(
            section_name, raw_upload, "delete_excess_files", True
        )
        max_total_size = optional_string(
            section_name, raw_upload, "max_total_size", None
        )
        delete_to_trash = optional_bool(
            section_name, raw_upload, "delete_to_trash", False
        )
        empty_trash = optional_bool(section_name, raw_upload, "empty_trash", True)
        buffer_size = optional_string(section_name, raw_upload, "buffer_size", None)
        copy_options = optional_string_list(section_name, raw_upload, "copy_options")
        cleanup_rules = parse_cleanup_rules(section_name, raw_upload)

        if max_total_size is not None:
            parse_size_to_bytes(max_total_size)
        if buffer_size is not None:
            buffer_size_bytes = parse_size_to_bytes(buffer_size)
            if buffer_size_bytes <= 0:
                raise ValueError(f"{section_name}.buffer_size must be greater than 0")

        script_managed_flags = {
            "--absolute",
            "--combined",
            "--compare-dest",
            "--copy-dest",
            "--csv",
            "--dest-after",
            "--dirs-only",
            "--dry-run",
            "--format",
            "--cutoff-mode",
            "--max-duration",
            "--max-transfer",
            "--buffer-size",
            "--no-traverse",
            "--separator",
            "-n",
        }
        for option in copy_options:
            option_name = option.split("=", 1)[0]
            if option_name in script_managed_flags:
                raise ValueError(
                    f"{section_name}.copy_options must not contain {option_name}; "
                    "the application reserves this flag for its own safety/runtime behavior"
                )

        if (
            upload_command == "sync"
            and delete_excess_files
            and max_total_size is not None
            and any(option.split("=", 1)[0] == "--delete-excluded" for option in copy_options)
        ):
            raise ValueError(
                f"{section_name}.copy_options must not contain --delete-excluded when "
                "quota-managed sync is enabled; the generated complete-file upload list "
                "intentionally excludes over-budget files and --delete-excluded could "
                "delete those excluded paths from the destination"
            )

        uploads.append(
            UploadDirectory(
                local_path=local_path,
                remote_path=remote_path,
                copy_options=copy_options,
                cleanup_rules=cleanup_rules,
                name=name,
                buffer_size=buffer_size,
                upload_command=upload_command,
                delete_old_files=delete_old_files,
                delete_excess_files=delete_excess_files,
                max_total_size=max_total_size,
                delete_to_trash=delete_to_trash,
                empty_trash=empty_trash,
            )
        )

    return uploads


def load_config(config_path_text: str):
    """Load the external JSON configuration into shared runtime state."""
    config_path = Path(config_path_text).expanduser()
    config = load_json_config(config_path)

    reject_unknown_options("root", config, ROOT_OPTIONS)

    uploads = parse_upload_directories(config)
    script_name = optional_string(
        "root", config, "script_name", "rclone-multithreaded-upload"
    )
    if script_name is None:
        raise ValueError("root.script_name must be a non-empty string")
    completion_config = parse_completion_config(config, config_path.resolve().parent)
    live_config = parse_live_config(config, config_path)
    if live_config.enabled:
        from .transfer_stats import REDIRECT_OPTIONS
        from .reservation import split_copy_options
        for upload in uploads:
            for option in split_copy_options(upload)[1]:
                if option.split("=", 1)[0] in REDIRECT_OPTIONS:
                    raise ValueError(f"{option.split('=', 1)[0]} redirects transfer stats; remove it when live.enabled=true")
    comment = config.get("comment", "")
    if not isinstance(comment, str):
        raise ValueError("root.comment must be a string")
    delete_min_age = optional_string(
        "root", config, "delete_min_age", STATE.delete_min_age
    )
    assert delete_min_age is not None
    try:
        parse_rclone_age_cutoff(delete_min_age, datetime.now(timezone.utc))
    except ValueError as error:
        raise ValueError(f"root.delete_min_age is invalid: {error}") from error

    thread_limits = config.get("thread_limits", {})
    if not isinstance(thread_limits, dict):
        raise ValueError("thread_limits must be an object")

    reject_unknown_options("thread_limits", thread_limits, THREAD_OPTIONS)

    upload_threads = optional_non_negative_int(
        "thread_limits", thread_limits, "upload_threads", STATE.upload_threads
    )
    cleanup_threads = optional_non_negative_int(
        "thread_limits", thread_limits, "cleanup_threads", STATE.cleanup_threads
    )
    remote_quota_cleanup_threads = optional_non_negative_int(
        "thread_limits",
        thread_limits,
        "remote_quota_cleanup_threads",
        STATE.remote_quota_cleanup_threads,
    )
    trash_cleanup_threads = optional_non_negative_int(
        "thread_limits",
        thread_limits,
        "trash_cleanup_threads",
        STATE.trash_cleanup_threads,
    )

    if upload_threads < 1:
        raise ValueError("thread_limits.upload_threads must be at least 1")
    if cleanup_threads < 1:
        raise ValueError("thread_limits.cleanup_threads must be at least 1")
    if remote_quota_cleanup_threads < 1:
        raise ValueError(
            "thread_limits.remote_quota_cleanup_threads must be at least 1"
        )
    if trash_cleanup_threads < 1:
        raise ValueError("thread_limits.trash_cleanup_threads must be at least 1")

    lock_file_text = optional_string("root", config, "lock_file", str(STATE.lock_file))
    delete_list_dir_text = optional_string(
        "root", config, "delete_list_dir", str(STATE.delete_list_dir)
    )
    assert lock_file_text is not None
    assert delete_list_dir_text is not None

    STATE.script_name = script_name
    STATE.completion = completion_config
    STATE.live_config = live_config
    STATE.comment = comment
    STATE.upload_directories = uploads
    STATE.delete_min_age = delete_min_age
    STATE.upload_threads = upload_threads
    STATE.cleanup_threads = cleanup_threads
    STATE.remote_quota_cleanup_threads = remote_quota_cleanup_threads
    STATE.trash_cleanup_threads = trash_cleanup_threads
    STATE.lock_file = Path(lock_file_text).expanduser()
    STATE.delete_list_dir = Path(delete_list_dir_text).expanduser()
    STATE.sleep_after_step = optional_non_negative_int(
        "root", config, "sleep_after_step", STATE.sleep_after_step
    )
    STATE.config_path = config_path
