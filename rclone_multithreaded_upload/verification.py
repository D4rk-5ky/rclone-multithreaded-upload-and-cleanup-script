"""Final cleanup-rule and remote-quota verification."""

from .models import CleanupTarget, UploadDirectory
from .output import print_job_block
from .remote_files import get_remote_file_entries, get_upload_remote_quota_entries
from .results import record_stage_failure
from .utils import format_bytes, parse_size_to_bytes


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
            failures.append(
                f"size {format_bytes(total_size)} exceeds max_size {target.max_size}"
            )

    if failures:
        detail = "Final cleanup target verification FAILED:\n  " + "\n  ".join(failures)
        record_stage_failure(target.owner_remote_path, "final_quota", detail)
        print_job_block("VERIFY CLEANUP TARGET", job_number, target.path, detail)
        return False

    print_job_block(
        "VERIFY CLEANUP TARGET",
        job_number,
        target.path,
        (
            "Final cleanup target verification passed.\n"
            f"Files: {file_count}\n"
            f"Size : {format_bytes(total_size)}"
        ),
    )
    return True


def verify_one_upload_remote_quota(
    job_number: int,
    upload: UploadDirectory,
) -> bool:
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
        "VERIFY REMOTE QUOTA",
        job_number,
        upload.remote_path,
        (
            "Final remote quota verification passed.\n"
            f"Managed size  : {format_bytes(total_size)}\n"
            f"Max total size: {format_bytes(max_total_size_bytes)}"
        ),
    )
    return True
