"""Filtered local sizing and pre-upload quota reservation."""

import json
from pathlib import Path

from .commands import run_command
from .models import UploadDirectory
from .output import print_job_block
from .rclone_backend import get_delete_mode_options
from .remote_files import get_upload_remote_quota_entries
from .results import command_error_summary, record_stage_failure, record_stage_success
from .state import STATE
from .utils import (
    format_bytes,
    parse_size_to_bytes,
    remote_file_oldest_sort_key,
    remote_name_from_path,
)


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
SIZE_FILTER_OPTIONS_BOOLEAN = {"--ignore-case"}


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
    STATE.delete_list_dir.mkdir(parents=True, exist_ok=True)
    safe_name = remote_name_from_path(upload.remote_path)
    delete_list_path = STATE.delete_list_dir / f"to-delete-upload-reservation-{safe_name}"

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
        + STATE.reservation_safety_headroom_bytes
        - max_total_size_bytes,
    )

    if required_free_bytes == 0:
        delete_list_path.write_text("", encoding="utf-8")
        return delete_list_path, current_size, 0, 0

    candidates = sorted(
        files,
        key=remote_file_oldest_sort_key,
    )

    selected = []
    selected_free_bytes = 0

    for file in candidates:
        if selected_free_bytes >= required_free_bytes:
            break

        selected.append(file)
        selected_free_bytes += file.size

    delete_list_path.write_text(
        "\n".join(file.path for file in selected)
        + ("\n" if selected else ""),
        encoding="utf-8",
    )

    return (
        delete_list_path,
        current_size,
        required_free_bytes,
        selected_free_bytes,
    )

def reserve_one_upload_remote_space(job_number: int, upload: UploadDirectory) -> bool:
    """Run the complete repeated size-reservation loop for one remote."""
    print_job_block(
        "UPLOAD RESERVATION JOB",
        job_number,
        upload.remote_path,
        "Starting pre-upload size reservation",
    )

    if not upload.delete_excess_files:
        print_job_block(
            "UPLOAD RESERVATION JOB",
            job_number,
            upload.remote_path,
            "delete_excess_files=False, skipping max_total_size reservation",
        )
        record_stage_success(upload.remote_path, "reservation")
        return True

    if upload.max_total_size is None:
        print_job_block(
            "UPLOAD RESERVATION JOB",
            job_number,
            upload.remote_path,
            "max_total_size=None, skipping max_total_size reservation",
        )
        record_stage_success(upload.remote_path, "reservation")
        return True

    max_total_size_bytes = parse_size_to_bytes(upload.max_total_size)

    for reservation_pass in range(1, STATE.max_reservation_cleanup_passes + 1):
        local_size_result = get_filtered_local_upload_size(job_number, upload)
        if local_size_result is None:
            return False
        local_upload_bytes, local_file_count = local_size_result
        reserved_upload_bytes = transfer_cap_bytes(local_upload_bytes)

        if (
            reserved_upload_bytes + STATE.reservation_safety_headroom_bytes
            > max_total_size_bytes
        ):
            detail = (
                "Filtered local source cannot fit on an empty managed remote with the "
                "required reservation safety headroom. Upload was not started.\n"
                f"Filtered local files : {local_file_count}\n"
                f"Filtered local size  : {format_bytes(local_upload_bytes)}\n"
                f"Reserved upload cap  : {format_bytes(reserved_upload_bytes)}\n"
                f"Reservation headroom : {format_bytes(STATE.reservation_safety_headroom_bytes)}\n"
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
            detail = (
                f"Failed calculating size reservation on pass {reservation_pass}: {error}"
            )
            record_stage_failure(upload.remote_path, "reservation", detail)
            print_job_block("UPLOAD RESERVATION JOB", job_number, upload.remote_path, detail)
            return False

        projected_temporary_size = current_size + reserved_upload_bytes
        available_headroom = max_total_size_bytes - projected_temporary_size

        if required_free_bytes == 0:
            with STATE.reserved_upload_bytes_lock:
                STATE.reserved_upload_bytes[upload.remote_path] = local_upload_bytes
            record_stage_success(upload.remote_path, "reservation")
            print_job_block(
                "UPLOAD RESERVATION JOB",
                job_number,
                upload.remote_path,
                (
                    "Pre-upload size reservation verified successfully.\n"
                    f"Reservation pass        : {reservation_pass}\n"
                    f"Filtered local files    : {local_file_count}\n"
                    f"Managed remote size     : {format_bytes(current_size)}\n"
                    f"Filtered local size     : {format_bytes(local_upload_bytes)}\n"
                    f"Reserved upload cap     : {format_bytes(reserved_upload_bytes)}\n"
                    f"Projected temporary size: {format_bytes(projected_temporary_size)}\n"
                    f"Required safety headroom: {format_bytes(STATE.reservation_safety_headroom_bytes)}\n"
                    f"Available headroom      : {format_bytes(available_headroom)}\n"
                    f"Max total size          : {format_bytes(max_total_size_bytes)}"
                ),
            )
            return True

        if selected_free_bytes < required_free_bytes:
            detail = (
                "Reservation could not select enough complete managed files to free "
                "the calculated byte deficit. Upload was not started.\n"
                f"Required free bytes: {format_bytes(required_free_bytes)}\n"
                f"Selected free bytes: {format_bytes(selected_free_bytes)}"
            )
            record_stage_failure(upload.remote_path, "reservation", detail)
            print_job_block("UPLOAD RESERVATION JOB", job_number, upload.remote_path, detail)
            return False

        command = [
            "rclone",
            "delete",
            "--files-from",
            str(delete_list_path),
            upload.remote_path,
        ] + get_delete_mode_options(upload)
        result = run_command(command, capture_output=True)
        output = (result.stdout or "") + (result.stderr or "")

        if output:
            print_job_block(
                "UPLOAD RESERVATION JOB",
                job_number,
                upload.remote_path,
                output,
            )

        if result.returncode != 0:
            detail = (
                f"Command: {' '.join(command)}\n"
                f"Return code: {result.returncode}\n"
                f"{command_error_summary(output)}"
            )
            record_stage_failure(upload.remote_path, "reservation", detail)
            print_job_block(
                "UPLOAD RESERVATION JOB",
                job_number,
                upload.remote_path,
                f"Reservation delete failed.\n{detail}",
            )
            return False

        print_job_block(
            "UPLOAD RESERVATION JOB",
            job_number,
            upload.remote_path,
            (
                f"Reservation cleanup pass {reservation_pass} finished.\n"
                f"Calculated deficit : {format_bytes(required_free_bytes)}\n"
                f"Selected full files: {format_bytes(selected_free_bytes)}\n"
                "Re-reading the filtered local source and managed remote size."
            ),
        )

    local_size_result = get_filtered_local_upload_size(job_number, upload)
    if local_size_result is None:
        return False
    local_upload_bytes, local_file_count = local_size_result

    try:
        current_size_after = sum(
            file.size for file in get_upload_remote_quota_entries(upload)
        )
    except Exception as error:
        detail = f"Failed re-reading managed remote size after reservation retries: {error}"
        record_stage_failure(upload.remote_path, "reservation", detail)
        print_job_block("UPLOAD RESERVATION JOB", job_number, upload.remote_path, detail)
        return False

    reserved_upload_bytes_after = transfer_cap_bytes(local_upload_bytes)
    projected_temporary_size = current_size_after + reserved_upload_bytes_after
    required_limit = max_total_size_bytes - STATE.reservation_safety_headroom_bytes
    detail = (
        "Size reservation did not stabilize within the configured cleanup-pass limit; "
        "upload was not started. The filtered local source may be growing faster than "
        "reservation can make room.\n"
        f"Cleanup pass limit        : {STATE.max_reservation_cleanup_passes}\n"
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
