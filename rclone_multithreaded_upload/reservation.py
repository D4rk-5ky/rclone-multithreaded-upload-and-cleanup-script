"""Filtered local sizing with a concurrent single-flight cache."""

from concurrent.futures import Future
import json
from pathlib import Path
import threading

from .commands import run_command
from .models import UploadDirectory
from .output import print_job_block
from .results import command_error_summary, record_stage_failure
from .utils import format_bytes


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

_LOCAL_SIZE_LOCK = threading.Lock()
_LOCAL_SIZE_FUTURES: dict[tuple[str, tuple[str, ...]], Future] = {}


def clear_local_size_cache() -> None:
    with _LOCAL_SIZE_LOCK:
        _LOCAL_SIZE_FUTURES.clear()


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
    """Extract source-selection filters from copy_options for `rclone size`."""
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


def local_size_cache_key(upload: UploadDirectory) -> tuple[str, tuple[str, ...]]:
    return str(Path(upload.local_path).resolve()), tuple(get_size_filter_options(upload))


def _calculate_filtered_local_upload_size(upload: UploadDirectory) -> tuple[int, int]:
    valid, error = validate_local_upload_path(upload)
    if not valid:
        raise RuntimeError(error or "Invalid local path")
    filter_options = get_size_filter_options(upload)
    command = ["rclone", "size", upload.local_path, "--json"] + filter_options
    result = run_command(command, capture_output=True)
    if result.returncode != 0:
        command_output = (result.stderr or result.stdout or "unknown rclone size error").strip()
        raise RuntimeError(
            f"Command: {' '.join(command)}\n"
            f"Return code: {result.returncode}\n"
            f"{command_error_summary(command_output)}"
        )
    try:
        payload = json.loads(result.stdout)
        size_bytes = int(payload["bytes"])
        file_count = int(payload["count"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise RuntimeError(
            f"Could not parse rclone size --json output: {error}\nOutput: {result.stdout}"
        ) from error
    if size_bytes < 0 or file_count < 0:
        raise RuntimeError("rclone size returned a negative byte or file count")
    return size_bytes, file_count


def get_filtered_local_upload_size(
    job_number: int,
    upload: UploadDirectory,
) -> tuple[int, int] | None:
    """Return cached filtered local bytes/count; identical sources are scanned once."""
    try:
        key = local_size_cache_key(upload)
    except ValueError as error:
        detail = f"Could not build rclone size filters: {error}"
        record_stage_failure(upload.remote_path, "reservation", detail)
        print_job_block("UPLOAD SIZE JOB", job_number, upload.remote_path, detail)
        return None

    with _LOCAL_SIZE_LOCK:
        future = _LOCAL_SIZE_FUTURES.get(key)
        owner = future is None
        if future is None:
            future = Future()
            _LOCAL_SIZE_FUTURES[key] = future

    if owner:
        try:
            future.set_result(_calculate_filtered_local_upload_size(upload))
        except Exception as error:
            future.set_exception(error)

    try:
        size_bytes, file_count = future.result()
    except Exception as error:
        detail = f"Failed sizing filtered local upload source: {error}"
        record_stage_failure(upload.remote_path, "reservation", detail)
        print_job_block("UPLOAD SIZE JOB", job_number, upload.remote_path, detail)
        return None

    filter_options = get_size_filter_options(upload)
    print_job_block(
        "UPLOAD SIZE JOB",
        job_number,
        upload.remote_path,
        (
            f"Filtered local upload size {'calculated' if owner else 'reused from cache'}.\n"
            f"Files selected by upload filters: {file_count}\n"
            f"Local candidate size           : {format_bytes(size_bytes)}\n"
            f"Size filters                   : {' '.join(filter_options) if filter_options else '(none)'}"
        ),
    )
    return size_bytes, file_count
