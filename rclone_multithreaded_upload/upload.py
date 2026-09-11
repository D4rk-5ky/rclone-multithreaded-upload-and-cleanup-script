"""Streamed rclone upload execution."""

import subprocess
from pathlib import Path

from .models import UploadDirectory
from .output import OUTPUT_LOCK, OUTPUT_SEPARATOR, print_job_block
from .rclone_backend import get_delete_mode_options
from .reservation import get_non_filter_copy_options, validate_local_upload_path
from .results import (
    command_error_summary,
    record_stage_failure,
    record_stage_success,
    record_upload_trash_mode_attempted,
)
from .state import STATE
from .utils import remote_name_from_path, validate_upload_command


def print_thread_output(thread_number: int, remote_path: str, line: str):
    """Print one line of output from one upload job."""
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
    """Return optional per-remote rclone --buffer-size arguments."""
    if upload.buffer_size is None:
        return []
    return ["--buffer-size", upload.buffer_size]



def write_planned_upload_file_list(
    upload: UploadDirectory,
    planned_files: tuple[str, ...],
) -> Path:
    """Write the exact quota-managed source set as a NUL-separated rclone list."""
    STATE.delete_list_dir.mkdir(parents=True, exist_ok=True)
    list_path = STATE.delete_list_dir / f"to-upload-{remote_name_from_path(upload.remote_path)}.files0"
    with list_path.open("wb") as handle:
        for path in planned_files:
            if "\x00" in path:
                raise ValueError(f"Upload path contains a NUL byte: {path!r}")
            handle.write(path.encode("utf-8"))
            handle.write(b"\x00")
    return list_path

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

    with STATE.reserved_upload_bytes_lock:
        planned_files = STATE.planned_upload_files.get(upload.remote_path)

    if planned_files is not None and not planned_files:
        record_stage_success(upload.remote_path, "upload")
        print_job_block(
            "UPLOAD JOB",
            job_number,
            upload.remote_path,
            (
                "Upload finished successfully: no complete files fit before the "
                "newest-first quota cutoff; no rclone upload command was started"
            ),
        )
        return True

    upload_delete_options = (
        get_delete_mode_options(upload) if upload_command == "sync" else []
    )
    buffer_options = get_upload_buffer_options(upload)

    file_list_options: list[str] = []
    copy_options = list(upload.copy_options)
    if planned_files is not None:
        try:
            upload_list_path = write_planned_upload_file_list(upload, planned_files)
            copy_options = get_non_filter_copy_options(upload)
            file_list_options = ["--files-from0", str(upload_list_path)]
        except (OSError, UnicodeError, ValueError) as error:
            detail = f"Failed preparing exact upload file list: {error}"
            record_stage_failure(upload.remote_path, "upload", detail)
            print_job_block("UPLOAD JOB", job_number, upload.remote_path, detail)
            return False

    command = [
        "rclone",
        upload_command,
        upload.local_path,
        upload.remote_path,
    ] + upload_delete_options + copy_options + buffer_options + file_list_options

    if upload_command == "sync" and upload.delete_to_trash:
        record_upload_trash_mode_attempted(upload.remote_path)

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
        print_job_block(
            "UPLOAD JOB",
            job_number,
            upload.remote_path,
            f"Upload failed.\n{detail}",
        )
        return False

    record_stage_success(upload.remote_path, "upload")
    print_job_block(
        "UPLOAD JOB",
        job_number,
        upload.remote_path,
        f"Upload finished successfully with rclone {upload_command}",
    )
    return True
