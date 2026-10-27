"""Optional backend trash cleanup."""

from .progress import observe_step, skip_step
from .commands import run_command
from .models import UploadDirectory
from .output import print_job_block
from .results import command_error_summary, record_stage_failure, script_managed_trash_used


@observe_step(lambda args: "post_trash" if args["phase_name"].startswith("POST-UPLOAD") else "pre_trash")
def cleanup_one_trash_remote(
    job_number: int,
    upload: UploadDirectory,
    phase_name: str = "",
) -> bool:
    """Optionally run rclone cleanup for one upload remote."""
    stage_name = "post_cleanup" if phase_name.startswith("POST-UPLOAD") else "reservation"
    task_key = "post_trash" if stage_name == "post_cleanup" else "pre_trash"

    if not upload.empty_trash:
        skip_step(upload.remote_path, task_key, "Trash cleanup disabled")
        print_job_block(
            "TRASH CLEANUP JOB",
            job_number,
            upload.remote_path,
            "empty_trash=False, skipping rclone cleanup for this remote",
        )
        return True

    if not script_managed_trash_used(upload.remote_path, stage_name):
        skip_step(upload.remote_path, task_key, "No script-managed trash activity")
        print_job_block(
            "TRASH CLEANUP JOB",
            job_number,
            upload.remote_path,
            (
                "No script-managed trash-mode deletion was successfully executed or "
                "started for this stage; skipping rclone cleanup so unrelated backend "
                "trash is not emptied."
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
            skip_step(upload.remote_path, task_key, "Backend does not support trash cleanup")
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
