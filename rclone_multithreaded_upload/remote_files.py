"""Remote listing, managed-file filtering, and delete-list selection."""

import json
from pathlib import Path

from .commands import is_directory_not_found, run_command
from .models import CleanupTarget, RemoteFile, RemoteQuotaFile, UploadDirectory
from .output import print_job_block, print_step
from .state import STATE
from .utils import (
    format_bytes,
    normalize_relative_path,
    parse_rclone_modtime,
    parse_size_to_bytes,
    remote_file_oldest_sort_key,
    remote_name_from_path,
)


def get_remote_file_entries(remote_path: str) -> list[RemoteFile]:
    """Read one recursive rclone lsjson array and validate Path/Size/ModTime."""
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
            print_job_block(
                "REMOTE LISTING",
                0,
                remote_path,
                "Remote folder does not exist yet, treating it as empty",
            )
            return []
        detail = (result.stderr or result.stdout or "unknown rclone lsjson error").strip()
        raise RuntimeError(
            f"rclone lsjson failed for {remote_path}. "
            f"Return code: {result.returncode}. {detail}"
        )

    try:
        raw_entries = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"rclone lsjson returned invalid JSON for {remote_path}: {error}"
        ) from error

    if not isinstance(raw_entries, list):
        raise RuntimeError(f"rclone lsjson did not return a JSON array for {remote_path}")

    files: list[RemoteFile] = []
    for index, item in enumerate(raw_entries, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(
                f"rclone lsjson entry {index} for {remote_path} is not an object"
            )
        if item.get("IsDir") is True:
            raise RuntimeError(
                f"rclone lsjson entry {index} for {remote_path} is a directory despite --files-only"
            )

        path = item.get("Path")
        size = item.get("Size")
        modified = item.get("ModTime")
        if not isinstance(path, str) or not path:
            raise RuntimeError(
                f"rclone lsjson entry {index} for {remote_path} has invalid Path"
            )
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise RuntimeError(
                f"rclone lsjson entry {index} for {remote_path} has invalid Size"
            )
        if not isinstance(modified, str) or not modified:
            raise RuntimeError(
                f"rclone lsjson entry {index} for {remote_path} has invalid ModTime"
            )

        parse_rclone_modtime(modified)
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
    STATE.delete_list_dir.mkdir(parents=True, exist_ok=True)

    safe_name = remote_name_from_path(target.path)
    delete_list_path = STATE.delete_list_dir / f"to-delete-{safe_name}"

    files = get_remote_file_entries(target.path)

    if not files:
        print_step(f"No files found in {target.path}")
        delete_list_path.write_text("", encoding="utf-8")
        return delete_list_path

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

            if (
                relative_path == rule_path
                or relative_path.startswith(f"{rule_path}/")
            ):
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

    This enforces upload.max_total_size across files managed by this upload
    destination's cleanup_rules below upload.remote_path.
    """
    STATE.delete_list_dir.mkdir(parents=True, exist_ok=True)

    safe_name = remote_name_from_path(upload.remote_path)
    delete_list_path = STATE.delete_list_dir / f"to-delete-remote-quota-{safe_name}"

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
