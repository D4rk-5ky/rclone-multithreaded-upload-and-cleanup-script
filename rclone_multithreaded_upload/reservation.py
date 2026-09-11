"""Filtered local upload snapshots, sizing, and quota selection."""

from concurrent.futures import Future
import json
from pathlib import Path
import threading

from .commands import run_command
from .models import LocalUploadFile, LocalUploadSnapshot, UploadDirectory
from .output import print_job_block
from .results import command_error_summary, record_stage_failure
from .utils import format_bytes, normalize_relative_path, parse_rclone_modtime


SOURCE_SELECTION_OPTIONS_WITH_VALUE = {
    "--min-age",
    "--max-age",
    "--min-size",
    "--max-size",
    "--include",
    "--include-from",
    "--exclude",
    "--exclude-from",
    "--exclude-if-present",
    "--filter",
    "--filter-from",
    "--files-from",
    "--files-from-raw",
    "--files-from0",
    "--hash-filter",
}
SOURCE_SELECTION_OPTIONS_BOOLEAN = {"--ignore-case"}

_LOCAL_SIZE_LOCK = threading.Lock()
_LOCAL_SIZE_FUTURES: dict[tuple[str, tuple[str, ...]], Future] = {}


def clear_local_size_cache() -> None:
    """Clear the per-run single-flight cache for filtered local snapshots."""
    with _LOCAL_SIZE_LOCK:
        _LOCAL_SIZE_FUTURES.clear()


def validate_local_upload_path(upload: UploadDirectory) -> tuple[bool, str | None]:
    """Validate the local upload root before snapshotting or uploading."""
    local_path = Path(upload.local_path)
    if not local_path.exists():
        return False, f"Local path does not exist: {upload.local_path}"
    if not local_path.is_dir():
        return False, f"Local path is not a directory: {upload.local_path}"
    return True, None


def get_size_filter_options(upload: UploadDirectory) -> list[str]:
    """Extract source-selection filters from copy_options for the local snapshot."""
    extracted: list[str] = []
    options = upload.copy_options
    index = 0
    while index < len(options):
        option = options[index]
        option_name = option.split("=", 1)[0]
        if option_name in SOURCE_SELECTION_OPTIONS_BOOLEAN:
            extracted.append(option)
            index += 1
            continue
        if option_name in SOURCE_SELECTION_OPTIONS_WITH_VALUE:
            if "=" in option:
                extracted.append(option)
                index += 1
                continue
            if index + 1 >= len(options):
                raise ValueError(
                    f"Upload option {option_name} requires a value for local source selection"
                )
            extracted.extend([option, options[index + 1]])
            index += 2
            continue
        index += 1
    return extracted


def get_non_filter_copy_options(upload: UploadDirectory) -> list[str]:
    """Remove source-selection filters once an exact generated file list is used."""
    retained: list[str] = []
    options = upload.copy_options
    index = 0
    while index < len(options):
        option = options[index]
        option_name = option.split("=", 1)[0]
        if option_name in SOURCE_SELECTION_OPTIONS_BOOLEAN:
            index += 1
            continue
        if option_name in SOURCE_SELECTION_OPTIONS_WITH_VALUE:
            if "=" in option:
                index += 1
            else:
                if index + 1 >= len(options):
                    raise ValueError(f"Upload option {option_name} requires a value")
                index += 2
            continue
        retained.append(option)
        index += 1
    return retained


def local_size_cache_key(upload: UploadDirectory) -> tuple[str, tuple[str, ...]]:
    return str(Path(upload.local_path).resolve()), tuple(get_size_filter_options(upload))


def _calculate_filtered_local_upload_snapshot(upload: UploadDirectory) -> LocalUploadSnapshot:
    """Return the exact local candidate files selected by rclone source filters."""
    valid, error = validate_local_upload_path(upload)
    if not valid:
        raise RuntimeError(error or "Invalid local path")

    filter_options = get_size_filter_options(upload)
    command = [
        "rclone",
        "lsjson",
        upload.local_path,
        "--recursive",
        "--files-only",
        "--no-mimetype",
    ] + filter_options
    result = run_command(command, capture_output=True)
    if result.returncode != 0:
        command_output = (result.stderr or result.stdout or "unknown rclone lsjson error").strip()
        raise RuntimeError(
            f"Command: {' '.join(command)}\n"
            f"Return code: {result.returncode}\n"
            f"{command_error_summary(command_output)}"
        )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"Could not parse local rclone lsjson output: {error}\nOutput: {result.stdout}"
        ) from error
    if not isinstance(payload, list):
        raise RuntimeError("Local rclone lsjson did not return a JSON array")

    files: list[LocalUploadFile] = []
    seen_paths: set[str] = set()
    total_bytes = 0
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(f"Local rclone lsjson entry {index} is not an object")
        if item.get("IsDir") is True:
            raise RuntimeError(
                f"Local rclone lsjson entry {index} is a directory despite --files-only"
            )
        path = item.get("Path")
        size = item.get("Size")
        modified = item.get("ModTime")
        if not isinstance(path, str) or not path:
            raise RuntimeError(f"Local rclone lsjson entry {index} has invalid Path")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise RuntimeError(f"Local rclone lsjson entry {index} has invalid Size")
        if not isinstance(modified, str) or not modified:
            raise RuntimeError(f"Local rclone lsjson entry {index} has invalid ModTime")
        parse_rclone_modtime(modified)
        normalized_path = normalize_relative_path(path)
        if normalized_path in seen_paths:
            raise RuntimeError(
                f"Local rclone lsjson returned duplicate path {normalized_path!r}"
            )
        seen_paths.add(normalized_path)
        files.append(LocalUploadFile(normalized_path, size, modified))
        total_bytes += size

    return LocalUploadSnapshot(tuple(files), total_bytes)


def get_filtered_local_upload_snapshot(
    job_number: int,
    upload: UploadDirectory,
) -> LocalUploadSnapshot | None:
    """Return one cached exact filtered source snapshot for identical source/filter sets."""
    try:
        key = local_size_cache_key(upload)
    except ValueError as error:
        detail = f"Could not build local source filters: {error}"
        record_stage_failure(upload.remote_path, "reservation", detail)
        print_job_block("UPLOAD SOURCE JOB", job_number, upload.remote_path, detail)
        return None

    with _LOCAL_SIZE_LOCK:
        future = _LOCAL_SIZE_FUTURES.get(key)
        owner = future is None
        if future is None:
            future = Future()
            _LOCAL_SIZE_FUTURES[key] = future

    if owner:
        try:
            future.set_result(_calculate_filtered_local_upload_snapshot(upload))
        except Exception as error:
            future.set_exception(error)

    try:
        snapshot = future.result()
    except Exception as error:
        detail = f"Failed snapshotting filtered local upload source: {error}"
        record_stage_failure(upload.remote_path, "reservation", detail)
        print_job_block("UPLOAD SOURCE JOB", job_number, upload.remote_path, detail)
        return None

    filter_options = get_size_filter_options(upload)
    print_job_block(
        "UPLOAD SOURCE JOB",
        job_number,
        upload.remote_path,
        (
            f"Filtered local upload snapshot {'calculated' if owner else 'reused from cache'}.\n"
            f"Files selected by upload filters: {snapshot.file_count}\n"
            f"Local candidate size           : {format_bytes(snapshot.total_bytes)}\n"
            f"Source filters                 : {' '.join(filter_options) if filter_options else '(none)'}"
        ),
    )
    return snapshot


def get_filtered_local_upload_size(
    job_number: int,
    upload: UploadDirectory,
) -> tuple[int, int] | None:
    """Compatibility helper returning bytes/count from the cached exact source snapshot."""
    snapshot = get_filtered_local_upload_snapshot(job_number, upload)
    if snapshot is None:
        return None
    return snapshot.total_bytes, snapshot.file_count


def newest_first_local_files(files: tuple[LocalUploadFile, ...]) -> list[LocalUploadFile]:
    """Sort local candidates newest first with path ordering as a deterministic tie-breaker."""
    return sorted(
        files,
        key=lambda file: (-parse_rclone_modtime(file.modified).timestamp(), file.path),
    )


def select_local_upload_files(
    snapshot: LocalUploadSnapshot,
    max_upload_bytes: int,
) -> tuple[tuple[LocalUploadFile, ...], int]:
    """Select a contiguous newest-first prefix and stop at the first non-fitting file."""
    if max_upload_bytes < 0:
        raise ValueError("max_upload_bytes cannot be negative")

    selected: list[LocalUploadFile] = []
    selected_bytes = 0
    for file in newest_first_local_files(snapshot.files):
        remaining_bytes = max_upload_bytes - selected_bytes
        if remaining_bytes <= 0 or file.size > remaining_bytes:
            break
        selected.append(file)
        selected_bytes += file.size
    return tuple(selected), selected_bytes
