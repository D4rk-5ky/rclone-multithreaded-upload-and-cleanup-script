"""Cleanup-rule, remote quota, and trash cleanup jobs."""

from .commands import is_directory_not_found, run_command
from .models import CleanupTarget, UploadDirectory
from .output import print_job_block
from .rclone_backend import get_delete_mode_options
from .remote_files import make_delete_list, make_upload_remote_quota_delete_list
from .results import command_error_summary, record_stage_failure
from .state import STATE


def cleanup_one_directory(
    job_number: int,
    target: CleanupTarget,
    phase_name: str = "",
) -> bool:
    """Run age and/or excess-file cleanup for one generated target."""
    stage_name = "post_cleanup" if phase_name.startswith("POST-UPLOAD") else "reservation"

    print_job_block(
        "CLEANUP JOB",
        job_number,
        target.path,
        "Starting cleanup job",
    )

    if not target.delete_old_files and not target.delete_excess_files:
        print_job_block(
            "CLEANUP JOB",
            job_number,
            target.path,
            "Both delete_old_files and delete_excess_files are disabled, skipping",
        )
        return True

    if target.delete_old_files:
        command = [
            "rclone",
            "delete",
            target.path,
            "--min-age",
            STATE.delete_min_age,
        ] + get_delete_mode_options(target)
        result = run_command(command, capture_output=True)
        output = (result.stdout or "") + (result.stderr or "")

        if output:
            print_job_block("CLEANUP JOB", job_number, target.path, output)

        if result.returncode != 0:
            if is_directory_not_found(result):
                print_job_block(
                    "CLEANUP JOB",
                    job_number,
                    target.path,
                    "Remote folder does not exist yet, skipping cleanup for this folder",
                )
                return True

            detail = (
                f"Command: {' '.join(command)}\n"
                f"Return code: {result.returncode}\n"
                f"{command_error_summary(output)}"
            )
            record_stage_failure(target.owner_remote_path, stage_name, detail)
            print_job_block(
                "CLEANUP JOB",
                job_number,
                target.path,
                f"Failed deleting old files.\n{detail}",
            )
            return False
    else:
        print_job_block(
            "CLEANUP JOB",
            job_number,
            target.path,
            "Skipping delete_old_files for this target",
        )

    if not target.delete_excess_files:
        print_job_block(
            "CLEANUP JOB",
            job_number,
            target.path,
            "Skipping delete_excess_files for this target",
        )
        return True

    if target.max_files is None and target.max_size is None:
        print_job_block(
            "CLEANUP JOB",
            job_number,
            target.path,
            "delete_excess_files=True but no max_files or max_size is set, skipping",
        )
        return True

    try:
        delete_list_path = make_delete_list(target)
    except Exception as error:
        detail = f"Failed making delete list: {error}"
        record_stage_failure(target.owner_remote_path, stage_name, detail)
        print_job_block("CLEANUP JOB", job_number, target.path, detail)
        return False

    if delete_list_path.stat().st_size == 0:
        print_job_block(
            "CLEANUP JOB",
            job_number,
            target.path,
            "No excessive files to delete",
        )
        return True

    command = [
        "rclone",
        "delete",
        "--files-from",
        str(delete_list_path),
        target.path,
    ] + get_delete_mode_options(target)
    result = run_command(command, capture_output=True)
    output = (result.stdout or "") + (result.stderr or "")

    if output:
        print_job_block("CLEANUP JOB", job_number, target.path, output)

    if result.returncode != 0:
        detail = (
            f"Command: {' '.join(command)}\n"
            f"Return code: {result.returncode}\n"
            f"{command_error_summary(output)}"
        )
        record_stage_failure(target.owner_remote_path, stage_name, detail)
        print_job_block(
            "CLEANUP JOB",
            job_number,
            target.path,
            f"Failed deleting excessive files.\n{detail}",
        )
        return False

    print_job_block(
        "CLEANUP JOB",
        job_number,
        target.path,
        "Cleanup job finished successfully",
    )
    return True


def cleanup_one_upload_remote_quota(
    job_number: int,
    upload: UploadDirectory,
    phase_name: str = "",
) -> bool:
    """Enforce upload.max_total_size across this remote's managed cleanup rules."""
    stage_name = "post_cleanup" if phase_name.startswith("POST-UPLOAD") else "reservation"

    print_job_block(
        "REMOTE QUOTA CLEANUP JOB",
        job_number,
        upload.remote_path,
        "Starting remote-wide quota cleanup job",
    )

    if not upload.delete_excess_files:
        print_job_block(
            "REMOTE QUOTA CLEANUP JOB",
            job_number,
            upload.remote_path,
            "delete_excess_files=False, skipping remote-wide quota cleanup",
        )
        return True

    if upload.max_total_size is None:
        print_job_block(
            "REMOTE QUOTA CLEANUP JOB",
            job_number,
            upload.remote_path,
            "max_total_size=None, skipping remote-wide quota cleanup",
        )
        return True

    try:
        delete_list_path = make_upload_remote_quota_delete_list(upload)
    except Exception as error:
        detail = f"Failed making remote quota delete list: {error}"
        record_stage_failure(upload.remote_path, stage_name, detail)
        print_job_block(
            "REMOTE QUOTA CLEANUP JOB",
            job_number,
            upload.remote_path,
            detail,
        )
        return False

    if delete_list_path.stat().st_size == 0:
        print_job_block(
            "REMOTE QUOTA CLEANUP JOB",
            job_number,
            upload.remote_path,
            "No remote-wide quota files to delete",
        )
        return True

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
            "REMOTE QUOTA CLEANUP JOB",
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
        record_stage_failure(upload.remote_path, stage_name, detail)
        print_job_block(
            "REMOTE QUOTA CLEANUP JOB",
            job_number,
            upload.remote_path,
            f"Failed deleting remote-wide quota files.\n{detail}",
        )
        return False

    print_job_block(
        "REMOTE QUOTA CLEANUP JOB",
        job_number,
        upload.remote_path,
        "Remote-wide quota cleanup finished successfully",
    )
    return True


def cleanup_one_trash_remote(
    job_number: int,
    upload: UploadDirectory,
    phase_name: str = "",
) -> bool:
    """Optionally run rclone cleanup for one upload remote."""
    stage_name = "post_cleanup" if phase_name.startswith("POST-UPLOAD") else "reservation"

    if not upload.empty_trash:
        print_job_block(
            "TRASH CLEANUP JOB",
            job_number,
            upload.remote_path,
            "empty_trash=False, skipping rclone cleanup for this remote",
        )
        return True

    if not upload.delete_to_trash:
        print_job_block(
            "TRASH CLEANUP JOB",
            job_number,
            upload.remote_path,
            (
                "delete_to_trash=False, script cleanup deletions are direct; "
                "skipping rclone cleanup because no script-managed trash needs emptying"
            ),
        )
        return True

    print_job_block(
        "TRASH CLEANUP JOB",
        job_number,
        upload.remote_path,
        "Starting rclone cleanup / empty trash",
    )

    command = ["rclone", "cleanup", upload.remote_path]
    result = run_command(command, capture_output=True)
    output = (result.stdout or "") + (result.stderr or "")

    if result.returncode != 0:
        if "not supported" in output.lower() or "doesn't support" in output.lower():
            print_job_block(
                "TRASH CLEANUP JOB",
                job_number,
                upload.remote_path,
                f"rclone cleanup is not supported for this remote, skipping.\n\n{output}",
            )
            return True

        detail = (
            f"Command: {' '.join(command)}\n"
            f"Return code: {result.returncode}\n"
            f"{command_error_summary(output)}"
        )
        record_stage_failure(upload.remote_path, stage_name, detail)
        print_job_block(
            "TRASH CLEANUP JOB",
            job_number,
            upload.remote_path,
            f"Trash cleanup failed.\n{detail}",
        )
        return False

    if output:
        print_job_block("TRASH CLEANUP JOB", job_number, upload.remote_path, output)

    print_job_block(
        "TRASH CLEANUP JOB",
        job_number,
        upload.remote_path,
        "Trash cleanup finished successfully",
    )
    return True
