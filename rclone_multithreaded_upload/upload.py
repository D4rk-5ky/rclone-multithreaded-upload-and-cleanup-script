"""Streamed rclone upload execution."""

import subprocess

from .models import UploadDirectory
from .output import OUTPUT_LOCK, OUTPUT_SEPARATOR, print_job_block
from .rclone_backend import get_delete_mode_options
from .reservation import transfer_cap_bytes, validate_local_upload_path
from .results import command_error_summary, record_stage_failure, record_stage_success
from .state import STATE
from .utils import validate_upload_command


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

    upload_delete_options = (
        get_delete_mode_options(upload) if upload_command == "sync" else []
    )
    transfer_cap_options: list[str] = []
    buffer_options = get_upload_buffer_options(upload)

    with STATE.reserved_upload_bytes_lock:
        reserved_upload_bytes = STATE.reserved_upload_bytes.get(upload.remote_path)

    if (
        upload.max_total_size is not None
        and reserved_upload_bytes is not None
        and reserved_upload_bytes > 0
    ):
        transfer_cap_options = [
            "--max-transfer",
            f"{transfer_cap_bytes(reserved_upload_bytes)}B",
            "--cutoff-mode",
            "CAUTIOUS",
        ]

    command = [
        "rclone",
        upload_command,
        upload.local_path,
        upload.remote_path,
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
