"""Final per-remote verification from one recursive snapshot."""

from .models import CleanupTarget, RemoteSnapshot, UploadDirectory
from .output import print_job_block
from .remote_files import (
    files_below_relative_path,
    get_managed_snapshot_files,
    relative_cleanup_target_path,
)
from .results import record_stage_failure
from .utils import format_bytes, parse_size_to_bytes


def verify_upload_snapshot(
    job_number: int,
    upload: UploadDirectory,
    targets: list[CleanupTarget],
    snapshot: RemoteSnapshot,
) -> bool:
    """Verify every cleanup limit and max_total_size using the same live snapshot."""
    failures: list[str] = []

    for target in targets:
        if not target.delete_excess_files:
            continue
        if target.max_files is None and target.max_size is None:
            continue

        try:
            relative_path = relative_cleanup_target_path(upload, target)
            files = files_below_relative_path(snapshot, relative_path)
        except Exception as error:
            failures.append(f"{target.path}: verification filtering failed: {error}")
            continue

        file_count = len(files)
        total_size = sum(file.size for file in files)
        if target.max_files is not None and file_count > target.max_files:
            failures.append(
                f"{target.path}: file count {file_count} exceeds max_files {target.max_files}"
            )
        if target.max_size is not None:
            max_size_bytes = parse_size_to_bytes(target.max_size)
            if total_size > max_size_bytes:
                failures.append(
                    f"{target.path}: size {format_bytes(total_size)} exceeds max_size {target.max_size}"
                )

    if upload.delete_excess_files and upload.max_total_size is not None:
        managed_files = get_managed_snapshot_files(upload, snapshot)
        managed_size = sum(file.size for file in managed_files)
        max_total_size_bytes = parse_size_to_bytes(upload.max_total_size)
        if managed_size > max_total_size_bytes:
            failures.append(
                f"managed size {format_bytes(managed_size)} exceeds max_total_size "
                f"{format_bytes(max_total_size_bytes)}"
            )

    if failures:
        detail = "Final snapshot verification FAILED:\n  " + "\n  ".join(failures)
        record_stage_failure(upload.remote_path, "final_quota", detail)
        print_job_block("FINAL SNAPSHOT VERIFY", job_number, upload.remote_path, detail)
        return False

    print_job_block(
        "FINAL SNAPSHOT VERIFY",
        job_number,
        upload.remote_path,
        (
            "Final cleanup and quota verification passed using one recursive snapshot.\n"
            f"Remote files in snapshot: {len(snapshot.files_by_path)}"
        ),
    )
    return True
