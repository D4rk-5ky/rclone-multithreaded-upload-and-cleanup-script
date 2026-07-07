#!/usr/bin/env python3

# =============================================================================
# rclone-multithreaded-upload
# =============================================================================
#
# External-config version.
#
# Execution order:
#   1. Load and validate JSON config.
#   2. Build cleanup targets from each upload destination's own cleanup_rules.
#   3. PRE-UPLOAD per-rule cleanup.
#   4. Empty trash/recycle bin where configured.
#   5. For each remote independently, use rclone size on the filtered local
#      upload source, compare local bytes + current managed remote bytes against
#      max_total_size, and delete oldest managed remote files until enough space
#      has been freed for the complete filtered local upload size.
#   6. Upload using copy, sync, or move.
#   7. POST-UPLOAD per-rule cleanup, even if an upload partially failed.
#   8. POST-UPLOAD max_total_size cleanup.
#   9. Empty trash/recycle bin where configured.
#  10. Verify final max_files/max_size/max_total_size limits.
#  11. Print FINAL RUN RESULT with per-remote stages and captured command errors.
#
# Important design:
#   - upload_directories controls source, destination, rclone upload command,
#     upload options, and the total managed-remote max_total_size.
#   - cleanup_rules are scoped to exactly one upload_directories entry.
#   - cleanup_rules can independently limit one remote folder with max_files
#     and/or max_size.
#   - cleanup_rules run before and after the upload.
#   - max_total_size is also enforced before and after the upload.
#   - pre-upload reservation deliberately uses the complete filtered local source
#     size rather than a destination comparison.
#   - remote-wide reservation/max_total_size cleanup reads one recursive rclone
#     lsjson array for the upload remote. Python filters it to cleanup_rules,
#     parses Path/Size/ModTime, sorts files oldest-first, selects complete files
#     until selected bytes are at least the exact calculated deficit, writes one
#     --files-from list, and runs one rclone delete command for that pass.
#   - there is no identical-file protection: reservation deletion is based only
#     on managed remote file age and the number of bytes that must be freed.
#
# WARNING:
#   This script can delete files.
#   Test with safe data and/or an rclone test remote before production use.
# =============================================================================

import argparse
import atexit
import json
from functools import lru_cache
import os
import re
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import threading

from rclone_multithreaded_upload.models import (
    CleanupTarget,
    DirectoryCleanupRule,
    RemoteFile,
    RemoteQuotaFile,
    RemoteRunResult,
    StageRunResult,
    UploadDirectory,
)


VERSION = "0.0.17"


# =============================================================================
# Configuration loaded from external JSON file
# =============================================================================

# Runtime configuration is intentionally populated from --config/-c.
# The script no longer contains upload destinations or cleanup folders as
# hard-coded settings. This keeps private paths/remotes out of the script and
# makes it safer to reuse the same script with different config files.
UPLOAD_DIRECTORIES: list[UploadDirectory] = []
CONFIG_PATH: Path | None = None


# Age used by the optional delete_old_files step.
DELETE_MIN_AGE = "31d"


# Allowed rclone upload commands.
#
# This is intentionally restricted so dangerous commands like delete/purge
# cannot accidentally be used as an upload command.
ALLOWED_UPLOAD_COMMANDS = {
    "copy",
    "sync",
    "move",
}


# Thread limits for the cleanup, trash, quota, and upload phases.
UPLOAD_THREADS = 2
CLEANUP_THREADS = 2
REMOTE_QUOTA_CLEANUP_THREADS = 2
TRASH_CLEANUP_THREADS = 2


# Shared output lock.
# Without this, multiple threads could print at the same time and make logs unreadable.
OUTPUT_LOCK = threading.Lock()
OUTPUT_SEPARATOR = "=" * 80


# Lock file prevents multiple copies of this script from running at once.
LOCK_FILE = Path("/var/lock/subsys/RcloneLockFile.run")


# Temporary folder for generated --files-from lists.
DELETE_LIST_DIR = Path("/root/rclone")


# Small delay between the cleanup phase and the upload phase.
SLEEP_AFTER_STEP = 5


# Keep a small byte-level safety margin below max_total_size during pre-upload
# reservation. This avoids accepting an exact quota boundary where one byte of
# max-transfer headroom, metadata timing, or a live file-size change can make the
# verification fail immediately after the first reservation cleanup pass.
RESERVATION_SAFETY_HEADROOM_BYTES = 1 * 1024 ** 2


# A live source can grow while reservation is running. Re-read the filtered local
# size after each cleanup pass so a small increase causes another oldest-file
# cleanup pass instead of an under-sized reservation.
MAX_RESERVATION_CLEANUP_PASSES = 10


# Tracks whether this process successfully created the lock file.
lock_created = False


# Bytes reserved for each remote's real upload. The real rclone command is capped
# at this amount so files appearing or growing after the size check cannot consume
# unreserved quota. A larger live source can therefore fail safely and retry next run.
RESERVED_UPLOAD_BYTES: dict[str, int] = {}
RESERVED_UPLOAD_BYTES_LOCK = threading.Lock()

# Per-remote final result state. Worker threads update this through helpers below.
RUN_RESULTS: dict[str, RemoteRunResult] = {}
RUN_RESULTS_LOCK = threading.Lock()


# =============================================================================
# General helper functions
# =============================================================================

def print_step(message: str):
    print(f"\n  {message}\n", flush=True)


def print_error(message: str):
    print(f"\n      ERROR: {message}\n", flush=True)


def print_job_block(job_type: str, job_number: int, target: str, message: str):
    """
    Print output from threaded jobs without mixing lines between threads.
    """
    message = message.rstrip()

    if not message:
        return

    with OUTPUT_LOCK:
        print()
        print(OUTPUT_SEPARATOR)
        print(f"{job_type} {job_number}")
        print(f"Target: {target}")
        print(OUTPUT_SEPARATOR)
        print(message)
        print(OUTPUT_SEPARATOR)
        print(flush=True)


def run_command(command: list[str], capture_output: bool = False) -> subprocess.CompletedProcess:
    """
    Runs a command safely without shell=True.
    """
    print(f"Running command: {' '.join(command)}", flush=True)

    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
    )




def parse_size_to_bytes(size_text: str) -> int:
    """
    Convert sizes like 500M, 50G, 1T into bytes.
    """
    size_text = size_text.strip().upper()

    units = {
        "B": 1,
        "K": 1024,
        "KB": 1024,
        "M": 1024 ** 2,
        "MB": 1024 ** 2,
        "G": 1024 ** 3,
        "GB": 1024 ** 3,
        "T": 1024 ** 4,
        "TB": 1024 ** 4,
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


def is_directory_not_found(result: subprocess.CompletedProcess) -> bool:
    """
    Detect common rclone missing-directory errors.
    """
    output = ""

    if result.stdout:
        output += result.stdout

    if result.stderr:
        output += result.stderr

    return "directory not found" in output.lower()


def remote_name_from_path(remote_path: str) -> str:
    """
    Make a safe filename from the full rclone remote path.

    Example:
      Example-Mega-Encrypted:/CCTV/Home/Camera01

    Becomes:
      Example-Mega-Encrypted__CCTV_Home_Camera01
    """
    safe_name = remote_path.replace(":", "_")
    safe_name = safe_name.replace("/", "_")
    safe_name = safe_name.strip("_")

    return safe_name


def join_rclone_remote_path(remote_root: str, relative_path: str) -> str:
    """
    Join one rclone remote root with a path relative to that root.

    A relative path of "/" means the remote root itself.
    """
    remote_root = remote_root.rstrip("/")
    relative_path = relative_path.strip("/")

    if not relative_path:
        return f"{remote_root}/"

    return f"{remote_root}/{relative_path}"

def join_relative_path(base: str, child: str) -> str:
    """
    Join relative paths for --files-from and path comparisons.
    """
    base = base.strip("/")
    child = child.strip("/")

    if not base:
        return child

    if not child:
        return base

    return f"{base}/{child}"

def normalize_relative_path(path: str) -> str:
    """Normalize an rclone-relative path for equality comparisons."""
    return path.replace("\\", "/").strip("/")


def parse_rclone_modtime(modified: str) -> datetime:
    """Parse rclone lsjson RFC3339 ModTime for true chronological sorting."""
    try:
        parsed = datetime.fromisoformat(modified.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"Invalid rclone ModTime {modified!r}: {error}") from error

    if parsed.tzinfo is None:
        raise ValueError(f"rclone ModTime has no timezone offset: {modified!r}")

    return parsed.astimezone(timezone.utc)


def remote_file_oldest_sort_key(file: RemoteFile | RemoteQuotaFile) -> tuple[datetime, str]:
    """Sort remote files by real UTC modification time, then relative path."""
    return parse_rclone_modtime(file.modified), file.path


def format_bytes(size_bytes: int) -> str:
    """Return a readable binary-size string."""
    value = float(size_bytes)

    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.2f} {unit}"
        value /= 1024

    return f"{size_bytes} B"


def initialize_run_results():
    """Create fresh per-remote stage results for the current run."""
    with RUN_RESULTS_LOCK:
        RUN_RESULTS.clear()
        for upload in UPLOAD_DIRECTORIES:
            display_name = upload.name or upload.remote_path
            RUN_RESULTS[upload.remote_path] = RemoteRunResult(
                name=display_name,
                remote_path=upload.remote_path,
            )


def get_stage_result(remote_path: str, stage_name: str) -> StageRunResult:
    result = RUN_RESULTS[remote_path]
    return getattr(result, stage_name)


def record_stage_success(remote_path: str, stage_name: str):
    """Mark a stage successful unless a failure was already recorded."""
    with RUN_RESULTS_LOCK:
        stage = get_stage_result(remote_path, stage_name)
        if stage.status != "FAILED":
            stage.status = "SUCCESS"


def record_stage_failure(remote_path: str, stage_name: str, error: str):
    """Mark a stage failed and retain the error for FINAL RUN RESULT."""
    cleaned = (error or "Unknown failure").strip()
    with RUN_RESULTS_LOCK:
        stage = get_stage_result(remote_path, stage_name)
        stage.status = "FAILED"
        if cleaned and cleaned not in stage.errors:
            stage.errors.append(cleaned)


def record_stage_skipped(remote_path: str, stage_name: str):
    """Mark a stage skipped only when it has not already run or failed."""
    with RUN_RESULTS_LOCK:
        stage = get_stage_result(remote_path, stage_name)
        if stage.status == "PENDING":
            stage.status = "SKIPPED"


def finalize_stage_for_all(stage_name: str):
    """Turn still-pending aggregate stages into SUCCESS after their phase ends."""
    with RUN_RESULTS_LOCK:
        for result in RUN_RESULTS.values():
            stage = getattr(result, stage_name)
            if stage.status == "PENDING":
                stage.status = "SUCCESS"


def mark_pending_stages_skipped():
    """Used when a global pre-upload safety phase aborts the run."""
    with RUN_RESULTS_LOCK:
        for result in RUN_RESULTS.values():
            for stage_name in ("reservation", "upload", "post_cleanup", "final_quota"):
                stage = getattr(result, stage_name)
                if stage.status == "PENDING":
                    stage.status = "SKIPPED"


def command_error_summary(output: str, fallback: str = "No command error text was captured") -> str:
    """
    Keep error-bearing command lines for the final statistics block.

    Upload stdout/stderr is merged for live threaded output. The final summary
    keeps lines that look like failures; if none match, the last 20 non-empty
    command lines are retained so a non-zero command never loses its context.
    """
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    if not lines:
        return fallback

    error_pattern = re.compile(
        r"(?:error|failed|failure|fatal|quota|denied|forbidden|unauthorized|"
        r"not found|timeout|timed out|connection|cannot|could not|unable|"
        r"rate limit|exceeded|no space)",
        re.IGNORECASE,
    )
    selected = [line for line in lines if error_pattern.search(line)]
    if not selected:
        selected = lines[-20:]

    # Preserve order while removing exact duplicates from retry/stat output.
    deduplicated: list[str] = []
    seen: set[str] = set()
    for line in selected:
        if line in seen:
            continue
        seen.add(line)
        deduplicated.append(line)

    return "\n".join(deduplicated[-40:])


def remote_result_label(result: RemoteRunResult) -> str:
    statuses = [
        result.reservation.status,
        result.upload.status,
        result.post_cleanup.status,
        result.final_quota.status,
    ]
    if "FAILED" in statuses:
        return "FAILED"
    if all(status == "SUCCESS" for status in statuses):
        return "SUCCESS"
    return "SKIPPED"


def print_final_run_result(exit_code: int):
    """Print the requested final per-remote statistics and captured failures."""
    with OUTPUT_LOCK, RUN_RESULTS_LOCK:
        print()
        print(OUTPUT_SEPARATOR)
        print("FINAL RUN RESULT")
        print(OUTPUT_SEPARATOR)

        failed_remotes: list[str] = []

        for result in RUN_RESULTS.values():
            result_label = remote_result_label(result)
            if result_label == "FAILED":
                failed_remotes.append(result.remote_path)

            print()
            print(result.name)
            print(f"  Reservation : {result.reservation.status}")
            print(f"  Upload      : {result.upload.status}")
            print(f"  Post cleanup: {result.post_cleanup.status}")
            print(f"  Final quota : {result.final_quota.status}")
            print(f"  RESULT      : {result_label}")

            for label, stage in (
                ("Reservation", result.reservation),
                ("Upload", result.upload),
                ("Post cleanup", result.post_cleanup),
                ("Final quota", result.final_quota),
            ):
                if stage.status != "FAILED":
                    continue
                print(f"  {label} error:")
                for error in stage.errors:
                    for line in error.splitlines():
                        print(f"    {line}")

        print()
        print("-" * 80)
        print()
        print(f"OVERALL RESULT: {'SUCCESS' if exit_code == 0 else 'FAILED'}")
        print()
        print("Failed remotes:")
        if failed_remotes:
            for remote_path in failed_remotes:
                print(f"  {remote_path}")
        else:
            print("  (none)")
        print()
        print(f"Exit code: {exit_code}")
        print(OUTPUT_SEPARATOR)
        print(flush=True)


@lru_cache(maxsize=1)
def get_rclone_config_dump() -> dict:
    """
    Read rclone's configured remotes as JSON.

    The captured JSON is kept in memory and is never printed because it may
    contain obscured credentials or tokens.
    """
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
    """
    Resolve wrappers such as crypt to the configured storage backend.

    The user's destinations are crypt remotes, so delete_to_trash=False must be
    translated to the hard-delete option of the wrapped Drive/Mega/OneDrive
    backend rather than assuming every remote uses the Google Drive option.
    """
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

        # On-the-fly backend connection strings can look like :drive,option=x:path.
        if wrapped_remote.startswith(":"):
            connection = wrapped_remote[1:]
            backend_name = re.split(r"[:,]", connection, maxsplit=1)[0]
            return backend_name.lower() if backend_name else None

        if ":" not in wrapped_remote:
            return None

        remote_name = wrapped_remote.split(":", 1)[0]

    raise RuntimeError(f"Too many rclone wrapper layers while resolving: {remote_path}")


def get_delete_mode_options(target: CleanupTarget | UploadDirectory) -> list[str]:
    """
    Return the backend-specific hard-delete option when delete_to_trash=False.

    Supported explicit hard-delete mappings used by this script:
      drive    -> --drive-use-trash=false
      mega     -> --mega-hard-delete
      onedrive -> --onedrive-hard-delete

    Other backends keep their normal delete behavior because the script does
    not invent an unsafe backend flag it cannot verify.
    """
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

# =============================================================================
# Config file loading
# =============================================================================

def parse_cli_args() -> argparse.Namespace:
    """
    Parse command-line arguments.

    The config file is required so the script never silently runs with embedded
    example remotes or old hard-coded paths.
    """
    parser = argparse.ArgumentParser(
        description="Upload CCTV files to rclone remotes and clean managed remote folders.",
    )

    parser.add_argument(
        "-c",
        "--config",
        required=True,
        help="Path to JSON config file. The filename extension is not enforced.",
    )

    parser.add_argument(
        "--validate-config",
        action="store_true",
        help="Load the config, print the startup summary, and exit without lock/rclone commands.",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {VERSION}",
    )

    return parser.parse_args()


def load_json_config(config_path: Path) -> dict:
    """
    Load a JSON config file from any filename extension.
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Config file does not exist: {config_path}")

    if not config_path.is_file():
        raise ValueError(f"Config path is not a file: {config_path}")

    try:
        with config_path.open("r", encoding="utf-8") as config_file:
            loaded_config = json.load(config_file)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid JSON config in {config_path}: "
            f"line {error.lineno}, column {error.colno}: {error.msg}"
        ) from error

    if not isinstance(loaded_config, dict):
        raise ValueError("Config root must be a JSON object")

    return loaded_config


def require_string(section_name: str, data: dict, key: str) -> str:
    """
    Read a required string from a config object.
    """
    value = data.get(key)

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{section_name}.{key} must be a non-empty string")

    return value


def optional_string(section_name: str, data: dict, key: str, default: str | None) -> str | None:
    """
    Read an optional string or null from a config object.
    """
    value = data.get(key, default)

    if value is None:
        return None

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{section_name}.{key} must be a non-empty string or null")

    return value


def optional_bool(section_name: str, data: dict, key: str, default: bool) -> bool:
    """
    Read an optional boolean from a config object.
    """
    value = data.get(key, default)

    if not isinstance(value, bool):
        raise ValueError(f"{section_name}.{key} must be true or false")

    return value


def optional_bool_or_none(section_name: str, data: dict, key: str) -> bool | None:
    """Read an optional boolean or null. None means inherit."""
    value = data.get(key)

    if value is None:
        return None

    if not isinstance(value, bool):
        raise ValueError(f"{section_name}.{key} must be true, false, or null")

    return value


def optional_non_negative_int(section_name: str, data: dict, key: str, default: int) -> int:
    """
    Read an optional non-negative integer from a config object.
    """
    value = data.get(key, default)

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{section_name}.{key} must be a non-negative integer")

    return value


def optional_positive_int_or_none(section_name: str, data: dict, key: str) -> int | None:
    """
    Read an optional positive integer or null from a config object.
    """
    value = data.get(key)

    if value is None:
        return None

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{section_name}.{key} must be a positive integer or null")

    return value


def optional_string_list(section_name: str, data: dict, key: str) -> list[str]:
    """
    Read an optional list of strings from a config object.
    """
    value = data.get(key, [])

    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{section_name}.{key} must be a list of strings")

    return value


def parse_cleanup_rules(
    upload_section_name: str,
    raw_upload: dict,
) -> list[DirectoryCleanupRule]:
    """
    Parse cleanup_rules owned by one upload destination.
    """
    raw_rules = raw_upload.get("cleanup_rules", [])

    if not isinstance(raw_rules, list):
        raise ValueError(f"{upload_section_name}.cleanup_rules must be a list")

    rules: list[DirectoryCleanupRule] = []
    seen_paths: set[str] = set()

    for index, raw_rule in enumerate(raw_rules, start=1):
        section_name = f"{upload_section_name}.cleanup_rules[{index}]"

        if not isinstance(raw_rule, dict):
            raise ValueError(f"{section_name} must be an object")

        path = require_string(section_name, raw_rule, "path")
        normalized_path = normalize_relative_path(path)

        if normalized_path in seen_paths:
            raise ValueError(
                f"{section_name}.path duplicates another cleanup rule in "
                f"{upload_section_name}: {path}"
            )

        seen_paths.add(normalized_path)

        max_files = optional_positive_int_or_none(section_name, raw_rule, "max_files")
        max_size = optional_string(section_name, raw_rule, "max_size", None)
        delete_old_files = optional_bool_or_none(section_name, raw_rule, "delete_old_files")
        delete_excess_files = optional_bool_or_none(section_name, raw_rule, "delete_excess_files")
        delete_to_trash = optional_bool_or_none(section_name, raw_rule, "delete_to_trash")

        if max_size is not None:
            parse_size_to_bytes(max_size)

        rules.append(
            DirectoryCleanupRule(
                path=path,
                max_files=max_files,
                max_size=max_size,
                delete_old_files=delete_old_files,
                delete_excess_files=delete_excess_files,
                delete_to_trash=delete_to_trash,
            )
        )

    return rules

def parse_upload_directories(config: dict) -> list[UploadDirectory]:
    """Convert upload_directories config objects into UploadDirectory instances."""
    raw_uploads = config.get("upload_directories")

    if not isinstance(raw_uploads, list) or not raw_uploads:
        raise ValueError("upload_directories must be a non-empty list")

    uploads: list[UploadDirectory] = []

    for index, raw_upload in enumerate(raw_uploads, start=1):
        section_name = f"upload_directories[{index}]"

        if not isinstance(raw_upload, dict):
            raise ValueError(f"{section_name} must be an object")

        local_path = require_string(section_name, raw_upload, "local_path")
        remote_path = require_string(section_name, raw_upload, "remote_path")
        name = optional_string(section_name, raw_upload, "name", None)
        upload_command = validate_upload_command(
            optional_string(section_name, raw_upload, "upload_command", "copy")
        )
        delete_old_files = optional_bool(section_name, raw_upload, "delete_old_files", True)
        delete_excess_files = optional_bool(section_name, raw_upload, "delete_excess_files", True)
        max_total_size = optional_string(section_name, raw_upload, "max_total_size", None)
        delete_to_trash = optional_bool(section_name, raw_upload, "delete_to_trash", False)
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
                    "the script manages this flag through dedicated reservation/runtime settings"
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
    """Load the external JSON configuration."""
    global CONFIG_PATH
    global UPLOAD_DIRECTORIES
    global DELETE_MIN_AGE
    global UPLOAD_THREADS
    global CLEANUP_THREADS
    global REMOTE_QUOTA_CLEANUP_THREADS
    global TRASH_CLEANUP_THREADS
    global LOCK_FILE
    global DELETE_LIST_DIR
    global SLEEP_AFTER_STEP

    config_path = Path(config_path_text).expanduser()
    config = load_json_config(config_path)

    if "directory_cleanup_rules" in config:
        raise ValueError(
            "Top-level directory_cleanup_rules is no longer supported. "
            "Move each rule into cleanup_rules inside the upload_directories "
            "entry for the remote it should clean."
        )

    UPLOAD_DIRECTORIES = parse_upload_directories(config)
    DELETE_MIN_AGE = optional_string("root", config, "delete_min_age", DELETE_MIN_AGE)

    thread_limits = config.get("thread_limits", {})
    if not isinstance(thread_limits, dict):
        raise ValueError("thread_limits must be an object")

    UPLOAD_THREADS = optional_non_negative_int(
        "thread_limits", thread_limits, "upload_threads", UPLOAD_THREADS
    )
    CLEANUP_THREADS = optional_non_negative_int(
        "thread_limits", thread_limits, "cleanup_threads", CLEANUP_THREADS
    )
    REMOTE_QUOTA_CLEANUP_THREADS = optional_non_negative_int(
        "thread_limits",
        thread_limits,
        "remote_quota_cleanup_threads",
        REMOTE_QUOTA_CLEANUP_THREADS,
    )
    TRASH_CLEANUP_THREADS = optional_non_negative_int(
        "thread_limits",
        thread_limits,
        "trash_cleanup_threads",
        TRASH_CLEANUP_THREADS,
    )

    if UPLOAD_THREADS < 1:
        raise ValueError("thread_limits.upload_threads must be at least 1")
    if CLEANUP_THREADS < 1:
        raise ValueError("thread_limits.cleanup_threads must be at least 1")
    if REMOTE_QUOTA_CLEANUP_THREADS < 1:
        raise ValueError("thread_limits.remote_quota_cleanup_threads must be at least 1")
    if TRASH_CLEANUP_THREADS < 1:
        raise ValueError("thread_limits.trash_cleanup_threads must be at least 1")

    LOCK_FILE = Path(
        optional_string("root", config, "lock_file", str(LOCK_FILE))
    ).expanduser()
    DELETE_LIST_DIR = Path(
        optional_string("root", config, "delete_list_dir", str(DELETE_LIST_DIR))
    ).expanduser()
    SLEEP_AFTER_STEP = optional_non_negative_int(
        "root", config, "sleep_after_step", SLEEP_AFTER_STEP
    )
    CONFIG_PATH = config_path

# =============================================================================
# Lock handling
# =============================================================================

def acquire_lock():
    """
    Create lock file atomically.
    """
    global lock_created

    try:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

        fd = os.open(
            LOCK_FILE,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o644,
        )

        with os.fdopen(fd, "w") as lock_file:
            lock_file.write(f"pid={os.getpid()}\n")

        lock_created = True

    except FileExistsError:
        print("Lock file exists, exiting.")
        sys.exit(0)

    except Exception as error:
        print_error(f"Failed to create lock file: {error}")
        sys.exit(1)


def release_lock():
    global lock_created

    if lock_created and LOCK_FILE.exists():
        try:
            LOCK_FILE.unlink()
            lock_created = False
        except Exception as error:
            print_error(f"Failed to remove lock file: {error}")


def signal_handler(signum, frame):
    print_error(f"Received signal {signum}, exiting.")
    release_lock()
    sys.exit(128 + signum)


def sleep_after_step():
    print_step(f"Sleeping for {SLEEP_AFTER_STEP}s")
    time.sleep(SLEEP_AFTER_STEP)


# =============================================================================
# Build generated cleanup targets
# =============================================================================

def build_cleanup_directories() -> list[CleanupTarget]:
    """
    Build cleanup targets from each upload destination's own cleanup_rules.
    """
    cleanup_directories: list[CleanupTarget] = []

    for upload in UPLOAD_DIRECTORIES:
        for directory in upload.cleanup_rules:
            cleanup_directories.append(
                CleanupTarget(
                    path=join_rclone_remote_path(upload.remote_path, directory.path),
                    max_files=directory.max_files,
                    max_size=directory.max_size,
                    delete_old_files=(
                        upload.delete_old_files
                        if directory.delete_old_files is None
                        else directory.delete_old_files
                    ),
                    delete_excess_files=(
                        upload.delete_excess_files
                        if directory.delete_excess_files is None
                        else directory.delete_excess_files
                    ),
                    delete_to_trash=(
                        upload.delete_to_trash
                        if directory.delete_to_trash is None
                        else directory.delete_to_trash
                    ),
                    owner_remote_path=upload.remote_path,
                )
            )

    return cleanup_directories

# =============================================================================
# Startup summary
# =============================================================================

def print_startup_summary(cleanup_directories: list[CleanupTarget]):
    """Print the effective configuration before any destructive work."""
    with OUTPUT_LOCK:
        print()
        print(OUTPUT_SEPARATOR)
        print("SCRIPT STARTUP SUMMARY")
        print(OUTPUT_SEPARATOR)

        print()
        print("Runtime:")
        print(f"  Version     : {VERSION}")
        print(f"  Config file : {CONFIG_PATH}")

        print()
        print("Execution order:")
        print("  1. Pre-upload cleanup_rules")
        print("  2. Pre-upload trash cleanup")
        print("  3. Start independent per-remote reservation/upload pipelines")
        print("       a. Read filtered local source size with rclone size")
        print("       b. Compare local bytes + managed remote bytes with max_total_size")
        print("       c. Delete oldest remote files until selected bytes >= byte deficit")
        print("       d. Re-read sizes and verify reservation")
        print("       e. Trash cleanup after reservation for that remote")
        print("       f. Upload that remote as soon as it is ready")
        print("  4. Post-upload cleanup_rules")
        print("  5. Post-upload max_total_size cleanup")
        print("  6. Post-upload trash cleanup")
        print("  7. Final limit verification")

        print()
        print("Thread limits:")
        print(f"  Per-folder cleanup jobs   : {CLEANUP_THREADS}")
        print(f"  Remote quota cleanup jobs : {REMOTE_QUOTA_CLEANUP_THREADS}")
        print(f"  Trash cleanup jobs        : {TRASH_CLEANUP_THREADS}")
        print(f"  Upload jobs               : {UPLOAD_THREADS}")

        print()
        print("Global cleanup settings:")
        print(f"  Delete min age              : {DELETE_MIN_AGE}")
        print(f"  Reservation safety headroom : {format_bytes(RESERVATION_SAFETY_HEADROOM_BYTES)}")
        print(f"  Reservation cleanup passes  : {MAX_RESERVATION_CLEANUP_PASSES}")
        print(f"  Lock file                   : {LOCK_FILE}")
        print(f"  Delete-list folder          : {DELETE_LIST_DIR}")

        print()
        print("Upload destinations and cleanup rules:")
        for index, upload in enumerate(UPLOAD_DIRECTORIES, start=1):
            print(f"  Upload destination {index}:")
            print(f"    Name                : {upload.name or upload.remote_path}")
            print(f"    Local path          : {upload.local_path}")
            print(f"    Remote path         : {upload.remote_path}")
            print(f"    Upload command      : rclone {upload.upload_command}")
            print(f"    Delete old files    : {upload.delete_old_files}")
            print(f"    Delete excess files : {upload.delete_excess_files}")
            print(f"    Remote max total    : {upload.max_total_size}")
            print(f"    Delete mode         : {get_delete_mode_text(upload)}")
            print(f"    Empty trash         : {upload.empty_trash}")
            print(f"    Buffer size         : {upload.buffer_size}")
            print(f"    Upload options      : {' '.join(upload.copy_options)}")
            print(f"    Cleanup rules       : {len(upload.cleanup_rules)}")

            for rule_index, rule in enumerate(upload.cleanup_rules, start=1):
                print(f"      Rule {rule_index}:")
                print(f"        Relative path          : {rule.path}")
                print(f"        Max files              : {rule.max_files}")
                print(f"        Max size               : {rule.max_size}")
                print(f"        Delete old override    : {rule.delete_old_files}")
                print(f"        Delete excess override : {rule.delete_excess_files}")
                print(f"        Delete mode override   : {rule.delete_to_trash}")

        print()
        print("Generated cleanup targets:")
        for index, target in enumerate(cleanup_directories, start=1):
            print(f"  Cleanup target {index}:")
            print(f"    Path                : {target.path}")
            print(f"    Delete old files    : {target.delete_old_files}")
            print(f"    Delete excess files : {target.delete_excess_files}")
            print(f"    Max files           : {target.max_files}")
            print(f"    Max size            : {target.max_size}")
            print(f"    Delete mode         : {get_delete_mode_text(target)}")
            print(f"    Delete min age      : {DELETE_MIN_AGE}")

        total_cleanup_rules = sum(
            len(upload.cleanup_rules) for upload in UPLOAD_DIRECTORIES
        )

        print()
        print(f"Total upload destinations     : {len(UPLOAD_DIRECTORIES)}")
        print(f"Total directory cleanup rules : {total_cleanup_rules}")
        print(f"Total cleanup targets         : {len(cleanup_directories)}")
        print(OUTPUT_SEPARATOR)
        print(flush=True)

# =============================================================================
# Remote file listing and delete-list creation
# =============================================================================

def get_remote_file_entries(remote_path: str) -> list[RemoteFile]:
    """
    Recursively read remote file metadata as one rclone lsjson array.

    Required JSON fields for every file:
      Path    - path relative to remote_path
      Size    - exact file size in bytes
      ModTime - modification time used for oldest-first ordering

    Cleanup ordering and byte accounting are destructive decisions, so malformed
    or missing metadata is a hard failure. The script never silently skips a JSON
    item and continues with an incomplete remote view.
    """
    command = [
        "rclone",
        "lsjson",
        "--recursive",
        "--files-only",
        "--no-mimetype",
        remote_path,
    ]

    result = run_command(command, capture_output=True)

    if result.returncode != 0:
        if is_directory_not_found(result):
            print_step(f"Remote folder does not exist yet, skipping list: {remote_path}")
            return []

        detail = (result.stderr or result.stdout or "unknown rclone lsjson error").strip()
        raise RuntimeError(
            f"rclone lsjson failed for {remote_path}. "
            f"Return code: {result.returncode}\n{detail}"
        )

    try:
        raw_items = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"rclone lsjson returned invalid JSON for {remote_path}: "
            f"line {error.lineno}, column {error.colno}: {error.msg}"
        ) from error

    if not isinstance(raw_items, list):
        raise RuntimeError(
            f"rclone lsjson returned {type(raw_items).__name__} for {remote_path}; "
            "expected a JSON array"
        )

    files: list[RemoteFile] = []

    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(
                f"rclone lsjson item {index} for {remote_path} is not a JSON object"
            )

        if item.get("IsDir") is True:
            raise RuntimeError(
                f"rclone lsjson unexpectedly returned a directory with --files-only "
                f"for {remote_path}: {item.get('Path')!r}"
            )

        path = item.get("Path")
        size = item.get("Size")
        modified = item.get("ModTime")

        if not isinstance(path, str) or not path.strip():
            raise RuntimeError(
                f"rclone lsjson item {index} for {remote_path} has invalid Path: {path!r}"
            )

        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise RuntimeError(
                f"rclone lsjson item {index} for {remote_path} has invalid Size "
                f"for {path!r}: {size!r}"
            )

        if not isinstance(modified, str) or not modified.strip():
            raise RuntimeError(
                f"rclone lsjson item {index} for {remote_path} has missing/invalid "
                f"ModTime for {path!r}; oldest-first cleanup cannot be calculated safely"
            )

        try:
            parse_rclone_modtime(modified)
        except ValueError as error:
            raise RuntimeError(
                f"rclone lsjson item {index} for {remote_path} has an unusable "
                f"ModTime for {path!r}: {error}"
            ) from error

        files.append(
            RemoteFile(
                path=normalize_relative_path(path),
                size=size,
                modified=modified,
            )
        )

    return files

def make_delete_list(target: CleanupTarget) -> Path:
    """
    Create a --files-from list for files that should be deleted.

    Supports:
      max_files = keep only this many newest files
      max_size  = keep folder under this total size
      both      = delete oldest files until both limits are satisfied
    """
    DELETE_LIST_DIR.mkdir(parents=True, exist_ok=True)

    safe_name = remote_name_from_path(target.path)
    delete_list_path = DELETE_LIST_DIR / f"to-delete-{safe_name}"

    files = get_remote_file_entries(target.path)

    if not files:
        print_step(f"No files found in {target.path}")
        delete_list_path.write_text("", encoding="utf-8")
        return delete_list_path

    # Oldest first.
    # rclone modified timestamps normally sort correctly as text.
    files_oldest_first = sorted(
        files,
        key=remote_file_oldest_sort_key,
    )

    total_size = sum(file.size for file in files_oldest_first)
    total_files = len(files_oldest_first)

    max_size_bytes = None

    if target.max_size is not None:
        max_size_bytes = parse_size_to_bytes(target.max_size)

    files_to_delete: list[RemoteFile] = []

    current_size = total_size
    current_files = total_files

    for file in files_oldest_first:
        too_many_files = (
            target.max_files is not None
            and current_files > target.max_files
        )

        too_much_size = (
            max_size_bytes is not None
            and current_size > max_size_bytes
        )

        if not too_many_files and not too_much_size:
            break

        files_to_delete.append(file)
        current_size -= file.size
        current_files -= 1

    delete_list_path.write_text(
        "\n".join(file.path for file in files_to_delete)
        + ("\n" if files_to_delete else ""),
        encoding="utf-8",
    )

    size_before_gib = total_size / 1024 ** 3
    size_after_gib = current_size / 1024 ** 3
    deleted_size_gib = (total_size - current_size) / 1024 ** 3

    print_step(
        f"Made delete list for {target.path}: "
        f"{len(files_to_delete)} file(s) marked for deletion. "
        f"Files before: {total_files}, after: {current_files}. "
        f"Size before: {size_before_gib:.2f} GiB, "
        f"after: {size_after_gib:.2f} GiB, "
        f"delete: {deleted_size_gib:.2f} GiB."
    )

    return delete_list_path


def get_upload_remote_quota_entries(upload: UploadDirectory) -> list[RemoteQuotaFile]:
    """
    Read ONE recursive lsjson array for upload.remote_path, then keep only files
    covered by this upload destination's cleanup_rules.

    This gives remote-wide reservation/max_total_size cleanup one consistent JSON
    snapshot for the remote. Overlapping cleanup rules are resolved in Python and
    every relative file path is returned at most once.
    """
    entries = get_remote_file_entries(upload.remote_path)
    managed_rule_paths = [
        normalize_relative_path(rule.path)
        for rule in upload.cleanup_rules
    ]

    files_by_path: dict[str, RemoteQuotaFile] = {}

    for file in entries:
        relative_path = normalize_relative_path(file.path)
        matched_rule_path: str | None = None

        for rule_path in managed_rule_paths:
            if not rule_path:
                matched_rule_path = "/"
                break

            if relative_path == rule_path or relative_path.startswith(f"{rule_path}/"):
                matched_rule_path = rule_path
                break

        if matched_rule_path is None:
            continue

        files_by_path[relative_path] = RemoteQuotaFile(
            path=relative_path,
            size=file.size,
            modified=file.modified,
            source_folder=matched_rule_path,
        )

    return list(files_by_path.values())

def make_upload_remote_quota_delete_list(upload: UploadDirectory) -> Path:
    """
    Create a --files-from list for remote-wide quota cleanup.

    This enforces upload.max_total_size across this upload destination's own
    cleanup_rules below upload.remote_path.
    """
    DELETE_LIST_DIR.mkdir(parents=True, exist_ok=True)

    safe_name = remote_name_from_path(upload.remote_path)
    delete_list_path = DELETE_LIST_DIR / f"to-delete-remote-quota-{safe_name}"

    if upload.max_total_size is None:
        delete_list_path.write_text("", encoding="utf-8")
        return delete_list_path

    max_total_size_bytes = parse_size_to_bytes(upload.max_total_size)
    files = get_upload_remote_quota_entries(upload)

    if not files:
        print_step(f"No managed files found below {upload.remote_path}")
        delete_list_path.write_text("", encoding="utf-8")
        return delete_list_path

    files_oldest_first = sorted(
        files,
        key=remote_file_oldest_sort_key,
    )

    total_size = sum(file.size for file in files_oldest_first)
    total_files = len(files_oldest_first)

    files_to_delete: list[RemoteQuotaFile] = []
    current_size = total_size
    current_files = total_files

    for file in files_oldest_first:
        if current_size <= max_total_size_bytes:
            break

        files_to_delete.append(file)
        current_size -= file.size
        current_files -= 1

    delete_list_path.write_text(
        "\n".join(file.path for file in files_to_delete)
        + ("\n" if files_to_delete else ""),
        encoding="utf-8",
    )

    size_before_gib = total_size / 1024 ** 3
    size_after_gib = current_size / 1024 ** 3
    deleted_size_gib = (total_size - current_size) / 1024 ** 3
    max_size_gib = max_total_size_bytes / 1024 ** 3

    print_step(
        f"Made remote quota delete list for {upload.remote_path}: "
        f"{len(files_to_delete)} file(s) marked for deletion. "
        f"Files before: {total_files}, after: {current_files}. "
        f"Size before: {size_before_gib:.2f} GiB, "
        f"after: {size_after_gib:.2f} GiB, "
        f"limit: {max_size_gib:.2f} GiB, "
        f"delete: {deleted_size_gib:.2f} GiB."
    )

    return delete_list_path


# =============================================================================
# Cleanup jobs
# =============================================================================

def cleanup_one_directory(
    job_number: int,
    target: CleanupTarget,
    phase_name: str = "",
) -> bool:
    """Cleanup one generated remote directory."""
    stage_name = "post_cleanup" if phase_name.startswith("POST-UPLOAD") else "reservation"

    print_job_block("CLEANUP JOB", job_number, target.path, "Starting cleanup job")

    if not target.delete_old_files and not target.delete_excess_files:
        print_job_block(
            "CLEANUP JOB", job_number, target.path,
            "delete_old_files=False and delete_excess_files=False, skipping",
        )
        return True

    if target.delete_old_files:
        command = [
            "rclone", "delete", target.path, "--min-age", DELETE_MIN_AGE,
        ] + get_delete_mode_options(target)
        result = run_command(command, capture_output=True)
        output = (result.stdout or "") + (result.stderr or "")

        if output:
            print_job_block("CLEANUP JOB", job_number, target.path, output)

        if result.returncode != 0:
            if is_directory_not_found(result):
                print_job_block(
                    "CLEANUP JOB", job_number, target.path,
                    "Remote folder does not exist yet, skipping cleanup for this folder",
                )
                return True

            detail = (
                f"Command: {' '.join(command)}\n"
                f"Return code: {result.returncode}\n"
                f"{command_error_summary(output)}"
            )
            record_stage_failure(target.owner_remote_path, stage_name, detail)
            print_job_block("CLEANUP JOB", job_number, target.path, f"Failed deleting old files.\n{detail}")
            return False
    else:
        print_job_block("CLEANUP JOB", job_number, target.path, "Skipping delete_old_files for this target")

    if not target.delete_excess_files:
        print_job_block("CLEANUP JOB", job_number, target.path, "Skipping delete_excess_files for this target")
        return True

    if target.max_files is None and target.max_size is None:
        print_job_block(
            "CLEANUP JOB", job_number, target.path,
            "delete_excess_files=True but no max_files or max_size is set, skipping",
        )
        return True

    try:
        delete_list_path = make_delete_list(target)
    except Exception as error:
        detail = f"Failed making delete list: {error}"
        record_stage_failure(target.owner_remote_path, stage_name, detail)
        print_job_block("CLEANUP JOB", job_number, target.path, detail)
        return False

    if delete_list_path.stat().st_size == 0:
        print_job_block("CLEANUP JOB", job_number, target.path, "No excessive files to delete")
        return True

    command = [
        "rclone", "delete", "--files-from", str(delete_list_path), target.path,
    ] + get_delete_mode_options(target)
    result = run_command(command, capture_output=True)
    output = (result.stdout or "") + (result.stderr or "")

    if output:
        print_job_block("CLEANUP JOB", job_number, target.path, output)

    if result.returncode != 0:
        detail = (
            f"Command: {' '.join(command)}\n"
            f"Return code: {result.returncode}\n"
            f"{command_error_summary(output)}"
        )
        record_stage_failure(target.owner_remote_path, stage_name, detail)
        print_job_block("CLEANUP JOB", job_number, target.path, f"Failed deleting excessive files.\n{detail}")
        return False

    print_job_block("CLEANUP JOB", job_number, target.path, "Cleanup job finished successfully")
    return True

def cleanup_one_upload_remote_quota(
    job_number: int,
    upload: UploadDirectory,
    phase_name: str = "",
) -> bool:
    """Enforce upload.max_total_size across this remote's managed cleanup rules."""
    stage_name = "post_cleanup" if phase_name.startswith("POST-UPLOAD") else "reservation"

    print_job_block(
        "REMOTE QUOTA CLEANUP JOB", job_number, upload.remote_path,
        "Starting remote-wide quota cleanup job",
    )

    if not upload.delete_excess_files:
        print_job_block(
            "REMOTE QUOTA CLEANUP JOB", job_number, upload.remote_path,
            "delete_excess_files=False, skipping remote-wide quota cleanup",
        )
        return True

    if upload.max_total_size is None:
        print_job_block(
            "REMOTE QUOTA CLEANUP JOB", job_number, upload.remote_path,
            "max_total_size=None, skipping remote-wide quota cleanup",
        )
        return True

    try:
        delete_list_path = make_upload_remote_quota_delete_list(upload)
    except Exception as error:
        detail = f"Failed making remote quota delete list: {error}"
        record_stage_failure(upload.remote_path, stage_name, detail)
        print_job_block("REMOTE QUOTA CLEANUP JOB", job_number, upload.remote_path, detail)
        return False

    if delete_list_path.stat().st_size == 0:
        print_job_block(
            "REMOTE QUOTA CLEANUP JOB", job_number, upload.remote_path,
            "No remote-wide quota files to delete",
        )
        return True

    command = [
        "rclone", "delete", "--files-from", str(delete_list_path), upload.remote_path,
    ] + get_delete_mode_options(upload)
    result = run_command(command, capture_output=True)
    output = (result.stdout or "") + (result.stderr or "")

    if output:
        print_job_block("REMOTE QUOTA CLEANUP JOB", job_number, upload.remote_path, output)

    if result.returncode != 0:
        detail = (
            f"Command: {' '.join(command)}\n"
            f"Return code: {result.returncode}\n"
            f"{command_error_summary(output)}"
        )
        record_stage_failure(upload.remote_path, stage_name, detail)
        print_job_block(
            "REMOTE QUOTA CLEANUP JOB", job_number, upload.remote_path,
            f"Failed deleting remote-wide quota files.\n{detail}",
        )
        return False

    print_job_block(
        "REMOTE QUOTA CLEANUP JOB", job_number, upload.remote_path,
        "Remote-wide quota cleanup finished successfully",
    )
    return True

def cleanup_one_trash_remote(
    job_number: int,
    upload: UploadDirectory,
    phase_name: str = "",
) -> bool:
    """Optionally run rclone cleanup for one upload remote."""
    stage_name = "post_cleanup" if phase_name.startswith("POST-UPLOAD") else "reservation"

    if not upload.empty_trash:
        print_job_block(
            "TRASH CLEANUP JOB", job_number, upload.remote_path,
            "empty_trash=False, skipping rclone cleanup for this remote",
        )
        return True

    if not upload.delete_to_trash:
        print_job_block(
            "TRASH CLEANUP JOB", job_number, upload.remote_path,
            (
                "delete_to_trash=False, script cleanup deletions are direct; "
                "skipping rclone cleanup because no script-managed trash needs emptying"
            ),
        )
        return True

    print_job_block(
        "TRASH CLEANUP JOB", job_number, upload.remote_path,
        "Starting rclone cleanup / empty trash",
    )

    command = ["rclone", "cleanup", upload.remote_path]
    result = run_command(command, capture_output=True)
    output = (result.stdout or "") + (result.stderr or "")

    if result.returncode != 0:
        if "not supported" in output.lower() or "doesn't support" in output.lower():
            print_job_block(
                "TRASH CLEANUP JOB", job_number, upload.remote_path,
                f"rclone cleanup is not supported for this remote, skipping.\n\n{output}",
            )
            return True

        detail = (
            f"Command: {' '.join(command)}\n"
            f"Return code: {result.returncode}\n"
            f"{command_error_summary(output)}"
        )
        record_stage_failure(upload.remote_path, stage_name, detail)
        print_job_block(
            "TRASH CLEANUP JOB", job_number, upload.remote_path,
            f"Trash cleanup failed.\n{detail}",
        )
        return False

    if output:
        print_job_block("TRASH CLEANUP JOB", job_number, upload.remote_path, output)

    print_job_block(
        "TRASH CLEANUP JOB", job_number, upload.remote_path,
        "Trash cleanup finished successfully",
    )
    return True

# =============================================================================
# Filtered local sizing and pre-upload quota reservation
# =============================================================================

# Filter options which change the set of source files considered by rclone copy,
# move, and sync. Only these are forwarded to `rclone size`; stats, transfers,
# buffering, and other runtime options do not affect the source byte total.
SIZE_FILTER_OPTIONS_WITH_VALUE = {
    "--min-age",
    "--max-age",
    "--min-size",
    "--max-size",
    "--include",
    "--include-from",
    "--exclude",
    "--exclude-from",
    "--filter",
    "--filter-from",
    "--files-from",
    "--files-from-raw",
}

SIZE_FILTER_OPTIONS_BOOLEAN = {
    "--ignore-case",
}


def transfer_cap_bytes(transfer_bytes: int) -> int:
    """Add one byte so an exactly measured upload can reach its byte cap."""
    if transfer_bytes <= 0:
        return 0

    return transfer_bytes + 1


def validate_local_upload_path(upload: UploadDirectory) -> tuple[bool, str | None]:
    """Validate the local upload root before sizing or uploading."""
    local_path = Path(upload.local_path)

    if not local_path.exists():
        return False, f"Local path does not exist: {upload.local_path}"

    if not local_path.is_dir():
        return False, f"Local path is not a directory: {upload.local_path}"

    return True, None


def get_size_filter_options(upload: UploadDirectory) -> list[str]:
    """
    Extract source-selection filters from copy_options for `rclone size`.

    This keeps the size calculation aligned with options such as --max-age and
    --exclude without passing unrelated upload-only/runtime options to size.
    Both `--flag value` and `--flag=value` forms are supported.
    """
    extracted: list[str] = []
    options = upload.copy_options
    index = 0

    while index < len(options):
        option = options[index]
        option_name = option.split("=", 1)[0]

        if option_name in SIZE_FILTER_OPTIONS_BOOLEAN:
            extracted.append(option)
            index += 1
            continue

        if option_name in SIZE_FILTER_OPTIONS_WITH_VALUE:
            if "=" in option:
                extracted.append(option)
                index += 1
                continue

            if index + 1 >= len(options):
                raise ValueError(
                    f"Upload option {option_name} requires a value for local size calculation"
                )

            extracted.extend([option, options[index + 1]])
            index += 2
            continue

        index += 1

    return extracted


def get_filtered_local_upload_size(
    job_number: int,
    upload: UploadDirectory,
) -> tuple[int, int] | None:
    """
    Return (bytes, file_count) for the complete filtered local upload source.

    This intentionally does not compare source and destination. The full local
    candidate size is reserved conservatively even when some files may already
    exist remotely.
    """
    valid, error = validate_local_upload_path(upload)
    if not valid:
        detail = error or "Invalid local path"
        record_stage_failure(upload.remote_path, "reservation", detail)
        print_job_block("UPLOAD SIZE JOB", job_number, upload.remote_path, detail)
        return None

    try:
        filter_options = get_size_filter_options(upload)
    except ValueError as error:
        detail = f"Could not build rclone size filters: {error}"
        record_stage_failure(upload.remote_path, "reservation", detail)
        print_job_block("UPLOAD SIZE JOB", job_number, upload.remote_path, detail)
        return None

    command = [
        "rclone",
        "size",
        upload.local_path,
        "--json",
    ] + filter_options

    result = run_command(command, capture_output=True)

    if result.returncode != 0:
        command_output = (result.stderr or result.stdout or "unknown rclone size error").strip()
        detail = (
            f"Command: {' '.join(command)}\n"
            f"Return code: {result.returncode}\n"
            f"{command_error_summary(command_output)}"
        )
        record_stage_failure(upload.remote_path, "reservation", detail)
        print_job_block(
            "UPLOAD SIZE JOB",
            job_number,
            upload.remote_path,
            f"Failed sizing filtered local upload source.\n\n{detail}",
        )
        return None

    try:
        payload = json.loads(result.stdout)
        size_bytes = int(payload["bytes"])
        file_count = int(payload["count"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        detail = f"Could not parse rclone size --json output: {error}\nOutput: {result.stdout}"
        record_stage_failure(upload.remote_path, "reservation", detail)
        print_job_block("UPLOAD SIZE JOB", job_number, upload.remote_path, detail)
        return None

    if size_bytes < 0 or file_count < 0:
        detail = "rclone size returned a negative byte or file count"
        record_stage_failure(upload.remote_path, "reservation", detail)
        print_job_block("UPLOAD SIZE JOB", job_number, upload.remote_path, detail)
        return None

    print_job_block(
        "UPLOAD SIZE JOB",
        job_number,
        upload.remote_path,
        (
            "Filtered local upload size calculated.\n"
            f"Files selected by upload filters: {file_count}\n"
            f"Local candidate size           : {format_bytes(size_bytes)}\n"
            f"Size filters                   : {' '.join(filter_options) if filter_options else '(none)'}"
        ),
    )

    return size_bytes, file_count

def make_upload_reservation_delete_list(
    upload: UploadDirectory,
    local_upload_bytes: int,
) -> tuple[Path, int, int, int]:
    """
    Select oldest managed remote files until selected bytes are AT LEAST the
    calculated byte deficit.

    Returns:
      delete_list_path, current_remote_size, required_free_bytes,
      selected_free_bytes
    """
    DELETE_LIST_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = remote_name_from_path(upload.remote_path)
    delete_list_path = DELETE_LIST_DIR / f"to-delete-upload-reservation-{safe_name}"

    if upload.max_total_size is None or not upload.delete_excess_files:
        delete_list_path.write_text("", encoding="utf-8")
        return delete_list_path, 0, 0, 0

    max_total_size_bytes = parse_size_to_bytes(upload.max_total_size)
    files = get_upload_remote_quota_entries(upload)
    current_size = sum(file.size for file in files)
    reserved_upload_bytes = transfer_cap_bytes(local_upload_bytes)
    required_free_bytes = max(
        0,
        current_size
        + reserved_upload_bytes
        + RESERVATION_SAFETY_HEADROOM_BYTES
        - max_total_size_bytes,
    )

    if required_free_bytes == 0:
        delete_list_path.write_text("", encoding="utf-8")
        return delete_list_path, current_size, 0, 0

    candidates = sorted(
        files,
        key=remote_file_oldest_sort_key,
    )

    selected: list[RemoteQuotaFile] = []
    selected_free_bytes = 0

    for file in candidates:
        if selected_free_bytes >= required_free_bytes:
            break

        selected.append(file)
        selected_free_bytes += file.size

    if selected_free_bytes < required_free_bytes:
        delete_list_path.write_text("", encoding="utf-8")
        return (
            delete_list_path,
            current_size,
            required_free_bytes,
            selected_free_bytes,
        )

    delete_list_path.write_text(
        "\n".join(file.path for file in selected) + "\n",
        encoding="utf-8",
    )

    return (
        delete_list_path,
        current_size,
        required_free_bytes,
        selected_free_bytes,
    )


def reserve_one_upload_remote_space(job_number: int, upload: UploadDirectory) -> bool:
    """
    Reserve enough managed remote space for the COMPLETE filtered local source.

    There is no identical-file protection and no source/destination comparison.
    Each pass measures the filtered local source, reads managed remote metadata
    through rclone lsjson, calculates the exact byte deficit, sorts the JSON file
    records oldest-first by ModTime, and selects complete files until selected
    bytes are greater than or equal to the deficit. One --files-from list is then
    written and one rclone delete command is run for that reservation pass.
    """
    print_job_block(
        "UPLOAD RESERVATION JOB",
        job_number,
        upload.remote_path,
        "Starting size-based pre-upload space reservation",
    )

    if not upload.delete_excess_files:
        print_job_block(
            "UPLOAD RESERVATION JOB",
            job_number,
            upload.remote_path,
            "delete_excess_files=False, reservation cleanup is disabled",
        )
        record_stage_success(upload.remote_path, "reservation")
        return True

    if upload.max_total_size is None:
        print_job_block(
            "UPLOAD RESERVATION JOB",
            job_number,
            upload.remote_path,
            "max_total_size=None, no total managed-remote limit to reserve against",
        )
        record_stage_success(upload.remote_path, "reservation")
        return True

    max_total_size_bytes = parse_size_to_bytes(upload.max_total_size)

    for reservation_pass in range(1, MAX_RESERVATION_CLEANUP_PASSES + 1):
        local_size_result = get_filtered_local_upload_size(job_number, upload)
        if local_size_result is None:
            return False

        local_upload_bytes, local_file_count = local_size_result
        reserved_upload_bytes = transfer_cap_bytes(local_upload_bytes)

        if reserved_upload_bytes + RESERVATION_SAFETY_HEADROOM_BYTES > max_total_size_bytes:
            detail = (
                "The complete filtered local upload source plus safety headroom is larger "
                "than max_total_size. Even an empty managed remote cannot reserve enough space.\n"
                f"Filtered local files : {local_file_count}\n"
                f"Local upload size    : {format_bytes(local_upload_bytes)}\n"
                f"Reserved byte cap    : {format_bytes(reserved_upload_bytes)}\n"
                f"Safety headroom      : {format_bytes(RESERVATION_SAFETY_HEADROOM_BYTES)}\n"
                f"Max total size       : {format_bytes(max_total_size_bytes)}"
            )
            record_stage_failure(upload.remote_path, "reservation", detail)
            print_job_block("UPLOAD RESERVATION JOB", job_number, upload.remote_path, detail)
            return False

        try:
            (
                delete_list_path,
                current_size,
                required_free_bytes,
                selected_free_bytes,
            ) = make_upload_reservation_delete_list(upload, local_upload_bytes)
        except Exception as error:
            detail = f"Failed calculating size reservation on pass {reservation_pass}: {error}"
            record_stage_failure(upload.remote_path, "reservation", detail)
            print_job_block("UPLOAD RESERVATION JOB", job_number, upload.remote_path, detail)
            return False

        projected_temporary_size = current_size + reserved_upload_bytes
        available_headroom = max_total_size_bytes - projected_temporary_size

        if required_free_bytes == 0:
            with RESERVED_UPLOAD_BYTES_LOCK:
                RESERVED_UPLOAD_BYTES[upload.remote_path] = local_upload_bytes

            record_stage_success(upload.remote_path, "reservation")
            print_job_block(
                "UPLOAD RESERVATION JOB",
                job_number,
                upload.remote_path,
                (
                    "Pre-upload size reservation verified successfully.\n"
                    f"Reservation pass          : {reservation_pass}\n"
                    f"Current managed size      : {format_bytes(current_size)}\n"
                    f"Filtered local files      : {local_file_count}\n"
                    f"Filtered local upload size: {format_bytes(local_upload_bytes)}\n"
                    f"Reserved upload cap       : {format_bytes(reserved_upload_bytes)}\n"
                    f"Projected temporary size  : {format_bytes(projected_temporary_size)}\n"
                    f"Available quota headroom  : {format_bytes(max(0, available_headroom))}\n"
                    f"Required safety headroom  : {format_bytes(RESERVATION_SAFETY_HEADROOM_BYTES)}\n"
                    f"Max total size            : {format_bytes(max_total_size_bytes)}"
                ),
            )
            return True

        if selected_free_bytes < required_free_bytes:
            detail = (
                "Cannot reserve enough space because the managed remote does not contain "
                "enough deletable bytes.\n"
                f"Reservation pass       : {reservation_pass}\n"
                f"Current managed size   : {format_bytes(current_size)}\n"
                f"Local upload size      : {format_bytes(local_upload_bytes)}\n"
                f"Need to free           : {format_bytes(required_free_bytes)}\n"
                f"Oldest bytes available : {format_bytes(selected_free_bytes)}\n"
                f"Max total size         : {upload.max_total_size}\n"
                "Upload was not started."
            )
            record_stage_failure(upload.remote_path, "reservation", detail)
            print_job_block("UPLOAD RESERVATION JOB", job_number, upload.remote_path, detail)
            return False

        result = run_command(
            [
                "rclone",
                "delete",
                "--files-from", str(delete_list_path),
                upload.remote_path,
            ] + get_delete_mode_options(upload),
            capture_output=True,
        )

        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += result.stderr

        if result.returncode != 0:
            detail = (
                f"Reservation delete failed on pass {reservation_pass}.\n"
                f"Return code: {result.returncode}\n"
                f"{command_error_summary(output)}"
            )
            record_stage_failure(upload.remote_path, "reservation", detail)
            print_job_block("UPLOAD RESERVATION JOB", job_number, upload.remote_path, detail)
            return False

        print_job_block(
            "UPLOAD RESERVATION JOB",
            job_number,
            upload.remote_path,
            (
                f"Reservation cleanup pass {reservation_pass} deleted oldest managed files.\n"
                f"Needed to free       : {format_bytes(required_free_bytes)}\n"
                f"Selected for deletion: {format_bytes(selected_free_bytes)}\n"
                "Selection guarantee  : selected >= needed\n"
                f"Safety target        : {format_bytes(RESERVATION_SAFETY_HEADROOM_BYTES)} below max_total_size\n"
                "Local and remote sizes will now be read again before upload."
            ),
        )

    local_size_result = get_filtered_local_upload_size(job_number, upload)
    if local_size_result is None:
        return False

    local_upload_bytes, local_file_count = local_size_result

    try:
        current_size_after = sum(file.size for file in get_upload_remote_quota_entries(upload))
    except Exception as error:
        detail = f"Failed re-reading managed remote size after reservation retries: {error}"
        record_stage_failure(upload.remote_path, "reservation", detail)
        print_job_block("UPLOAD RESERVATION JOB", job_number, upload.remote_path, detail)
        return False

    reserved_upload_bytes_after = transfer_cap_bytes(local_upload_bytes)
    projected_temporary_size = current_size_after + reserved_upload_bytes_after
    required_limit = max_total_size_bytes - RESERVATION_SAFETY_HEADROOM_BYTES

    detail = (
        "Size reservation did not stabilize within the configured cleanup-pass limit; "
        "upload was not started. The filtered local source may be growing faster than "
        "reservation can make room.\n"
        f"Cleanup pass limit        : {MAX_RESERVATION_CLEANUP_PASSES}\n"
        f"Filtered local files      : {local_file_count}\n"
        f"Managed size after cleanup: {format_bytes(current_size_after)}\n"
        f"Filtered local upload size: {format_bytes(local_upload_bytes)}\n"
        f"Reserved upload cap       : {format_bytes(reserved_upload_bytes_after)}\n"
        f"Projected temporary size  : {format_bytes(projected_temporary_size)}\n"
        f"Required projected maximum: {format_bytes(required_limit)}\n"
        f"Max total size            : {format_bytes(max_total_size_bytes)}"
    )
    record_stage_failure(upload.remote_path, "reservation", detail)
    print_job_block("UPLOAD RESERVATION JOB", job_number, upload.remote_path, detail)
    return False

# =============================================================================
# Final limit verification
# =============================================================================

def verify_one_cleanup_target(job_number: int, target: CleanupTarget) -> bool:
    """Verify max_files/max_size for one cleanup target."""
    if not target.delete_excess_files:
        return True
    if target.max_files is None and target.max_size is None:
        return True

    try:
        files = get_remote_file_entries(target.path)
    except Exception as error:
        detail = f"Verification listing failed: {error}"
        record_stage_failure(target.owner_remote_path, "final_quota", detail)
        print_job_block("VERIFY CLEANUP TARGET", job_number, target.path, detail)
        return False

    file_count = len(files)
    total_size = sum(file.size for file in files)
    failures: list[str] = []

    if target.max_files is not None and file_count > target.max_files:
        failures.append(f"file count {file_count} exceeds max_files {target.max_files}")

    if target.max_size is not None:
        max_size_bytes = parse_size_to_bytes(target.max_size)
        if total_size > max_size_bytes:
            failures.append(f"size {format_bytes(total_size)} exceeds max_size {target.max_size}")

    if failures:
        detail = "Final cleanup target verification FAILED:\n  " + "\n  ".join(failures)
        record_stage_failure(target.owner_remote_path, "final_quota", detail)
        print_job_block("VERIFY CLEANUP TARGET", job_number, target.path, detail)
        return False

    print_job_block(
        "VERIFY CLEANUP TARGET", job_number, target.path,
        f"Final cleanup target verification passed.\nFiles: {file_count}\nSize : {format_bytes(total_size)}",
    )
    return True

def verify_one_upload_remote_quota(job_number: int, upload: UploadDirectory) -> bool:
    """Verify the final managed size is at or below max_total_size."""
    if not upload.delete_excess_files or upload.max_total_size is None:
        return True

    try:
        files = get_upload_remote_quota_entries(upload)
    except Exception as error:
        detail = f"Final quota verification listing failed: {error}"
        record_stage_failure(upload.remote_path, "final_quota", detail)
        print_job_block("VERIFY REMOTE QUOTA", job_number, upload.remote_path, detail)
        return False

    total_size = sum(file.size for file in files)
    max_total_size_bytes = parse_size_to_bytes(upload.max_total_size)

    if total_size > max_total_size_bytes:
        detail = (
            "Final remote quota verification FAILED.\n"
            f"Managed size  : {format_bytes(total_size)}\n"
            f"Max total size: {format_bytes(max_total_size_bytes)}"
        )
        record_stage_failure(upload.remote_path, "final_quota", detail)
        print_job_block("VERIFY REMOTE QUOTA", job_number, upload.remote_path, detail)
        return False

    print_job_block(
        "VERIFY REMOTE QUOTA", job_number, upload.remote_path,
        (
            "Final remote quota verification passed.\n"
            f"Managed size  : {format_bytes(total_size)}\n"
            f"Max total size: {format_bytes(max_total_size_bytes)}"
        ),
    )
    return True

# =============================================================================
# Shared phase runners
# =============================================================================

def run_cleanup_phase(cleanup_directories: list[CleanupTarget], phase_name: str) -> bool:
    print_step(
        f"{phase_name}: cleaning remote folders using up to "
        f"{CLEANUP_THREADS} CLEANUP JOB(s)"
    )
    failed = False
    stage_name = "post_cleanup" if phase_name.startswith("POST-UPLOAD") else "reservation"

    with ThreadPoolExecutor(max_workers=CLEANUP_THREADS) as executor:
        future_to_target = {
            executor.submit(cleanup_one_directory, index, target, phase_name): target
            for index, target in enumerate(cleanup_directories, start=1)
        }

        for future in as_completed(future_to_target):
            target = future_to_target[future]
            try:
                success = future.result()
            except Exception as error:
                detail = f"{phase_name} cleanup job crashed: {error}"
                record_stage_failure(target.owner_remote_path, stage_name, detail)
                print_job_block("CLEANUP JOB", 0, target.path, detail)
                failed = True
                continue

            if not success:
                failed = True

    return not failed

def run_remote_quota_phase(phase_name: str) -> bool:
    print_step(
        f"{phase_name}: enforcing max_total_size using up to "
        f"{REMOTE_QUOTA_CLEANUP_THREADS} REMOTE QUOTA CLEANUP JOB(s)"
    )
    failed = False
    stage_name = "post_cleanup" if phase_name.startswith("POST-UPLOAD") else "reservation"

    with ThreadPoolExecutor(max_workers=REMOTE_QUOTA_CLEANUP_THREADS) as executor:
        future_to_upload = {
            executor.submit(cleanup_one_upload_remote_quota, index, upload, phase_name): upload
            for index, upload in enumerate(UPLOAD_DIRECTORIES, start=1)
        }

        for future in as_completed(future_to_upload):
            upload = future_to_upload[future]
            try:
                success = future.result()
            except Exception as error:
                detail = f"{phase_name} quota cleanup crashed: {error}"
                record_stage_failure(upload.remote_path, stage_name, detail)
                print_job_block("REMOTE QUOTA CLEANUP JOB", 0, upload.remote_path, detail)
                failed = True
                continue

            if not success:
                failed = True

    return not failed

def run_trash_cleanup_phase(phase_name: str) -> bool:
    print_step(
        f"{phase_name}: cleaning rclone trash using up to "
        f"{TRASH_CLEANUP_THREADS} TRASH CLEANUP JOB(s)"
    )
    failed = False
    stage_name = "post_cleanup" if phase_name.startswith("POST-UPLOAD") else "reservation"

    with ThreadPoolExecutor(max_workers=TRASH_CLEANUP_THREADS) as executor:
        future_to_upload = {
            executor.submit(cleanup_one_trash_remote, index, upload, phase_name): upload
            for index, upload in enumerate(UPLOAD_DIRECTORIES, start=1)
        }

        for future in as_completed(future_to_upload):
            upload = future_to_upload[future]
            try:
                success = future.result()
            except Exception as error:
                detail = f"{phase_name} trash cleanup crashed: {error}"
                record_stage_failure(upload.remote_path, stage_name, detail)
                print_job_block("TRASH CLEANUP JOB", 0, upload.remote_path, detail)
                failed = True
                continue

            if not success:
                failed = True

    return not failed

def reserve_and_upload_one_remote(job_number: int, upload: UploadDirectory) -> bool:
    """Size-reserve and upload one remote independently."""
    print_job_block(
        "REMOTE PIPELINE", job_number, upload.remote_path,
        "Starting independent reservation/upload pipeline",
    )

    if not reserve_one_upload_remote_space(job_number, upload):
        if get_stage_result(upload.remote_path, "reservation").status == "PENDING":
            record_stage_failure(upload.remote_path, "reservation", "Reservation failed")
        record_stage_skipped(upload.remote_path, "upload")
        print_job_block(
            "REMOTE PIPELINE", job_number, upload.remote_path,
            "Reservation failed; upload for this remote was not started",
        )
        return False

    if not cleanup_one_trash_remote(job_number, upload, "POST-RESERVATION TRASH CLEANUP"):
        if get_stage_result(upload.remote_path, "reservation").status != "FAILED":
            record_stage_failure(
                upload.remote_path, "reservation",
                "Post-reservation trash cleanup failed; upload was not started",
            )
        record_stage_skipped(upload.remote_path, "upload")
        print_job_block(
            "REMOTE PIPELINE", job_number, upload.remote_path,
            "Post-reservation trash cleanup failed; upload for this remote was not started",
        )
        return False

    if SLEEP_AFTER_STEP > 0:
        print_job_block(
            "REMOTE PIPELINE", job_number, upload.remote_path,
            f"Reservation complete; sleeping {SLEEP_AFTER_STEP}s before starting this upload",
        )
        time.sleep(SLEEP_AFTER_STEP)

    if not upload_one_directory(job_number, upload):
        print_job_block(
            "REMOTE PIPELINE", job_number, upload.remote_path,
            "Upload failed. Global post-upload cleanup and verification will still run.",
        )
        return False

    print_job_block(
        "REMOTE PIPELINE", job_number, upload.remote_path,
        "Reservation and upload finished successfully for this remote",
    )
    return True

def run_reservation_and_upload_phase() -> bool:
    """Run independent reservation/upload pipelines concurrently."""
    print_step(
        f"Starting independent reservation/upload pipelines using up to "
        f"{UPLOAD_THREADS} REMOTE PIPELINE JOB(s)"
    )
    failed = False

    with ThreadPoolExecutor(max_workers=UPLOAD_THREADS) as executor:
        future_to_upload = {
            executor.submit(reserve_and_upload_one_remote, index, upload): upload
            for index, upload in enumerate(UPLOAD_DIRECTORIES, start=1)
        }

        for future in as_completed(future_to_upload):
            upload = future_to_upload[future]
            try:
                success = future.result()
            except Exception as error:
                reservation_stage = get_stage_result(upload.remote_path, "reservation")
                stage_name = "reservation" if reservation_stage.status == "PENDING" else "upload"
                detail = f"Reservation/upload pipeline crashed: {error}"
                record_stage_failure(upload.remote_path, stage_name, detail)
                if stage_name == "reservation":
                    record_stage_skipped(upload.remote_path, "upload")
                print_job_block("REMOTE PIPELINE", 0, upload.remote_path, detail)
                failed = True
                continue

            if not success:
                failed = True

    return not failed

def run_final_verification(cleanup_directories: list[CleanupTarget]) -> bool:
    print_step("Verifying final cleanup rule and max_total_size limits")
    failed = False

    with ThreadPoolExecutor(max_workers=CLEANUP_THREADS) as executor:
        future_to_target = {
            executor.submit(verify_one_cleanup_target, index, target): target
            for index, target in enumerate(cleanup_directories, start=1)
        }

        for future in as_completed(future_to_target):
            target = future_to_target[future]
            try:
                if not future.result():
                    failed = True
            except Exception as error:
                detail = f"Final target verification crashed: {error}"
                record_stage_failure(target.owner_remote_path, "final_quota", detail)
                print_job_block("VERIFY CLEANUP TARGET", 0, target.path, detail)
                failed = True

    with ThreadPoolExecutor(max_workers=REMOTE_QUOTA_CLEANUP_THREADS) as executor:
        future_to_upload = {
            executor.submit(verify_one_upload_remote_quota, index, upload): upload
            for index, upload in enumerate(UPLOAD_DIRECTORIES, start=1)
        }

        for future in as_completed(future_to_upload):
            upload = future_to_upload[future]
            try:
                if not future.result():
                    failed = True
            except Exception as error:
                detail = f"Final quota verification crashed: {error}"
                record_stage_failure(upload.remote_path, "final_quota", detail)
                print_job_block("VERIFY REMOTE QUOTA", 0, upload.remote_path, detail)
                failed = True

    finalize_stage_for_all("final_quota")
    return not failed

# =============================================================================
# Upload jobs
# =============================================================================

def print_thread_output(thread_number: int, remote_path: str, line: str):
    """
    Print one line of output from one upload job.
    """
    line = line.rstrip()

    if not line:
        return

    with OUTPUT_LOCK:
        print()
        print(OUTPUT_SEPARATOR)
        print(f"UPLOAD JOB {thread_number}")
        print(f"Remote: {remote_path}")
        print(OUTPUT_SEPARATOR)
        print(line)
        print(OUTPUT_SEPARATOR)
        print(flush=True)


def run_command_streamed(
    command: list[str],
    thread_number: int,
    remote_path: str,
) -> tuple[int, str]:
    """Run a command, stream merged stdout/stderr, and retain it for failures."""
    with OUTPUT_LOCK:
        print()
        print(OUTPUT_SEPARATOR)
        print(f"STARTING UPLOAD JOB {thread_number}")
        print(f"Remote: {remote_path}")
        print(f"Command: {' '.join(command)}")
        print(OUTPUT_SEPARATOR)
        print(flush=True)

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    captured_lines: list[str] = []
    if process.stdout is not None:
        for line in process.stdout:
            captured_lines.append(line.rstrip("\n"))
            print_thread_output(thread_number, remote_path, line)

    return process.wait(), "\n".join(captured_lines)

def get_upload_buffer_options(upload: UploadDirectory) -> list[str]:
    """Return the optional per-remote rclone --buffer-size arguments."""
    if upload.buffer_size is None:
        return []

    return ["--buffer-size", upload.buffer_size]


def upload_one_directory(job_number: int, upload: UploadDirectory) -> bool:
    """Upload one local directory to one remote destination."""
    valid, error = validate_local_upload_path(upload)
    if not valid:
        detail = error or "Invalid local upload path"
        record_stage_failure(upload.remote_path, "upload", detail)
        print_job_block("UPLOAD JOB", job_number, upload.remote_path, detail)
        return False

    try:
        upload_command = validate_upload_command(upload.upload_command)
    except ValueError as error:
        detail = f"Failed before starting: {error}"
        record_stage_failure(upload.remote_path, "upload", detail)
        print_job_block("UPLOAD JOB", job_number, upload.remote_path, detail)
        return False

    upload_delete_options = get_delete_mode_options(upload) if upload_command == "sync" else []
    transfer_cap_options: list[str] = []
    buffer_options = get_upload_buffer_options(upload)

    with RESERVED_UPLOAD_BYTES_LOCK:
        reserved_upload_bytes = RESERVED_UPLOAD_BYTES.get(upload.remote_path)

    if upload.max_total_size is not None and reserved_upload_bytes is not None and reserved_upload_bytes > 0:
        transfer_cap_options = [
            "--max-transfer", f"{transfer_cap_bytes(reserved_upload_bytes)}B",
            "--cutoff-mode", "CAUTIOUS",
        ]

    command = [
        "rclone", upload_command, upload.local_path, upload.remote_path,
    ] + upload_delete_options + upload.copy_options + buffer_options + transfer_cap_options

    return_code, command_output = run_command_streamed(
        command=command,
        thread_number=job_number,
        remote_path=upload.remote_path,
    )

    if return_code != 0:
        detail = (
            f"Command: {' '.join(command)}\n"
            f"Return code: {return_code}\n"
            f"{command_error_summary(command_output)}"
        )
        record_stage_failure(upload.remote_path, "upload", detail)
        print_job_block("UPLOAD JOB", job_number, upload.remote_path, f"Upload failed.\n{detail}")
        return False

    record_stage_success(upload.remote_path, "upload")
    print_job_block(
        "UPLOAD JOB", job_number, upload.remote_path,
        f"Upload finished successfully with rclone {upload_command}",
    )
    return True

# =============================================================================
# Main
# =============================================================================

def main() -> int:
    args = parse_cli_args()

    try:
        load_config(args.config)
    except Exception as error:
        print_error(f"Failed loading config: {error}")
        return 1

    cleanup_directories = build_cleanup_directories()
    initialize_run_results()

    if not args.validate_config:
        atexit.register(release_lock)
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        acquire_lock()

    print_startup_summary(cleanup_directories)

    if args.validate_config:
        print_step(
            "Config validation successful; no lock file, cleanup, local sizing, "
            "trash cleanup, or upload was started"
        )
        return 0

    print_step("Lockfile doesn't exist, script is starting")

    if not run_cleanup_phase(cleanup_directories, "PRE-UPLOAD CLEANUP"):
        print_error("One or more pre-upload cleanup_rules jobs failed")
        mark_pending_stages_skipped()
        print_final_run_result(1)
        return 1

    if not run_trash_cleanup_phase("PRE-UPLOAD TRASH CLEANUP"):
        print_error("One or more pre-upload trash cleanup jobs failed")
        mark_pending_stages_skipped()
        print_final_run_result(1)
        return 1

    sleep_after_step()

    upload_success = run_reservation_and_upload_phase()

    post_cleanup_success = run_cleanup_phase(cleanup_directories, "POST-UPLOAD CLEANUP")
    post_quota_success = run_remote_quota_phase("POST-UPLOAD QUOTA CLEANUP")
    post_trash_success = run_trash_cleanup_phase("POST-UPLOAD TRASH CLEANUP")
    finalize_stage_for_all("post_cleanup")

    verification_success = run_final_verification(cleanup_directories)

    if not post_cleanup_success:
        print_error("One or more post-upload cleanup_rules jobs failed")
    if not post_quota_success:
        print_error("One or more post-upload max_total_size cleanup jobs failed")
    if not post_trash_success:
        print_error("One or more post-upload trash cleanup jobs failed")
    if not verification_success:
        print_error("Final cleanup/quota verification failed")
    if not upload_success:
        print_error(
            "One or more reservation/upload pipelines failed. Post-upload cleanup and final verification "
            "were still run because a failed upload may have transferred partial data."
        )

    overall_success = all(
        [
            upload_success,
            post_cleanup_success,
            post_quota_success,
            post_trash_success,
            verification_success,
        ]
    )
    exit_code = 0 if overall_success else 1

    if overall_success:
        print_step("Upload, post-upload cleanup, and final quota verification successful")

    print_final_run_result(exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
