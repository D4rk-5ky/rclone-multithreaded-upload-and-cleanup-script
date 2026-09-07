"""Barriered per-remote cleanup, trash, upload, and verification phase runners."""

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
    """Print one compact recursive-snapshot summary without interleaving threads."""
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


def prepare_one_remote_for_upload(
    job_number: int,
    upload: UploadDirectory,
    cleanup_directories: list[CleanupTarget],
) -> bool:
    """Plan and execute one remote's pre-upload cleanup/reservation, but do not trash-clean or upload yet."""
    print_job_block(
        "PRE-UPLOAD PREPARATION",
        job_number,
        upload.remote_path,
        "Starting pre-upload snapshot, cleanup planning, and reservation",
    )
    targets = cleanup_targets_for_upload(cleanup_directories, upload)

    try:
        pre_snapshot = fetch_remote_snapshot(upload.remote_path)
        print_snapshot_summary(job_number, "PRE-UPLOAD", upload, pre_snapshot)
    except Exception as error:
        detail = f"Pre-upload remote snapshot failed: {error}"
        record_stage_failure(upload.remote_path, "reservation", detail)
        record_stage_skipped(upload.remote_path, "upload")
        print_job_block("PRE-UPLOAD PREPARATION", job_number, upload.remote_path, detail)
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
        print_job_block("PRE-UPLOAD PREPARATION", job_number, upload.remote_path, detail)
        return False

    if not execute_delete_plan(job_number, upload, plan, "reservation"):
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

    print_job_block(
        "PRE-UPLOAD PREPARATION",
        job_number,
        upload.remote_path,
        "Pre-upload cleanup/reservation preparation finished; waiting at stage barrier",
    )
    return True


def pre_upload_trash_cleanup_one_remote(
    job_number: int,
    upload: UploadDirectory,
) -> bool:
    """Run the pre-upload trash cleanup after every preparation worker has reached the barrier."""
    if not cleanup_one_trash_remote(
        job_number,
        upload,
        "POST-RESERVATION TRASH CLEANUP",
    ):
        record_stage_skipped(upload.remote_path, "upload")
        return False

    record_stage_success(upload.remote_path, "reservation")

    if STATE.sleep_after_step > 0:
        print_job_block(
            "PRE-UPLOAD TRASH CLEANUP",
            job_number,
            upload.remote_path,
            f"Preparation/trash cleanup complete; sleeping {STATE.sleep_after_step}s before upload barrier",
        )
        time.sleep(STATE.sleep_after_step)

    return True


def run_reservation_and_upload_phase(
    cleanup_directories: list[CleanupTarget],
) -> bool:
    """Run PREPARE -> TRASH CLEANUP -> UPLOAD with global barriers and per-remote failure isolation."""
    clear_local_size_cache()
    failed = False

    preparation_workers = max(STATE.cleanup_threads, STATE.remote_quota_cleanup_threads)
    print_step(
        "PRE-UPLOAD PREPARATION: all remotes must finish snapshot/cleanup/reservation "
        f"before trash cleanup starts; using up to {preparation_workers} JOB(s)"
    )

    preparation_success: dict[str, bool] = {}
    with ThreadPoolExecutor(max_workers=preparation_workers) as executor:
        future_to_upload = {
            executor.submit(
                prepare_one_remote_for_upload,
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
                detail = f"Pre-upload preparation crashed: {error}"
                record_stage_failure(upload.remote_path, "reservation", detail)
                record_stage_skipped(upload.remote_path, "upload")
                print_job_block("PRE-UPLOAD PREPARATION", 0, upload.remote_path, detail)
                success = False
            preparation_success[upload.remote_path] = success
            if not success:
                failed = True

    trash_candidates = [
        upload
        for upload in STATE.upload_directories
        if preparation_success.get(upload.remote_path, False)
    ]
    print_step(
        "PRE-UPLOAD TRASH CLEANUP: preparation barrier reached; all eligible trash "
        f"cleanup jobs now run using up to {STATE.trash_cleanup_threads} JOB(s)"
    )

    trash_success: dict[str, bool] = {}
    with ThreadPoolExecutor(max_workers=STATE.trash_cleanup_threads) as executor:
        future_to_upload = {
            executor.submit(pre_upload_trash_cleanup_one_remote, index, upload): upload
            for index, upload in enumerate(trash_candidates, start=1)
        }
        for future in as_completed(future_to_upload):
            upload = future_to_upload[future]
            try:
                success = future.result()
            except Exception as error:
                detail = f"Pre-upload trash cleanup crashed: {error}"
                record_stage_failure(upload.remote_path, "reservation", detail)
                record_stage_skipped(upload.remote_path, "upload")
                print_job_block("PRE-UPLOAD TRASH CLEANUP", 0, upload.remote_path, detail)
                success = False
            trash_success[upload.remote_path] = success
            if not success:
                failed = True

    upload_candidates = [
        upload
        for upload in STATE.upload_directories
        if preparation_success.get(upload.remote_path, False)
        and trash_success.get(upload.remote_path, False)
    ]
    upload_candidate_paths = {upload.remote_path for upload in upload_candidates}
    for upload in STATE.upload_directories:
        if upload.remote_path not in upload_candidate_paths:
            record_stage_skipped(upload.remote_path, "upload")

    print_step(
        "UPLOAD: trash-cleanup barrier reached; all eligible uploads now run using "
        f"up to {STATE.upload_threads} JOB(s)"
    )
    with ThreadPoolExecutor(max_workers=STATE.upload_threads) as executor:
        future_to_upload = {
            executor.submit(upload_one_directory, index, upload): upload
            for index, upload in enumerate(upload_candidates, start=1)
        }
        for future in as_completed(future_to_upload):
            upload = future_to_upload[future]
            try:
                success = future.result()
            except Exception as error:
                detail = f"Upload worker crashed: {error}"
                record_stage_failure(upload.remote_path, "upload", detail)
                print_job_block("UPLOAD JOB", 0, upload.remote_path, detail)
                success = False
            if not success:
                failed = True

    return not failed


def post_cleanup_plan_one_remote(
    job_number: int,
    upload: UploadDirectory,
    cleanup_directories: list[CleanupTarget],
) -> bool:
    """Fetch the post-upload snapshot, plan all cleanup/quota rules, and execute planned deletes."""
    targets = cleanup_targets_for_upload(cleanup_directories, upload)
    try:
        snapshot = fetch_remote_snapshot(upload.remote_path)
        print_snapshot_summary(job_number, "POST-UPLOAD", upload, snapshot)
        plan, _working_snapshot = build_post_upload_plan(upload, targets, snapshot)
    except Exception as error:
        detail = f"Post-upload snapshot/planning failed: {error}"
        record_stage_failure(upload.remote_path, "post_cleanup", detail)
        print_job_block("POST-UPLOAD CLEANUP", job_number, upload.remote_path, detail)
        return False

    if not execute_delete_plan(job_number, upload, plan, "post_cleanup"):
        return False

    print_job_block(
        "POST-UPLOAD CLEANUP",
        job_number,
        upload.remote_path,
        "Post-upload cleanup deletion stage finished; waiting at trash-cleanup barrier",
    )
    return True


def post_upload_trash_cleanup_one_remote(
    job_number: int,
    upload: UploadDirectory,
) -> bool:
    """Run post-upload trash cleanup only after all post-upload cleanup jobs have finished."""
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
    """Run post-upload CLEANUP -> TRASH CLEANUP with a barrier and isolated worker failures."""
    workers = max(STATE.cleanup_threads, STATE.remote_quota_cleanup_threads)
    print_step(
        "POST-UPLOAD CLEANUP: one recursive snapshot per remote for age, rule limits, "
        f"and max_total_size planning using up to {workers} JOB(s)"
    )
    failed = False
    cleanup_success: dict[str, bool] = {}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_upload = {
            executor.submit(
                post_cleanup_plan_one_remote,
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
                detail = f"Post-upload cleanup worker crashed: {error}"
                record_stage_failure(upload.remote_path, "post_cleanup", detail)
                print_job_block("POST-UPLOAD CLEANUP", 0, upload.remote_path, detail)
                success = False
            cleanup_success[upload.remote_path] = success
            if not success:
                failed = True

    trash_candidates = [
        upload
        for upload in STATE.upload_directories
        if cleanup_success.get(upload.remote_path, False)
    ]
    print_step(
        "POST-UPLOAD TRASH CLEANUP: cleanup barrier reached; all eligible trash cleanup "
        f"jobs now run using up to {STATE.trash_cleanup_threads} JOB(s)"
    )
    with ThreadPoolExecutor(max_workers=STATE.trash_cleanup_threads) as executor:
        future_to_upload = {
            executor.submit(post_upload_trash_cleanup_one_remote, index, upload): upload
            for index, upload in enumerate(trash_candidates, start=1)
        }
        for future in as_completed(future_to_upload):
            upload = future_to_upload[future]
            try:
                if not future.result():
                    failed = True
            except Exception as error:
                detail = f"Post-upload trash cleanup worker crashed: {error}"
                record_stage_failure(upload.remote_path, "post_cleanup", detail)
                print_job_block("POST-UPLOAD TRASH CLEANUP", 0, upload.remote_path, detail)
                failed = True

    finalize_stage_for_all("post_cleanup")
    return not failed


def verify_one_remote(
    job_number: int,
    upload: UploadDirectory,
    cleanup_directories: list[CleanupTarget],
) -> bool:
    """Fetch one final live snapshot and verify every configured limit for that remote."""
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
