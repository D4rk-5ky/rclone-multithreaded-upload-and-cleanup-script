"""Concurrent phase runners and per-remote reservation/upload pipelines."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from .cleanup import (
    cleanup_one_directory,
    cleanup_one_trash_remote,
    cleanup_one_upload_remote_quota,
)
from .models import CleanupTarget, UploadDirectory
from .output import print_job_block, print_step
from .reservation import reserve_one_upload_remote_space
from .results import (
    finalize_stage_for_all,
    get_stage_result,
    record_stage_failure,
    record_stage_skipped,
)
from .state import STATE
from .upload import upload_one_directory
from .verification import verify_one_cleanup_target, verify_one_upload_remote_quota


def run_cleanup_phase(
    cleanup_directories: list[CleanupTarget],
    phase_name: str,
) -> bool:
    print_step(
        f"{phase_name}: cleaning remote folders using up to "
        f"{STATE.cleanup_threads} CLEANUP JOB(s)"
    )
    failed = False
    stage_name = "post_cleanup" if phase_name.startswith("POST-UPLOAD") else "reservation"

    with ThreadPoolExecutor(max_workers=STATE.cleanup_threads) as executor:
        future_to_target = {
            executor.submit(cleanup_one_directory, index, target, phase_name): target
            for index, target in enumerate(cleanup_directories, start=1)
        }
        for future in as_completed(future_to_target):
            target = future_to_target[future]
            try:
                success = future.result()
            except Exception as error:
                detail = f"{phase_name} cleanup job crashed: {error}"
                record_stage_failure(target.owner_remote_path, stage_name, detail)
                print_job_block("CLEANUP JOB", 0, target.path, detail)
                failed = True
                continue
            if not success:
                failed = True

    return not failed


def run_remote_quota_phase(phase_name: str) -> bool:
    print_step(
        f"{phase_name}: enforcing max_total_size using up to "
        f"{STATE.remote_quota_cleanup_threads} REMOTE QUOTA CLEANUP JOB(s)"
    )
    failed = False
    stage_name = "post_cleanup" if phase_name.startswith("POST-UPLOAD") else "reservation"

    with ThreadPoolExecutor(max_workers=STATE.remote_quota_cleanup_threads) as executor:
        future_to_upload = {
            executor.submit(
                cleanup_one_upload_remote_quota,
                index,
                upload,
                phase_name,
            ): upload
            for index, upload in enumerate(STATE.upload_directories, start=1)
        }
        for future in as_completed(future_to_upload):
            upload = future_to_upload[future]
            try:
                success = future.result()
            except Exception as error:
                detail = f"{phase_name} quota cleanup crashed: {error}"
                record_stage_failure(upload.remote_path, stage_name, detail)
                print_job_block(
                    "REMOTE QUOTA CLEANUP JOB",
                    0,
                    upload.remote_path,
                    detail,
                )
                failed = True
                continue
            if not success:
                failed = True

    return not failed


def run_trash_cleanup_phase(phase_name: str) -> bool:
    print_step(
        f"{phase_name}: cleaning rclone trash using up to "
        f"{STATE.trash_cleanup_threads} TRASH CLEANUP JOB(s)"
    )
    failed = False
    stage_name = "post_cleanup" if phase_name.startswith("POST-UPLOAD") else "reservation"

    with ThreadPoolExecutor(max_workers=STATE.trash_cleanup_threads) as executor:
        future_to_upload = {
            executor.submit(cleanup_one_trash_remote, index, upload, phase_name): upload
            for index, upload in enumerate(STATE.upload_directories, start=1)
        }
        for future in as_completed(future_to_upload):
            upload = future_to_upload[future]
            try:
                success = future.result()
            except Exception as error:
                detail = f"{phase_name} trash cleanup crashed: {error}"
                record_stage_failure(upload.remote_path, stage_name, detail)
                print_job_block("TRASH CLEANUP JOB", 0, upload.remote_path, detail)
                failed = True
                continue
            if not success:
                failed = True

    return not failed


def reserve_and_upload_one_remote(job_number: int, upload: UploadDirectory) -> bool:
    """Size-reserve and upload one remote independently."""
    print_job_block(
        "REMOTE PIPELINE",
        job_number,
        upload.remote_path,
        "Starting independent reservation/upload pipeline",
    )

    if not reserve_one_upload_remote_space(job_number, upload):
        if get_stage_result(upload.remote_path, "reservation").status == "PENDING":
            record_stage_failure(upload.remote_path, "reservation", "Reservation failed")
        record_stage_skipped(upload.remote_path, "upload")
        print_job_block(
            "REMOTE PIPELINE",
            job_number,
            upload.remote_path,
            "Reservation failed; upload for this remote was not started",
        )
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
        print_job_block(
            "REMOTE PIPELINE",
            job_number,
            upload.remote_path,
            "Post-reservation trash cleanup failed; upload for this remote was not started",
        )
        return False

    if STATE.sleep_after_step > 0:
        print_job_block(
            "REMOTE PIPELINE",
            job_number,
            upload.remote_path,
            (
                f"Reservation complete; sleeping {STATE.sleep_after_step}s "
                "before starting this upload"
            ),
        )
        time.sleep(STATE.sleep_after_step)

    if not upload_one_directory(job_number, upload):
        print_job_block(
            "REMOTE PIPELINE",
            job_number,
            upload.remote_path,
            "Upload failed. Global post-upload cleanup and verification will still run.",
        )
        return False

    print_job_block(
        "REMOTE PIPELINE",
        job_number,
        upload.remote_path,
        "Reservation and upload finished successfully for this remote",
    )
    return True


def run_reservation_and_upload_phase() -> bool:
    """Run independent reservation/upload pipelines concurrently."""
    print_step(
        "Starting independent reservation/upload pipelines using up to "
        f"{STATE.upload_threads} REMOTE PIPELINE JOB(s)"
    )
    failed = False

    with ThreadPoolExecutor(max_workers=STATE.upload_threads) as executor:
        future_to_upload = {
            executor.submit(reserve_and_upload_one_remote, index, upload): upload
            for index, upload in enumerate(STATE.upload_directories, start=1)
        }
        for future in as_completed(future_to_upload):
            upload = future_to_upload[future]
            try:
                success = future.result()
            except Exception as error:
                reservation_stage = get_stage_result(upload.remote_path, "reservation")
                stage_name = (
                    "reservation" if reservation_stage.status == "PENDING" else "upload"
                )
                detail = f"Reservation/upload pipeline crashed: {error}"
                record_stage_failure(upload.remote_path, stage_name, detail)
                if stage_name == "reservation":
                    record_stage_skipped(upload.remote_path, "upload")
                print_job_block("REMOTE PIPELINE", 0, upload.remote_path, detail)
                failed = True
                continue
            if not success:
                failed = True

    return not failed


def run_final_verification(cleanup_directories: list[CleanupTarget]) -> bool:
    print_step("Verifying final cleanup rule and max_total_size limits")
    failed = False

    with ThreadPoolExecutor(max_workers=STATE.cleanup_threads) as executor:
        future_to_target = {
            executor.submit(verify_one_cleanup_target, index, target): target
            for index, target in enumerate(cleanup_directories, start=1)
        }
        for future in as_completed(future_to_target):
            target = future_to_target[future]
            try:
                if not future.result():
                    failed = True
            except Exception as error:
                detail = f"Final target verification crashed: {error}"
                record_stage_failure(target.owner_remote_path, "final_quota", detail)
                print_job_block("VERIFY CLEANUP TARGET", 0, target.path, detail)
                failed = True

    with ThreadPoolExecutor(max_workers=STATE.remote_quota_cleanup_threads) as executor:
        future_to_upload = {
            executor.submit(verify_one_upload_remote_quota, index, upload): upload
            for index, upload in enumerate(STATE.upload_directories, start=1)
        }
        for future in as_completed(future_to_upload):
            upload = future_to_upload[future]
            try:
                if not future.result():
                    failed = True
            except Exception as error:
                detail = f"Final quota verification crashed: {error}"
                record_stage_failure(upload.remote_path, "final_quota", detail)
                print_job_block("VERIFY REMOTE QUOTA", 0, upload.remote_path, detail)
                failed = True

    finalize_stage_for_all("final_quota")
    return not failed
