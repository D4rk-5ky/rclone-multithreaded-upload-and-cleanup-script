#!/usr/bin/env python3

# =============================================================================
# Rclone CCTV Multi-Remote Upload and Cleanup Script
# =============================================================================
#
# GitHub-safe external-config version.
#
# What this script does:
#   1. Loads settings from a JSON config file passed with --config/-c.
#
#   2. Builds remote cleanup targets from:
#        UPLOAD_DIRECTORIES remote_path
#        +
#        DIRECTORY_CLEANUP_RULES camera folder paths
#
#   3. Cleans remote camera folders:
#        - optionally delete files older than DELETE_MIN_AGE
#        - optionally delete oldest files until max_files/max_size is satisfied
#
#   4. Optionally runs "rclone cleanup" per upload remote.
#
#   5. Uploads local CCTV files to multiple remotes using rclone copy/sync/move.
#
# Important design:
#   - Runtime settings live in the external JSON config file.
#   - DIRECTORY_CLEANUP_RULES controls folder names and retention limits.
#   - UPLOAD_DIRECTORIES controls remote/cloud behavior:
#       delete_old_files
#       delete_excess_files
#       delete_to_trash
#       empty_trash
#       upload_command
#
# WARNING:
#   This script can delete files.
#   Test with safe data and/or --dry-run before using on important data.
# =============================================================================

import argparse
import atexit
import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
import threading


VERSION = "0.0.2"


# =============================================================================
# Dataclasses
# =============================================================================

@dataclass
class DirectoryCleanupRule:
    """
    One camera/folder rule.

    This only describes:
      - the folder path relative to each upload remote root
      - the max file count and/or max size for this folder

    It does NOT decide whether files are deleted directly, moved to trash,
    or whether old/excess cleanup is enabled. Those settings belong to
    UploadDirectory, because they are cloud/remote-specific.
    """
    path: str
    max_files: int | None = None
    max_size: str | None = None


@dataclass
class UploadDirectory:
    """
    One upload remote/cloud destination.

    This controls:
      - where to upload
      - how to upload: copy, sync, or move
      - how cleanup behaves on this remote
    """
    local_path: str
    remote_path: str
    copy_options: list[str]

    # Safe choices: copy, sync, move
    upload_command: str = "copy"

    # Cleanup behavior for this upload remote/cloud.
    delete_old_files: bool = True
    delete_excess_files: bool = True

    # Optional total size limit for this upload remote.
    # This is checked across all configured DIRECTORY_CLEANUP_RULES below this
    # upload.remote_path. It does not inspect unrelated folders outside the
    # configured camera folders.
    #
    # Example: "500G", "1T", "200G"
    max_total_size: str | None = None

    # False = add --drive-use-trash=false where supported.
    # True  = do not add --drive-use-trash=false; use backend default/trash behavior.
    delete_to_trash: bool = False

    # Run "rclone cleanup remote:/path" for this remote.
    # This empties trash/recycle bin where the backend supports it.
    empty_trash: bool = True


@dataclass
class CleanupTarget:
    """
    A generated full remote cleanup target.

    This is created by combining:
      UploadDirectory.remote_path
      +
      DirectoryCleanupRule.path

    Retention limits come from DirectoryCleanupRule.
    Delete behavior comes from UploadDirectory.
    """
    path: str
    max_files: int | None = None
    max_size: str | None = None
    delete_old_files: bool = True
    delete_excess_files: bool = True
    delete_to_trash: bool = False


@dataclass
class RemoteFile:
    path: str
    size: int
    modified: str


@dataclass
class RemoteQuotaFile:
    """
    File entry used for per-upload-remote total size cleanup.

    path is relative to UploadDirectory.remote_path, so it can be used with:
      rclone delete --files-from LIST upload.remote_path
    """
    path: str
    size: int
    modified: str
    source_folder: str


# =============================================================================
# Configuration loaded from external JSON file
# =============================================================================

# Runtime configuration is intentionally populated from --config/-c.
# The script no longer contains upload destinations or cleanup folders as
# hard-coded settings. This keeps private paths/remotes out of the script and
# makes it safer to reuse the same script with different config files.
DIRECTORY_CLEANUP_RULES: list[DirectoryCleanupRule] = []
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


# Tracks whether this process successfully created the lock file.
lock_created = False


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
    Join an rclone remote root with a relative path.

    Example:
      Example-GoogleDrive-Encrypted:/CCTV
      Home/Camera01

    Becomes:
      Example-GoogleDrive-Encrypted:/CCTV/Home/Camera01
    """
    return f"{remote_root.rstrip('/')}/{relative_path.lstrip('/')}"


def join_relative_path(base: str, child: str) -> str:
    """
    Join two relative paths for use inside --files-from.
    """
    return f"{base.rstrip('/')}/{child.lstrip('/')}"


def get_delete_mode_options(target: CleanupTarget | UploadDirectory) -> list[str]:
    """
    Return rclone delete options for direct-delete or trash/default mode.

    delete_to_trash=False:
      Adds --drive-use-trash=false.
      This means direct/permanent delete where supported.

    delete_to_trash=True:
      Does not add --drive-use-trash=false.
      This lets rclone/backend use default trash behavior where supported.
    """
    if target.delete_to_trash:
        return []

    return ["--drive-use-trash=false"]


def get_delete_mode_text(target: CleanupTarget | UploadDirectory) -> str:
    if target.delete_to_trash:
        return "trash/backend default"

    return "direct/permanent where supported"



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


def parse_directory_cleanup_rules(config: dict) -> list[DirectoryCleanupRule]:
    """
    Convert config directory_cleanup_rules objects into DirectoryCleanupRule instances.
    """
    raw_rules = config.get("directory_cleanup_rules")

    if not isinstance(raw_rules, list):
        raise ValueError("directory_cleanup_rules must be a list")

    rules: list[DirectoryCleanupRule] = []

    for index, raw_rule in enumerate(raw_rules, start=1):
        section_name = f"directory_cleanup_rules[{index}]"

        if not isinstance(raw_rule, dict):
            raise ValueError(f"{section_name} must be an object")

        path = require_string(section_name, raw_rule, "path")
        max_files = optional_positive_int_or_none(section_name, raw_rule, "max_files")
        max_size = optional_string(section_name, raw_rule, "max_size", None)

        if max_size is not None:
            parse_size_to_bytes(max_size)

        rules.append(
            DirectoryCleanupRule(
                path=path,
                max_files=max_files,
                max_size=max_size,
            )
        )

    return rules


def parse_upload_directories(config: dict) -> list[UploadDirectory]:
    """
    Convert config upload_directories objects into UploadDirectory instances.
    """
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
        upload_command = validate_upload_command(
            optional_string(section_name, raw_upload, "upload_command", "copy")
        )
        delete_old_files = optional_bool(section_name, raw_upload, "delete_old_files", True)
        delete_excess_files = optional_bool(section_name, raw_upload, "delete_excess_files", True)
        max_total_size = optional_string(section_name, raw_upload, "max_total_size", None)
        delete_to_trash = optional_bool(section_name, raw_upload, "delete_to_trash", False)
        empty_trash = optional_bool(section_name, raw_upload, "empty_trash", True)
        copy_options = optional_string_list(section_name, raw_upload, "copy_options")

        if max_total_size is not None:
            parse_size_to_bytes(max_total_size)

        uploads.append(
            UploadDirectory(
                local_path=local_path,
                remote_path=remote_path,
                copy_options=copy_options,
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
    """
    Load external config and copy its values into the existing global settings.

    Existing functions intentionally keep using the same global names as before.
    That preserves the original execution logic while moving the editable values
    into the config file.
    """
    global CONFIG_PATH
    global DIRECTORY_CLEANUP_RULES
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

    DIRECTORY_CLEANUP_RULES = parse_directory_cleanup_rules(config)
    UPLOAD_DIRECTORIES = parse_upload_directories(config)

    DELETE_MIN_AGE = optional_string("root", config, "delete_min_age", DELETE_MIN_AGE)

    thread_limits = config.get("thread_limits", {})
    if not isinstance(thread_limits, dict):
        raise ValueError("thread_limits must be an object")

    UPLOAD_THREADS = optional_non_negative_int("thread_limits", thread_limits, "upload_threads", UPLOAD_THREADS)
    CLEANUP_THREADS = optional_non_negative_int("thread_limits", thread_limits, "cleanup_threads", CLEANUP_THREADS)
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

    LOCK_FILE = Path(optional_string("root", config, "lock_file", str(LOCK_FILE))).expanduser()
    DELETE_LIST_DIR = Path(optional_string("root", config, "delete_list_dir", str(DELETE_LIST_DIR))).expanduser()
    SLEEP_AFTER_STEP = optional_non_negative_int("root", config, "sleep_after_step", SLEEP_AFTER_STEP)
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
    Build cleanup targets from every upload remote and every camera folder.

    Retention limits come from DIRECTORY_CLEANUP_RULES.
    Delete behavior comes from UPLOAD_DIRECTORIES.
    """
    cleanup_directories: list[CleanupTarget] = []

    for upload in UPLOAD_DIRECTORIES:
        for directory in DIRECTORY_CLEANUP_RULES:
            cleanup_directories.append(
                CleanupTarget(
                    path=join_rclone_remote_path(upload.remote_path, directory.path),
                    max_files=directory.max_files,
                    max_size=directory.max_size,
                    delete_old_files=upload.delete_old_files,
                    delete_excess_files=upload.delete_excess_files,
                    delete_to_trash=upload.delete_to_trash,
                )
            )

    return cleanup_directories


# =============================================================================
# Startup summary
# =============================================================================

def print_startup_summary(cleanup_directories: list[CleanupTarget]):
    """
    Print a summary of what the script is configured to do before it starts.
    """
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
        print("Thread limits:")
        print(f"  Per-folder cleanup jobs   : {CLEANUP_THREADS}")
        print(f"  Remote quota cleanup jobs : {REMOTE_QUOTA_CLEANUP_THREADS}")
        print(f"  Trash cleanup jobs        : {TRASH_CLEANUP_THREADS}")
        print(f"  Upload jobs               : {UPLOAD_THREADS}")

        print()
        print("Global cleanup settings:")
        print(f"  Delete min age     : {DELETE_MIN_AGE}")
        print(f"  Lock file          : {LOCK_FILE}")
        print(f"  Delete-list folder : {DELETE_LIST_DIR}")

        print()
        print("Directory cleanup rules:")
        for index, directory in enumerate(DIRECTORY_CLEANUP_RULES, start=1):
            print(f"  Camera rule {index}:")
            print(f"    Relative path : {directory.path}")
            print(f"    Max files     : {directory.max_files}")
            print(f"    Max size      : {directory.max_size}")

        print()
        print("Upload destinations and remote cleanup behavior:")
        for index, upload in enumerate(UPLOAD_DIRECTORIES, start=1):
            try:
                upload_command = validate_upload_command(upload.upload_command)
            except ValueError:
                upload_command = f"INVALID: {upload.upload_command}"

            print(f"  Upload destination {index}:")
            print(f"    Local path          : {upload.local_path}")
            print(f"    Remote path         : {upload.remote_path}")
            print(f"    Upload command      : rclone {upload_command}")
            print(f"    Delete old files    : {upload.delete_old_files}")
            print(f"    Delete excess files : {upload.delete_excess_files}")
            print(f"    Remote max total    : {upload.max_total_size}")
            print(f"    Delete mode         : {get_delete_mode_text(upload)}")
            print(f"    Empty trash         : {upload.empty_trash}")
            print(f"    Upload options      : {' '.join(upload.copy_options)}")

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

        print()
        print("Remote-wide quota cleanup:")
        for index, upload in enumerate(UPLOAD_DIRECTORIES, start=1):
            print(f"  Remote quota target {index}:")
            print(f"    Remote path    : {upload.remote_path}")
            print(f"    Enabled        : {upload.delete_excess_files and upload.max_total_size is not None}")
            print(f"    Max total size : {upload.max_total_size}")
            print(f"    Delete mode    : {get_delete_mode_text(upload)}")
            print(f"    Counts folders        : {[directory.path for directory in DIRECTORY_CLEANUP_RULES]}")

        print()
        print(f"Total upload destinations : {len(UPLOAD_DIRECTORIES)}")
        print(f"Total directory cleanup rules   : {len(DIRECTORY_CLEANUP_RULES)}")
        print(f"Total cleanup targets     : {len(cleanup_directories)}")

        print(OUTPUT_SEPARATOR)
        print(flush=True)


# =============================================================================
# Remote file listing and delete-list creation
# =============================================================================

def get_remote_file_entries(remote_path: str) -> list[RemoteFile]:
    """
    Get remote files with modified time, size, and path.

    Uses:
      rclone lsf --files-only --format tsp --separator TAB

    Format:
      t = modified time
      s = size
      p = path
    """
    result = run_command(
        [
            "rclone",
            "lsf",
            "--files-only",
            "--format", "tsp",
            "--separator", "\t",
            remote_path,
        ],
        capture_output=True,
    )

    if result.returncode != 0:
        if is_directory_not_found(result):
            print_step(f"Remote folder does not exist yet, skipping list: {remote_path}")
            return []

        if result.stdout:
            print(result.stdout)

        if result.stderr:
            print(result.stderr)

        raise RuntimeError(f"Failed listing files in {remote_path}")

    files: list[RemoteFile] = []

    for line in result.stdout.splitlines():
        line = line.strip()

        if not line:
            continue

        parts = line.split("\t", 2)

        if len(parts) != 3:
            print_error(f"Could not parse rclone lsf line: {line}")
            continue

        modified, size_text, path = parts

        try:
            size = int(size_text)
        except ValueError:
            print_error(f"Could not parse file size from line: {line}")
            continue

        files.append(
            RemoteFile(
                path=path,
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
        key=lambda item: (item.modified, item.path),
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
    Get all managed files below one upload remote across all configured camera folders.

    This only counts folders listed in DIRECTORY_CLEANUP_RULES.
    It does not count unrelated folders/files elsewhere in the cloud account.
    """
    all_files: list[RemoteQuotaFile] = []

    for directory in DIRECTORY_CLEANUP_RULES:
        full_remote_path = join_rclone_remote_path(upload.remote_path, directory.path)
        entries = get_remote_file_entries(full_remote_path)

        for file in entries:
            all_files.append(
                RemoteQuotaFile(
                    path=join_relative_path(directory.path, file.path),
                    size=file.size,
                    modified=file.modified,
                    source_folder=directory.path,
                )
            )

    return all_files


def make_upload_remote_quota_delete_list(upload: UploadDirectory) -> Path:
    """
    Create a --files-from list for remote-wide quota cleanup.

    This enforces upload.max_total_size across all configured camera folders
    below upload.remote_path.
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
        key=lambda item: (item.modified, item.path),
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

def cleanup_one_directory(job_number: int, target: CleanupTarget) -> bool:
    """
    Cleanup one generated remote camera directory.

    Optional steps are controlled by UploadDirectory and copied into CleanupTarget:
      1. delete_old_files
      2. delete_excess_files
    """
    print_job_block(
        "CLEANUP JOB",
        job_number,
        target.path,
        "Starting cleanup job",
    )

    if not target.delete_old_files and not target.delete_excess_files:
        print_job_block(
            "CLEANUP JOB",
            job_number,
            target.path,
            "delete_old_files=False and delete_excess_files=False, skipping",
        )
        return True

    # ----------------------------
    # Optional age-based delete
    # ----------------------------

    if target.delete_old_files:
        result = run_command(
            [
                "rclone",
                "delete",
                target.path,
                "--min-age", DELETE_MIN_AGE,
            ] + get_delete_mode_options(target),
            capture_output=True,
        )

        output = ""

        if result.stdout:
            output += result.stdout

        if result.stderr:
            output += result.stderr

        if output:
            print_job_block("CLEANUP JOB", job_number, target.path, output)

        if result.returncode != 0:
            if is_directory_not_found(result):
                print_job_block(
                    "CLEANUP JOB",
                    job_number,
                    target.path,
                    "Remote folder does not exist yet, skipping cleanup for this folder",
                )
                return True

            print_job_block(
                "CLEANUP JOB",
                job_number,
                target.path,
                f"Failed deleting old files. Return code: {result.returncode}",
            )
            return False
    else:
        print_job_block(
            "CLEANUP JOB",
            job_number,
            target.path,
            "Skipping delete_old_files for this target",
        )

    # ----------------------------
    # Optional max-files/max-size delete
    # ----------------------------

    if not target.delete_excess_files:
        print_job_block(
            "CLEANUP JOB",
            job_number,
            target.path,
            "Skipping delete_excess_files for this target",
        )
        return True

    if target.max_files is None and target.max_size is None:
        print_job_block(
            "CLEANUP JOB",
            job_number,
            target.path,
            "delete_excess_files=True but no max_files or max_size is set, skipping",
        )
        return True

    try:
        delete_list_path = make_delete_list(target)
    except Exception as error:
        print_job_block(
            "CLEANUP JOB",
            job_number,
            target.path,
            f"Failed making delete list: {error}",
        )
        return False

    if delete_list_path.stat().st_size == 0:
        print_job_block(
            "CLEANUP JOB",
            job_number,
            target.path,
            "No excessive files to delete",
        )
        return True

    result = run_command(
        [
            "rclone",
            "delete",
            "--files-from", str(delete_list_path),
            target.path,
        ] + get_delete_mode_options(target),
        capture_output=True,
    )

    output = ""

    if result.stdout:
        output += result.stdout

    if result.stderr:
        output += result.stderr

    if output:
        print_job_block("CLEANUP JOB", job_number, target.path, output)

    if result.returncode != 0:
        print_job_block(
            "CLEANUP JOB",
            job_number,
            target.path,
            f"Failed deleting excessive files. Return code: {result.returncode}",
        )
        return False

    print_job_block(
        "CLEANUP JOB",
        job_number,
        target.path,
        "Cleanup job finished successfully",
    )

    return True


def cleanup_one_upload_remote_quota(job_number: int, upload: UploadDirectory) -> bool:
    """
    Enforce upload.max_total_size across all configured DIRECTORY_CLEANUP_RULES
    below upload.remote_path.
    """
    print_job_block(
        "REMOTE QUOTA CLEANUP JOB",
        job_number,
        upload.remote_path,
        "Starting remote-wide quota cleanup job",
    )

    if not upload.delete_excess_files:
        print_job_block(
            "REMOTE QUOTA CLEANUP JOB",
            job_number,
            upload.remote_path,
            "delete_excess_files=False, skipping remote-wide quota cleanup",
        )
        return True

    if upload.max_total_size is None:
        print_job_block(
            "REMOTE QUOTA CLEANUP JOB",
            job_number,
            upload.remote_path,
            "max_total_size=None, skipping remote-wide quota cleanup",
        )
        return True

    try:
        delete_list_path = make_upload_remote_quota_delete_list(upload)
    except Exception as error:
        print_job_block(
            "REMOTE QUOTA CLEANUP JOB",
            job_number,
            upload.remote_path,
            f"Failed making remote quota delete list: {error}",
        )
        return False

    if delete_list_path.stat().st_size == 0:
        print_job_block(
            "REMOTE QUOTA CLEANUP JOB",
            job_number,
            upload.remote_path,
            "No remote-wide quota files to delete",
        )
        return True

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

    if output:
        print_job_block("REMOTE QUOTA CLEANUP JOB", job_number, upload.remote_path, output)

    if result.returncode != 0:
        print_job_block(
            "REMOTE QUOTA CLEANUP JOB",
            job_number,
            upload.remote_path,
            f"Failed deleting remote-wide quota files. Return code: {result.returncode}",
        )
        return False

    print_job_block(
        "REMOTE QUOTA CLEANUP JOB",
        job_number,
        upload.remote_path,
        "Remote-wide quota cleanup finished successfully",
    )

    return True


def cleanup_one_trash_remote(job_number: int, upload: UploadDirectory) -> bool:
    """
    Optionally run rclone cleanup once for one upload remote.

    Controlled by:
      upload.empty_trash
    """
    if not upload.empty_trash:
        print_job_block(
            "TRASH CLEANUP JOB",
            job_number,
            upload.remote_path,
            "empty_trash=False, skipping rclone cleanup for this remote",
        )
        return True

    print_job_block(
        "TRASH CLEANUP JOB",
        job_number,
        upload.remote_path,
        "Starting rclone cleanup / empty trash",
    )

    result = run_command(
        ["rclone", "cleanup", upload.remote_path],
        capture_output=True,
    )

    output = ""

    if result.stdout:
        output += result.stdout

    if result.stderr:
        output += result.stderr

    if result.returncode != 0:
        if "not supported" in output.lower() or "doesn't support" in output.lower():
            print_job_block(
                "TRASH CLEANUP JOB",
                job_number,
                upload.remote_path,
                f"rclone cleanup is not supported for this remote, skipping.\n\n{output}",
            )
            return True

        print_job_block(
            "TRASH CLEANUP JOB",
            job_number,
            upload.remote_path,
            f"Trash cleanup failed. Return code: {result.returncode}\n\n{output}",
        )
        return False

    if output:
        print_job_block(
            "TRASH CLEANUP JOB",
            job_number,
            upload.remote_path,
            output,
        )

    print_job_block(
        "TRASH CLEANUP JOB",
        job_number,
        upload.remote_path,
        "Trash cleanup finished successfully",
    )

    return True


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


def run_command_streamed(command: list[str], thread_number: int, remote_path: str) -> int:
    """
    Run a command and stream stdout/stderr line by line.

    stderr is merged into stdout so rclone output stays in the correct order.
    """
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

    if process.stdout is not None:
        for line in process.stdout:
            print_thread_output(thread_number, remote_path, line)

    return process.wait()


def upload_one_directory(job_number: int, upload: UploadDirectory) -> bool:
    """
    Upload one local directory to one remote destination.

    upload.upload_command controls whether rclone uses:
      copy, sync, or move
    """
    local_path = Path(upload.local_path)

    if not local_path.exists():
        with OUTPUT_LOCK:
            print()
            print(OUTPUT_SEPARATOR)
            print(f"UPLOAD JOB {job_number} FAILED BEFORE STARTING")
            print(f"Remote: {upload.remote_path}")
            print(f"Local path does not exist: {upload.local_path}")
            print(OUTPUT_SEPARATOR)
            print(flush=True)

        return False

    if not local_path.is_dir():
        with OUTPUT_LOCK:
            print()
            print(OUTPUT_SEPARATOR)
            print(f"UPLOAD JOB {job_number} FAILED BEFORE STARTING")
            print(f"Remote: {upload.remote_path}")
            print(f"Local path is not a directory: {upload.local_path}")
            print(OUTPUT_SEPARATOR)
            print(flush=True)

        return False

    try:
        upload_command = validate_upload_command(upload.upload_command)
    except ValueError as error:
        with OUTPUT_LOCK:
            print()
            print(OUTPUT_SEPARATOR)
            print(f"UPLOAD JOB {job_number} FAILED BEFORE STARTING")
            print(f"Remote: {upload.remote_path}")
            print(f"Error: {error}")
            print(OUTPUT_SEPARATOR)
            print(flush=True)

        return False

    command = [
        "rclone",
        upload_command,
        upload.local_path,
        upload.remote_path,
    ] + upload.copy_options

    return_code = run_command_streamed(
        command=command,
        thread_number=job_number,
        remote_path=upload.remote_path,
    )

    if return_code != 0:
        with OUTPUT_LOCK:
            print()
            print(OUTPUT_SEPARATOR)
            print(f"UPLOAD JOB {job_number} FAILED")
            print(f"Remote: {upload.remote_path}")
            print(f"Command: rclone {upload_command}")
            print(f"Return code: {return_code}")
            print(OUTPUT_SEPARATOR)
            print(flush=True)

        return False

    with OUTPUT_LOCK:
        print()
        print(OUTPUT_SEPARATOR)
        print(f"UPLOAD JOB {job_number} FINISHED SUCCESSFULLY")
        print(f"Remote: {upload.remote_path}")
        print(f"Command: rclone {upload_command}")
        print(OUTPUT_SEPARATOR)
        print(flush=True)

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

    if not args.validate_config:
        atexit.register(release_lock)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        acquire_lock()

    print_startup_summary(cleanup_directories)

    if args.validate_config:
        print_step("Config validation successful; no lock file, cleanup, trash cleanup, or upload was started")
        return 0

    print_step("Lockfile doesn't exist, script is starting")

    # ----------------------------
    # Phase 1: Cleanup remote camera folders
    # ----------------------------

    print_step(
        f"Cleaning remote camera folders using up to "
        f"{CLEANUP_THREADS} CLEANUP JOB(s)"
    )

    cleanup_failed = False

    with ThreadPoolExecutor(max_workers=CLEANUP_THREADS) as executor:
        future_to_cleanup = {
            executor.submit(cleanup_one_directory, index, target): target
            for index, target in enumerate(cleanup_directories, start=1)
        }

        for future in as_completed(future_to_cleanup):
            target = future_to_cleanup[future]

            try:
                success = future.result()
            except Exception as error:
                print_job_block(
                    "CLEANUP JOB",
                    0,
                    target.path,
                    f"Cleanup job crashed: {error}",
                )
                cleanup_failed = True
                continue

            if not success:
                cleanup_failed = True

    if cleanup_failed:
        print_error("One or more cleanup jobs failed")
        sys.exit(1)


    # ----------------------------
    # Phase 2: Per-upload-remote total quota cleanup
    # ----------------------------

    print_step(
        f"Cleaning remote-wide quota using up to "
        f"{REMOTE_QUOTA_CLEANUP_THREADS} REMOTE QUOTA CLEANUP JOB(s)"
    )

    remote_quota_cleanup_failed = False

    with ThreadPoolExecutor(max_workers=REMOTE_QUOTA_CLEANUP_THREADS) as executor:
        future_to_remote_quota_cleanup = {
            executor.submit(cleanup_one_upload_remote_quota, index, upload): upload
            for index, upload in enumerate(UPLOAD_DIRECTORIES, start=1)
        }

        for future in as_completed(future_to_remote_quota_cleanup):
            upload = future_to_remote_quota_cleanup[future]

            try:
                success = future.result()
            except Exception as error:
                print_job_block(
                    "REMOTE QUOTA CLEANUP JOB",
                    0,
                    upload.remote_path,
                    f"Remote quota cleanup crashed: {error}",
                )
                remote_quota_cleanup_failed = True
                continue

            if not success:
                remote_quota_cleanup_failed = True

    if remote_quota_cleanup_failed:
        print_error("One or more remote quota cleanup jobs failed")
        sys.exit(1)

    # ----------------------------
    # Phase 3: Empty remote trash/recycle bin
    # ----------------------------

    print_step(
        f"Cleaning rclone trash using up to "
        f"{TRASH_CLEANUP_THREADS} TRASH CLEANUP JOB(s)"
    )

    trash_cleanup_failed = False

    with ThreadPoolExecutor(max_workers=TRASH_CLEANUP_THREADS) as executor:
        future_to_trash_cleanup = {
            executor.submit(cleanup_one_trash_remote, index, upload): upload
            for index, upload in enumerate(UPLOAD_DIRECTORIES, start=1)
        }

        for future in as_completed(future_to_trash_cleanup):
            upload = future_to_trash_cleanup[future]

            try:
                success = future.result()
            except Exception as error:
                print_job_block(
                    "TRASH CLEANUP JOB",
                    0,
                    upload.remote_path,
                    f"Trash cleanup crashed: {error}",
                )
                trash_cleanup_failed = True
                continue

            if not success:
                trash_cleanup_failed = True

    if trash_cleanup_failed:
        print_error("One or more trash cleanup jobs failed")
        sys.exit(1)

    print_step("PreCleanup successful")

    sleep_after_step()

    # ----------------------------
    # Phase 4: Upload new files
    # ----------------------------

    print_step(
        f"Uploading new files using up to "
        f"{UPLOAD_THREADS} UPLOAD JOB(s)"
    )

    upload_failed = False

    with ThreadPoolExecutor(max_workers=UPLOAD_THREADS) as executor:
        future_to_upload = {
            executor.submit(upload_one_directory, index, upload): upload
            for index, upload in enumerate(UPLOAD_DIRECTORIES, start=1)
        }

        for future in as_completed(future_to_upload):
            upload = future_to_upload[future]

            try:
                success = future.result()
            except Exception as error:
                with OUTPUT_LOCK:
                    print()
                    print(OUTPUT_SEPARATOR)
                    print("UPLOAD JOB CRASHED")
                    print(f"Remote: {upload.remote_path}")
                    print(f"Error: {error}")
                    print(OUTPUT_SEPARATOR)
                    print(flush=True)

                upload_failed = True
                continue

            if not success:
                upload_failed = True

    if upload_failed:
        print_error("One or more uploads failed")
        sys.exit(1)

    print_step("Upload and cleanup successful")
    return 0


if __name__ == "__main__":
    sys.exit(main())
