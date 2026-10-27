"""Barriered per-remote cleanup, trash, upload, and verification phase runners."""

from concurrent.futures import as_completed
from .process_lifecycle import StopAwareExecutor as ThreadPoolExecutor, wait_or_cancel

from .progress import step
from .cleanup import cleanup_one_trash_remote
from .delete_plan import execute_delete_plan
from .models import CleanupTarget, UploadDirectory
from .output import print_job_block, print_step
from .planning import (
    build_post_upload_plan,
    build_pre_upload_plan,
    cleanup_targets_for_upload,
)
from .remote_files import fetch_remote_snapshot
from .reservation import (
    clear_local_size_cache,
    get_filtered_local_upload_snapshot,
    select_local_upload_files,
)
from .results import (
    finalize_stage_for_all,
    record_stage_failure,
    record_stage_skipped,
    record_stage_success,
)
from .state import STATE
from .upload import upload_one_directory
from .utils import format_bytes, parse_size_to_bytes
from .verification import verify_upload_snapshot


def run_remote_workers(uploads, workers, operation, *args, stage, label, error_prefix,
                       skip_upload_on_error=False) -> dict[str, bool]:
    """Join one barrier's workers, isolating ordinary failures per destination.

    Cancellation exceptions propagate to StopAwareExecutor so queued work is
    cancelled. Callers choose eligibility and wait for this complete result map
    before starting the next stage; this helper makes no cleanup/upload decisions.
    """
    results = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(operation, index, upload, *args): upload
                   for index, upload in enumerate(uploads, start=1)}
        for future in as_completed(futures):
            upload = futures[future]
            try:
                success = future.result()
            except Exception as error:
                detail = f"{error_prefix}: {error}"
                record_stage_failure(upload.remote_path, stage, detail)
                if skip_upload_on_error:
                    record_stage_skipped(upload.remote_path, "upload")
                print_job_block(label, 0, upload.remote_path, detail)
                success = False
            results[upload.remote_path] = success
    return results


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
        with step(upload.remote_path, "pre_snapshot"):
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
    selected_upload_bytes = 0
    selected_upload_files = ()
    max_upload_bytes = 0
    quota_reservation_enabled = (
        upload.delete_excess_files and upload.max_total_size is not None
    )
    if quota_reservation_enabled:
        local_snapshot = get_filtered_local_upload_snapshot(job_number, upload)
        if local_snapshot is None:
            record_stage_skipped(upload.remote_path, "upload")
            return False
        local_upload_bytes = local_snapshot.total_bytes
        local_file_count = local_snapshot.file_count
        max_total_size_bytes = parse_size_to_bytes(upload.max_total_size)
        max_upload_bytes = max_total_size_bytes - STATE.reservation_safety_headroom_bytes
        if max_upload_bytes < 0:
            detail = (
                "Pre-upload cleanup/reservation planning failed: reservation safety "
                "headroom is larger than max_total_size"
            )
            record_stage_failure(upload.remote_path, "reservation", detail)
            record_stage_skipped(upload.remote_path, "upload")
            print_job_block("PRE-UPLOAD PREPARATION", job_number, upload.remote_path, detail)
            return False
        selected_upload_files, selected_upload_bytes = select_local_upload_files(
            local_snapshot,
            max_upload_bytes,
        )

    try:
        plan, _working_snapshot, reservation = build_pre_upload_plan(
            upload,
            targets,
            pre_snapshot,
            selected_upload_bytes,
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
        selected_file_count = len(selected_upload_files)
        skipped_file_count = local_file_count - selected_file_count
        skipped_bytes = local_upload_bytes - selected_upload_bytes
        with STATE.planned_upload_files_lock:
            STATE.planned_upload_files[upload.remote_path] = tuple(
                file.path for file in selected_upload_files
            )
        print_job_block(
            "UPLOAD RESERVATION JOB",
            job_number,
            upload.remote_path,
            (
                "Pre-upload complete-file reservation planned from the frozen local/remote snapshots.\n"
                f"Filtered local files     : {local_file_count}\n"
                f"Filtered local size      : {format_bytes(local_upload_bytes)}\n"
                f"Upload byte budget       : {format_bytes(max_upload_bytes)}\n"
                f"Selected upload files    : {selected_file_count}\n"
                f"Selected upload size     : {format_bytes(selected_upload_bytes)}\n"
                "Selection policy         : newest-first; stop at first file that does not fit\n"
                f"Quota cutoff reached     : {'Yes' if skipped_file_count else 'No'}\n"
                f"Files after cutoff       : {skipped_file_count}\n"
                f"Bytes after cutoff       : {format_bytes(skipped_bytes)}\n"
                f"Managed size before plan : {format_bytes(reservation['current_size'])}\n"
                f"Calculated deficit       : {format_bytes(reservation['required_free_bytes'])}\n"
                f"Remote bytes selected    : {format_bytes(reservation['selected_free_bytes'])}\n"
                f"Projected temporary size : {format_bytes(reservation['projected_temporary_size'])}\n"
                f"Max total size           : {format_bytes(reservation['max_total_size_bytes'])}"
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
        with step(upload.remote_path, "pre_delay"):
            wait_or_cancel(STATE.sleep_after_step)

    return True


def run_reservation_and_upload_phase(
    cleanup_directories: list[CleanupTarget],
) -> bool:
    """Run PREPARE -> TRASH CLEANUP -> UPLOAD with global barriers and per-remote failure isolation."""
    clear_local_size_cache()
    with STATE.planned_upload_files_lock:
        STATE.planned_upload_files.clear()
    preparation_workers = max(STATE.cleanup_threads, STATE.remote_quota_cleanup_threads)
    print_step(
        "PRE-UPLOAD PREPARATION: all remotes must finish snapshot/cleanup/reservation "
        f"before trash cleanup starts; using up to {preparation_workers} JOB(s)"
    )

    preparation_success = run_remote_workers(
        STATE.upload_directories, preparation_workers, prepare_one_remote_for_upload,
        cleanup_directories, stage="reservation", label="PRE-UPLOAD PREPARATION",
        error_prefix="Pre-upload preparation crashed", skip_upload_on_error=True,
    )

    trash_candidates = [
        upload
        for upload in STATE.upload_directories
        if preparation_success.get(upload.remote_path, False)
    ]
    print_step(
        "PRE-UPLOAD TRASH CLEANUP: preparation barrier reached; all eligible trash "
        f"cleanup jobs now run using up to {STATE.trash_cleanup_threads} JOB(s)"
    )

    trash_success = run_remote_workers(
        trash_candidates, STATE.trash_cleanup_threads, pre_upload_trash_cleanup_one_remote,
        stage="reservation", label="PRE-UPLOAD TRASH CLEANUP",
        error_prefix="Pre-upload trash cleanup crashed", skip_upload_on_error=True,
    )

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
    upload_success = run_remote_workers(
        upload_candidates, STATE.upload_threads, upload_one_directory,
        stage="upload", label="UPLOAD JOB", error_prefix="Upload worker crashed",
    )
    return all(preparation_success.values()) and all(trash_success.values()) and all(upload_success.values())


def post_cleanup_plan_one_remote(
    job_number: int,
    upload: UploadDirectory,
    cleanup_directories: list[CleanupTarget],
) -> bool:
    """Fetch the post-upload snapshot, plan all cleanup/quota rules, and execute planned deletes."""
    targets = cleanup_targets_for_upload(cleanup_directories, upload)
    try:
        with step(upload.remote_path, "post_snapshot"):
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
    cleanup_success = run_remote_workers(
        STATE.upload_directories, workers, post_cleanup_plan_one_remote, cleanup_directories,
        stage="post_cleanup", label="POST-UPLOAD CLEANUP",
        error_prefix="Post-upload cleanup worker crashed",
    )

    trash_candidates = [
        upload
        for upload in STATE.upload_directories
        if cleanup_success.get(upload.remote_path, False)
    ]
    print_step(
        "POST-UPLOAD TRASH CLEANUP: cleanup barrier reached; all eligible trash cleanup "
        f"jobs now run using up to {STATE.trash_cleanup_threads} JOB(s)"
    )
    trash_success = run_remote_workers(
        trash_candidates, STATE.trash_cleanup_threads, post_upload_trash_cleanup_one_remote,
        stage="post_cleanup", label="POST-UPLOAD TRASH CLEANUP",
        error_prefix="Post-upload trash cleanup worker crashed",
    )
    finalize_stage_for_all("post_cleanup")
    return all(cleanup_success.values()) and all(trash_success.values())


def verify_one_remote(
    job_number: int,
    upload: UploadDirectory,
    cleanup_directories: list[CleanupTarget],
) -> bool:
    """Fetch one final live snapshot and verify every configured limit for that remote."""
    targets = cleanup_targets_for_upload(cleanup_directories, upload)
    try:
        with step(upload.remote_path, "final_snapshot"):
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
    workers = max(STATE.cleanup_threads, STATE.remote_quota_cleanup_threads)
    success = run_remote_workers(
        STATE.upload_directories, workers, verify_one_remote, cleanup_directories,
        stage="final_quota", label="FINAL SNAPSHOT VERIFY",
        error_prefix="Final verification crashed",
    )
    finalize_stage_for_all("final_quota")
    return all(success.values())
