"""Combined planned deletion execution and optional backend trash cleanup."""

from .commands import run_command
from .delete_plan import execute_delete_plan
from .models import RemoteDeletePlan, UploadDirectory
from .output import print_job_block
from .results import command_error_summary, record_stage_failure


__all__ = ["execute_delete_plan", "cleanup_one_trash_remote"]


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
