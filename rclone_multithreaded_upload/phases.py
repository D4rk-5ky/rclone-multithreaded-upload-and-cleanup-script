"""Per-remote snapshot pipelines and concurrent phase runners."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from .cleanup import cleanup_one_trash_remote, execute_delete_plan
from .models import CleanupTarget, UploadDirectory
from .output import print_job_block, print_step
from .planning import (
    build_post_upload_plan,
    build_pre_upload_plan,
    cleanup_targets_for_upload,
)
from .remote_files import fetch_remote_snapshot
from .reservation import clear_local_size_cache, get_filtered_local_upload_size
from .results import (
    finalize_stage_for_all,
    get_stage_result,
    record_stage_failure,
    record_stage_skipped,
    record_stage_success,
)
from .state import STATE
from .upload import upload_one_directory
from .utils import format_bytes
from .verification import verify_upload_snapshot


def print_snapshot_summary(
    job_number: int,
    snapshot_name: str,
    upload: UploadDirectory,
    snapshot,
) -> None:
    total_size = sum(file.size for file in snapshot.files_by_path.values())
    print_job_block(
        "REMOTE SNAPSHOT",
        job_number,
        upload.remote_path,
        (
            f"{snapshot_name} recursive snapshot loaded.\n"
            f"Files: {len(snapshot.files_by_path)}\n"
            f"Size : {format_bytes(total_size)}"
        ),
    )


def reserve_and_upload_one_remote(
    job_number: int,
    upload: UploadDirectory,
    cleanup_directories: list[CleanupTarget],
) -> bool:
    """Pre-clean, reserve, and upload one remote using one pre-upload snapshot."""
    print_job_block(
        "REMOTE PIPELINE",
        job_number,
        upload.remote_path,
        "Starting independent clean/reservation/upload pipeline",
    )
    targets = cleanup_targets_for_upload(cleanup_directories, upload)

    try:
        pre_snapshot = fetch_remote_snapshot(upload.remote_path)
        print_snapshot_summary(job_number, "PRE-UPLOAD", upload, pre_snapshot)
    except Exception as error:
        detail = f"Pre-upload remote snapshot failed: {error}"
        record_stage_failure(upload.remote_path, "reservation", detail)
        record_stage_skipped(upload.remote_path, "upload")
        print_job_block("REMOTE PIPELINE", job_number, upload.remote_path, detail)
        return False

    local_upload_bytes = 0
    local_file_count = 0
    quota_reservation_enabled = (
        upload.delete_excess_files and upload.max_total_size is not None
    )
    if quota_reservation_enabled:
        local_size_result = get_filtered_local_upload_size(job_number, upload)
        if local_size_result is None:
            record_stage_skipped(upload.remote_path, "upload")
            return False
        local_upload_bytes, local_file_count = local_size_result

    try:
        plan, _working_snapshot, reservation = build_pre_upload_plan(
            upload,
            targets,
            pre_snapshot,
            local_upload_bytes,
        )
    except Exception as error:
        detail = f"Pre-upload cleanup/reservation planning failed: {error}"
        record_stage_failure(upload.remote_path, "reservation", detail)
        record_stage_skipped(upload.remote_path, "upload")
        print_job_block("REMOTE PIPELINE", job_number, upload.remote_path, detail)
        return False

    if not execute_delete_plan(
        job_number,
        upload,
        plan,
        "reservation",
    ):
        record_stage_skipped(upload.remote_path, "upload")
        return False

    if not cleanup_one_trash_remote(
        job_number,
        upload,
        "POST-RESERVATION TRASH CLEANUP",
    ):
        if get_stage_result(upload.remote_path, "reservation").status != "FAILED":
            record_stage_failure(
                upload.remote_path,
                "reservation",
                "Post-reservation trash cleanup failed; upload was not started",
            )
        record_stage_skipped(upload.remote_path, "upload")
        return False

    if quota_reservation_enabled:
        with STATE.reserved_upload_bytes_lock:
            STATE.reserved_upload_bytes[upload.remote_path] = local_upload_bytes
        print_job_block(
            "UPLOAD RESERVATION JOB",
            job_number,
            upload.remote_path,
            (
                "Pre-upload size reservation planned from the pre-upload snapshot.\n"
                f"Filtered local files    : {local_file_count}\n"
                f"Managed size before plan: {format_bytes(reservation['current_size'])}\n"
                f"Filtered local size     : {format_bytes(local_upload_bytes)}\n"
                f"Reserved upload cap     : {format_bytes(reservation['reserved_upload_bytes'])}\n"
                f"Calculated deficit      : {format_bytes(reservation['required_free_bytes'])}\n"
                f"Selected complete files : {format_bytes(reservation['selected_free_bytes'])}\n"
                f"Projected temporary size: {format_bytes(reservation['projected_temporary_size'])}\n"
                f"Max total size          : {format_bytes(reservation['max_total_size_bytes'])}"
            ),
        )

    record_stage_success(upload.remote_path, "reservation")

    if STATE.sleep_after_step > 0:
        print_job_block(
            "REMOTE PIPELINE",
            job_number,
            upload.remote_path,
            f"Reservation complete; sleeping {STATE.sleep_after_step}s before upload",
        )
        time.sleep(STATE.sleep_after_step)

    if not upload_one_directory(job_number, upload):
        print_job_block(
            "REMOTE PIPELINE",
            job_number,
            upload.remote_path,
            "Upload failed. Post-upload cleanup and verification will still run.",
        )
        return False

    print_job_block(
        "REMOTE PIPELINE",
        job_number,
        upload.remote_path,
        "Pre-clean, reservation, and upload finished successfully",
    )
    return True


def run_reservation_and_upload_phase(
    cleanup_directories: list[CleanupTarget],
) -> bool:
    """Run independent pre-clean/reservation/upload pipelines concurrently."""
    clear_local_size_cache()
    print_step(
        "Starting independent PRE-SNAPSHOT -> CLEAN -> RESERVE -> UPLOAD pipelines "
        f"using up to {STATE.upload_threads} REMOTE PIPELINE JOB(s)"
    )
    failed = False

    with ThreadPoolExecutor(max_workers=STATE.upload_threads) as executor:
        future_to_upload = {
            executor.submit(
                reserve_and_upload_one_remote,
                index,
                upload,
                cleanup_directories,
            ): upload
            for index, upload in enumerate(STATE.upload_directories, start=1)
        }
        for future in as_completed(future_to_upload):
            upload = future_to_upload[future]
            try:
                success = future.result()
            except Exception as error:
                reservation_stage = get_stage_result(upload.remote_path, "reservation")
                stage_name = "reservation" if reservation_stage.status == "PENDING" else "upload"
                detail = f"Pre-clean/reservation/upload pipeline crashed: {error}"
                record_stage_failure(upload.remote_path, stage_name, detail)
                if stage_name == "reservation":
                    record_stage_skipped(upload.remote_path, "upload")
                print_job_block("REMOTE PIPELINE", 0, upload.remote_path, detail)
                failed = True
                continue
            if not success:
                failed = True

    return not failed


def post_cleanup_one_remote(
    job_number: int,
    upload: UploadDirectory,
    cleanup_directories: list[CleanupTarget],
) -> bool:
    """Fetch one post-upload snapshot, plan all cleanup/quota rules, and delete once."""
    targets = cleanup_targets_for_upload(cleanup_directories, upload)
    try:
        snapshot = fetch_remote_snapshot(upload.remote_path)
        print_snapshot_summary(job_number, "POST-UPLOAD", upload, snapshot)
        plan, _working_snapshot = build_post_upload_plan(upload, targets, snapshot)
    except Exception as error:
        detail = f"Post-upload snapshot/planning failed: {error}"
        record_stage_failure(upload.remote_path, "post_cleanup", detail)
        print_job_block("POST-UPLOAD PIPELINE", job_number, upload.remote_path, detail)
        return False

    if not execute_delete_plan(job_number, upload, plan, "post_cleanup"):
        return False
    if not cleanup_one_trash_remote(
        job_number,
        upload,
        "POST-UPLOAD TRASH CLEANUP",
    ):
        return False

    record_stage_success(upload.remote_path, "post_cleanup")
    return True


def run_post_upload_cleanup_phase(
    cleanup_directories: list[CleanupTarget],
) -> bool:
    """Run one-snapshot post-upload cleanup independently for every remote."""
    workers = max(STATE.cleanup_threads, STATE.remote_quota_cleanup_threads)
    print_step(
        "POST-UPLOAD: one recursive snapshot per remote for age, rule limits, and "
        f"max_total_size planning using up to {workers} JOB(s)"
    )
    failed = False
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_upload = {
            executor.submit(
                post_cleanup_one_remote,
                index,
                upload,
                cleanup_directories,
            ): upload
            for index, upload in enumerate(STATE.upload_directories, start=1)
        }
        for future in as_completed(future_to_upload):
            upload = future_to_upload[future]
            try:
                if not future.result():
                    failed = True
            except Exception as error:
                detail = f"Post-upload pipeline crashed: {error}"
                record_stage_failure(upload.remote_path, "post_cleanup", detail)
                print_job_block("POST-UPLOAD PIPELINE", 0, upload.remote_path, detail)
                failed = True

    finalize_stage_for_all("post_cleanup")
    return not failed


def verify_one_remote(
    job_number: int,
    upload: UploadDirectory,
    cleanup_directories: list[CleanupTarget],
) -> bool:
    targets = cleanup_targets_for_upload(cleanup_directories, upload)
    try:
        snapshot = fetch_remote_snapshot(upload.remote_path)
        print_snapshot_summary(job_number, "FINAL", upload, snapshot)
    except Exception as error:
        detail = f"Final remote snapshot failed: {error}"
        record_stage_failure(upload.remote_path, "final_quota", detail)
        print_job_block("FINAL SNAPSHOT VERIFY", job_number, upload.remote_path, detail)
        return False
    return verify_upload_snapshot(job_number, upload, targets, snapshot)


def run_final_verification(cleanup_directories: list[CleanupTarget]) -> bool:
    """Use exactly one final recursive snapshot per remote for every final limit check."""
    print_step("FINAL VERIFICATION: one recursive snapshot per remote for all limits")
    failed = False
    workers = max(STATE.cleanup_threads, STATE.remote_quota_cleanup_threads)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_upload = {
            executor.submit(
                verify_one_remote,
                index,
                upload,
                cleanup_directories,
            ): upload
            for index, upload in enumerate(STATE.upload_directories, start=1)
        }
        for future in as_completed(future_to_upload):
            upload = future_to_upload[future]
            try:
                if not future.result():
                    failed = True
            except Exception as error:
                detail = f"Final verification crashed: {error}"
                record_stage_failure(upload.remote_path, "final_quota", detail)
                print_job_block("FINAL SNAPSHOT VERIFY", 0, upload.remote_path, detail)
                failed = True

    finalize_stage_for_all("final_quota")
    return not failed
