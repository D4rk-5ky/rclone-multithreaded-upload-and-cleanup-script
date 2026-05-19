#!/usr/bin/env python3

import atexit
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
import threading


# ----------------------------
# Configuration dataclasses
# ----------------------------

@dataclass
class RcloneDirectory:
    """
    One camera folder cleanup rule.

    path:
      Folder path relative to every upload remote root.
      Example: Home/FrontDoorCamera

    max_files:
      Keep only this many newest files when delete_excess_files=True.

    max_size:
      Keep this folder under this total size when delete_excess_files=True.
      Examples: "500M", "50G", "1T"

    delete_old_files:
      Run rclone delete --min-age DELETE_MIN_AGE.

    delete_excess_files:
      Delete oldest files until max_files/max_size limits are satisfied.

    delete_to_trash:
      False = delete directly/permanently where supported.
      True  = use backend trash/default behavior where supported.
    """
    path: str
    max_files: int | None = None
    max_size: str | None = None
    delete_old_files: bool = True
    delete_excess_files: bool = True
    delete_to_trash: bool = False


@dataclass
class UploadDirectory:
    """
    One upload destination.

    local_path:
      Local source folder.

    remote_path:
      Rclone destination root.

    copy_options:
      Extra rclone options for this upload job.

    empty_trash:
      Run rclone cleanup on this remote_path before upload.

    upload_command:
      rclone command to use for upload: copy, sync, or move.
    """
    local_path: str
    remote_path: str
    copy_options: list[str]
    empty_trash: bool = True
    upload_command: str = "copy"


@dataclass
class RemoteFile:
    path: str
    size: int
    modified: str


# ----------------------------
# Example / GitHub-safe user configuration
# ----------------------------
# Replace these example paths with your own rclone remotes and local folders.
# These names are intentionally generic so the script can be shared publicly.

RCLONE_DIRECTORIES = [
    # Example 1:
    # Keep only the 80 newest files.
    # Delete old files directly/permanently where supported.
    RcloneDirectory(
        path="Home/FrontDoorCamera",
        max_files=80,
        delete_old_files=True,
        delete_excess_files=True,
        delete_to_trash=False,
    ),

    # Example 2:
    # Keep folder under 50 GiB.
    # Send deletes to trash/backend default where supported.
    RcloneDirectory(
        path="Home/GarageCamera",
        max_size="50G",
        delete_old_files=True,
        delete_excess_files=True,
        delete_to_trash=True,
    ),

    # Example 3:
    # Do not delete by age.
    # Only keep the newest 120 files.
    RcloneDirectory(
        path="Home/BackGardenCamera",
        max_files=120,
        delete_old_files=False,
        delete_excess_files=True,
        delete_to_trash=False,
    ),

    # Example 4:
    # Delete files older than DELETE_MIN_AGE.
    # Do not do max_files/max_size cleanup for this folder.
    RcloneDirectory(
        path="Home/WorkshopCamera",
        max_size="30G",
        delete_old_files=True,
        delete_excess_files=False,
        delete_to_trash=True,
    ),
]

UPLOAD_DIRECTORIES = [
    # Example 1: normal backup copy to encrypted cloud remote.
    # copy = upload new/changed files, do not delete extra remote files.
    UploadDirectory(
        local_path="/path/to/local/CCTV",
        remote_path="Example-GoogleDrive-Encrypted:/CCTV",
        upload_command="copy",
        empty_trash=True,
        copy_options=[
            "--max-age", "3h",
            "--stats", "10s",
            "--stats-one-line",
            "--transfers", "4",
            "--exclude", "/Home/OldCameraNotUploaded/**",
        ],
    ),

    # Example 2: mirror/sync style destination.
    # WARNING: sync can delete files on the remote so it matches the local source.
    UploadDirectory(
        local_path="/path/to/local/CCTV",
        remote_path="Example-Mega-Encrypted:/CCTV",
        upload_command="sync",
        empty_trash=False,
        copy_options=[
            "--max-age", "3h",
            "--stats", "10s",
            "--stats-one-line",
            "--transfers", "2",
            "--exclude", "/Home/OldCameraNotUploaded/**",
        ],
    ),

    # Example 3: move example.
    # WARNING: move uploads and then deletes the local source files after success.
    # This example includes --dry-run for safety. Remove --dry-run only when you are sure.
    UploadDirectory(
        local_path="/path/to/local/CCTV",
        remote_path="Example-OneDrive-Encrypted:/CCTV",
        upload_command="move",
        empty_trash=False,
        copy_options=[
            "--dry-run",
            "--max-age", "3h",
            "--stats", "10s",
            "--stats-one-line",
            "--transfers", "2",
            "--exclude", "/Home/OldCameraNotUploaded/**",
        ],
    ),
]

# Age used by delete_old_files=True.
DELETE_MIN_AGE = "31d"

# Allowed upload commands. Kept intentionally small for safety.
ALLOWED_UPLOAD_COMMANDS = {
    "copy",
    "sync",
    "move",
}

# Thread limits for the three phases.
UPLOAD_THREADS = 4
CLEANUP_THREADS = 4
TRASH_CLEANUP_THREADS = 4

# Lock/output/temp settings.
OUTPUT_LOCK = threading.Lock()
OUTPUT_SEPARATOR = "=" * 80
LOCK_FILE = Path("/tmp/rclone-upload-cleanup-example.lock")
DELETE_LIST_DIR = Path("/tmp/rclone-delete-lists")
SLEEP_AFTER_STEP = 5

lock_created = False


# ----------------------------
# Printing helpers
# ----------------------------

def print_step(message: str):
    with OUTPUT_LOCK:
        print(f"\n  {message}\n", flush=True)


def print_error(message: str):
    with OUTPUT_LOCK:
        print(f"\n      ERROR: {message}\n", flush=True)


def print_job_block(job_type: str, job_number: int, target: str, message: str):
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


# ----------------------------
# Generic helpers
# ----------------------------

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


def bytes_to_gib(size_bytes: int) -> float:
    return size_bytes / 1024 ** 3


def run_command(command: list[str], capture_output: bool = False) -> subprocess.CompletedProcess:
    with OUTPUT_LOCK:
        print(f"Running command: {' '.join(command)}", flush=True)

    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
    )


def acquire_lock():
    """
    Create lock file atomically to avoid two script instances running together.
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


def remote_name_from_path(remote_path: str) -> str:
    """
    Make a safe filename from the full rclone remote path.
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
      Home/FrontDoorCamera

    Becomes:
      Example-GoogleDrive-Encrypted:/CCTV/Home/FrontDoorCamera
    """
    return f"{remote_root.rstrip('/')}/{relative_path.lstrip('/')}"


def validate_upload_command(command: str) -> str:
    command = command.strip().lower()

    if command not in ALLOWED_UPLOAD_COMMANDS:
        raise ValueError(
            f"Invalid upload_command '{command}'. "
            f"Allowed commands: {', '.join(sorted(ALLOWED_UPLOAD_COMMANDS))}"
        )

    return command


def get_delete_mode_options(directory: RcloneDirectory) -> list[str]:
    """
    Return rclone delete options for direct-delete or trash mode.

    delete_to_trash=False:
      Adds --drive-use-trash=false.
      This means direct/permanent delete where supported.

    delete_to_trash=True:
      Does not add --drive-use-trash=false.
      This lets rclone/backend use trash/default behavior where supported.
    """
    if directory.delete_to_trash:
        return []

    return ["--drive-use-trash=false"]


def get_delete_mode_text(directory: RcloneDirectory) -> str:
    if directory.delete_to_trash:
        return "trash/backend default"

    return "direct/permanent where supported"


def is_directory_not_found(result: subprocess.CompletedProcess) -> bool:
    output = ""

    if result.stdout:
        output += result.stdout

    if result.stderr:
        output += result.stderr

    return "directory not found" in output.lower()


def build_cleanup_directories() -> list[RcloneDirectory]:
    """
    Build full cleanup targets from every upload remote and every camera rule.
    """
    cleanup_directories = []

    for upload in UPLOAD_DIRECTORIES:
        for directory in RCLONE_DIRECTORIES:
            cleanup_directories.append(
                RcloneDirectory(
                    path=join_rclone_remote_path(upload.remote_path, directory.path),
                    max_files=directory.max_files,
                    max_size=directory.max_size,
                    delete_old_files=directory.delete_old_files,
                    delete_excess_files=directory.delete_excess_files,
                    delete_to_trash=directory.delete_to_trash,
                )
            )

    return cleanup_directories


# ----------------------------
# Startup summary
# ----------------------------

def print_startup_summary(cleanup_directories: list[RcloneDirectory]):
    """
    Print a clear summary before anything is deleted or uploaded.
    """
    with OUTPUT_LOCK:
        print()
        print(OUTPUT_SEPARATOR)
        print("SCRIPT STARTUP SUMMARY")
        print(OUTPUT_SEPARATOR)

        print()
        print("Phase order:")
        print("  1. Cleanup camera folders")
        print("  2. Empty remote trash where enabled")
        print("  3. Upload local files")

        print()
        print("Thread limits:")
        print(f"  Cleanup jobs       : {CLEANUP_THREADS}")
        print(f"  Trash cleanup jobs : {TRASH_CLEANUP_THREADS}")
        print(f"  Upload jobs        : {UPLOAD_THREADS}")

        print()
        print("Global cleanup settings:")
        print(f"  Delete min age     : {DELETE_MIN_AGE}")
        print("  Missing folders    : skipped")

        print()
        print("Upload destinations:")
        for index, upload in enumerate(UPLOAD_DIRECTORIES, start=1):
            try:
                upload_command = validate_upload_command(upload.upload_command)
            except ValueError:
                upload_command = f"INVALID: {upload.upload_command}"

            print(f"  Upload destination {index}:")
            print(f"    Local path       : {upload.local_path}")
            print(f"    Remote path      : {upload.remote_path}")
            print(f"    Upload command   : rclone {upload_command}")
            print(f"    Empty trash      : {upload.empty_trash}")
            print(f"    Upload options   : {' '.join(upload.copy_options)}")

        print()
        print("Configured camera cleanup rules:")
        for index, directory in enumerate(RCLONE_DIRECTORIES, start=1):
            print(f"  Camera rule {index}:")
            print(f"    Relative path        : {directory.path}")
            print(f"    Delete old files     : {directory.delete_old_files}")
            print(f"    Delete excess files  : {directory.delete_excess_files}")
            print(f"    Max files            : {directory.max_files}")
            print(f"    Max size             : {directory.max_size}")
            print(f"    Delete mode          : {get_delete_mode_text(directory)}")

        print()
        print("Full cleanup targets that will be processed:")
        for index, directory in enumerate(cleanup_directories, start=1):
            print(f"  Cleanup target {index}:")
            print(f"    Path                : {directory.path}")
            print(f"    Delete old files    : {directory.delete_old_files}")
            print(f"    Delete excess files : {directory.delete_excess_files}")
            print(f"    Max files           : {directory.max_files}")
            print(f"    Max size            : {directory.max_size}")
            print(f"    Delete mode         : {get_delete_mode_text(directory)}")
            print(f"    Delete min age      : {DELETE_MIN_AGE}")

        print()
        print("Totals:")
        print(f"  Upload destinations : {len(UPLOAD_DIRECTORIES)}")
        print(f"  Camera rules        : {len(RCLONE_DIRECTORIES)}")
        print(f"  Cleanup targets     : {len(cleanup_directories)}")

        print(OUTPUT_SEPARATOR)
        print(flush=True)


# ----------------------------
# Remote file listing / delete-list building
# ----------------------------

def get_remote_file_entries(remote_path: str) -> list[RemoteFile]:
    """
    Get remote files with modified time, size, and path.

    Uses:
      rclone lsf --files-only --format tsp --separator TAB

    Format order:
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


def make_delete_list(directory: RcloneDirectory) -> Path:
    """
    Create a --files-from list for files that should be deleted.

    Supports:
      max_files = keep only this many newest files
      max_size  = keep folder under this total size
      both      = delete oldest files until both limits are satisfied
    """
    DELETE_LIST_DIR.mkdir(parents=True, exist_ok=True)

    safe_name = remote_name_from_path(directory.path)
    delete_list_path = DELETE_LIST_DIR / f"to-delete-{safe_name}"

    files = get_remote_file_entries(directory.path)

    if not files:
        print_step(f"No files found in {directory.path}")
        delete_list_path.write_text("", encoding="utf-8")
        return delete_list_path

    files_oldest_first = sorted(
        files,
        key=lambda item: (item.modified, item.path),
    )

    total_size = sum(file.size for file in files_oldest_first)
    total_files = len(files_oldest_first)

    max_size_bytes = None

    if directory.max_size is not None:
        max_size_bytes = parse_size_to_bytes(directory.max_size)

    files_to_delete: list[RemoteFile] = []

    current_size = total_size
    current_files = total_files

    for file in files_oldest_first:
        too_many_files = (
            directory.max_files is not None
            and current_files > directory.max_files
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

    print_step(
        f"Made delete list for {directory.path}: "
        f"{len(files_to_delete)} file(s) marked for deletion. "
        f"Files before: {total_files}, after: {current_files}. "
        f"Size before: {bytes_to_gib(total_size):.2f} GiB, "
        f"after: {bytes_to_gib(current_size):.2f} GiB, "
        f"delete: {bytes_to_gib(total_size - current_size):.2f} GiB."
    )

    return delete_list_path


# ----------------------------
# Cleanup jobs
# ----------------------------

def cleanup_one_directory(job_number: int, directory: RcloneDirectory) -> bool:
    """
    Cleanup one remote camera directory.

    Optional steps:
      1. rclone delete --min-age DELETE_MIN_AGE
      2. max_files / max_size delete-list cleanup
    """
    print_job_block(
        "CLEANUP JOB",
        job_number,
        directory.path,
        "Starting cleanup job",
    )

    if not directory.delete_old_files and not directory.delete_excess_files:
        print_job_block(
            "CLEANUP JOB",
            job_number,
            directory.path,
            "Both delete_old_files and delete_excess_files are disabled, skipping",
        )
        return True

    if directory.delete_old_files:
        result = run_command(
            [
                "rclone",
                "delete",
                directory.path,
                "--min-age", DELETE_MIN_AGE,
            ] + get_delete_mode_options(directory),
            capture_output=True,
        )

        output = ""

        if result.stdout:
            output += result.stdout

        if result.stderr:
            output += result.stderr

        if output:
            print_job_block("CLEANUP JOB", job_number, directory.path, output)

        if result.returncode != 0:
            if is_directory_not_found(result):
                print_job_block(
                    "CLEANUP JOB",
                    job_number,
                    directory.path,
                    "Remote folder does not exist yet, skipping cleanup for this folder",
                )
                return True

            print_job_block(
                "CLEANUP JOB",
                job_number,
                directory.path,
                f"Failed deleting old files. Return code: {result.returncode}",
            )
            return False
    else:
        print_job_block(
            "CLEANUP JOB",
            job_number,
            directory.path,
            "Skipping delete_old_files for this folder",
        )

    if not directory.delete_excess_files:
        print_job_block(
            "CLEANUP JOB",
            job_number,
            directory.path,
            "Skipping delete_excess_files for this folder",
        )
        return True

    if directory.max_files is None and directory.max_size is None:
        print_job_block(
            "CLEANUP JOB",
            job_number,
            directory.path,
            "delete_excess_files=True but no max_files or max_size is set, skipping",
        )
        return True

    try:
        delete_list_path = make_delete_list(directory)
    except Exception as error:
        print_job_block(
            "CLEANUP JOB",
            job_number,
            directory.path,
            f"Failed making delete list: {error}",
        )
        return False

    if delete_list_path.stat().st_size == 0:
        print_job_block(
            "CLEANUP JOB",
            job_number,
            directory.path,
            "No excessive files to delete",
        )
        return True

    result = run_command(
        [
            "rclone",
            "delete",
            "--files-from", str(delete_list_path),
            directory.path,
        ] + get_delete_mode_options(directory),
        capture_output=True,
    )

    output = ""

    if result.stdout:
        output += result.stdout

    if result.stderr:
        output += result.stderr

    if output:
        print_job_block("CLEANUP JOB", job_number, directory.path, output)

    if result.returncode != 0:
        print_job_block(
            "CLEANUP JOB",
            job_number,
            directory.path,
            f"Failed deleting excessive files. Return code: {result.returncode}",
        )
        return False

    print_job_block(
        "CLEANUP JOB",
        job_number,
        directory.path,
        "Cleanup job finished successfully",
    )

    return True


def cleanup_one_trash_remote(job_number: int, upload: UploadDirectory) -> bool:
    """
    Optionally run rclone cleanup once for one upload remote.
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


# ----------------------------
# Upload jobs
# ----------------------------

def print_thread_output(thread_number: int, remote_path: str, line: str):
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
    local_path = Path(upload.local_path)

    if not local_path.exists():
        print_job_block(
            "UPLOAD JOB",
            job_number,
            upload.remote_path,
            f"FAILED BEFORE STARTING\nLocal path does not exist: {upload.local_path}",
        )
        return False

    if not local_path.is_dir():
        print_job_block(
            "UPLOAD JOB",
            job_number,
            upload.remote_path,
            f"FAILED BEFORE STARTING\nLocal path is not a directory: {upload.local_path}",
        )
        return False

    try:
        upload_command = validate_upload_command(upload.upload_command)
    except ValueError as error:
        print_job_block(
            "UPLOAD JOB",
            job_number,
            upload.remote_path,
            f"FAILED BEFORE STARTING\nError: {error}",
        )
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
        print_job_block(
            "UPLOAD JOB",
            job_number,
            upload.remote_path,
            f"FAILED\nCommand: rclone {upload_command}\nReturn code: {return_code}",
        )
        return False

    print_job_block(
        "UPLOAD JOB",
        job_number,
        upload.remote_path,
        f"FINISHED SUCCESSFULLY\nCommand: rclone {upload_command}",
    )

    return True


# ----------------------------
# Main script
# ----------------------------

def main():
    atexit.register(release_lock)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    acquire_lock()

    cleanup_directories = build_cleanup_directories()

    print_startup_summary(cleanup_directories)

    print_step("Lockfile doesn't exist, script is starting")

    # ----------------------------
    # Phase 1: cleanup remote camera folders
    # ----------------------------

    print_step(
        f"Cleaning remote camera folders using up to "
        f"{CLEANUP_THREADS} CLEANUP JOB(s)"
    )

    cleanup_failed = False

    with ThreadPoolExecutor(max_workers=CLEANUP_THREADS) as executor:
        future_to_cleanup = {
            executor.submit(cleanup_one_directory, index, directory): directory
            for index, directory in enumerate(cleanup_directories, start=1)
        }

        for future in as_completed(future_to_cleanup):
            directory = future_to_cleanup[future]

            try:
                success = future.result()
            except Exception as error:
                print_job_block(
                    "CLEANUP JOB",
                    0,
                    directory.path,
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
    # Phase 2: empty rclone trash where enabled
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
    # Phase 3: upload new files
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
                print_job_block(
                    "UPLOAD JOB",
                    0,
                    upload.remote_path,
                    f"Upload job crashed: {error}",
                )
                upload_failed = True
                continue

            if not success:
                upload_failed = True

    if upload_failed:
        print_error("One or more uploads failed")
        sys.exit(1)

    print_step("Upload and cleanup successful")
    sys.exit(0)


if __name__ == "__main__":
    main()
