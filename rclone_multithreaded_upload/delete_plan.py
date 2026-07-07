"""Combined remote delete planning and execution."""

from collections import defaultdict
from pathlib import Path

from .commands import run_command
from .models import CleanupTarget, PlannedDeletion, RemoteDeletePlan, RemoteFile, RemoteSnapshot, UploadDirectory
from .output import print_job_block
from .rclone_backend import get_delete_mode_options
from .results import command_error_summary, record_stage_failure
from .state import STATE
from .utils import format_bytes, remote_name_from_path


def add_planned_deletion(
    plan: RemoteDeletePlan,
    snapshot: RemoteSnapshot,
    file: RemoteFile,
    *,
    delete_to_trash: bool,
    reason: str,
) -> bool:
    """Select a file once and immediately remove it from the working snapshot."""
    if file.path in plan.entries:
        return False
    current = snapshot.files_by_path.pop(file.path, None)
    if current is None:
        return False
    plan.entries[file.path] = PlannedDeletion(
        file=current,
        delete_to_trash=delete_to_trash,
        reason=reason,
    )
    return True


def planned_delete_bytes(plan: RemoteDeletePlan) -> int:
    return sum(entry.file.size for entry in plan.entries.values())


def print_delete_plan_summary(job_number: int, plan: RemoteDeletePlan) -> None:
    reason_counts: dict[str, int] = defaultdict(int)
    for entry in plan.entries.values():
        reason_counts[entry.reason] += 1
    reason_text = ", ".join(
        f"{reason}={count}" for reason, count in sorted(reason_counts.items())
    ) or "none"
    print_job_block(
        "COMBINED DELETE PLAN",
        job_number,
        plan.remote_path,
        (
            f"Phase              : {plan.phase_name}\n"
            f"Files marked       : {len(plan.entries)}\n"
            f"Bytes marked       : {format_bytes(planned_delete_bytes(plan))}\n"
            f"Selection reasons  : {reason_text}"
        ),
    )


def execute_delete_plan(
    job_number: int,
    upload: UploadDirectory,
    plan: RemoteDeletePlan,
    stage_name: str,
) -> bool:
    """Execute the combined plan, using one delete command per required delete mode."""
    print_delete_plan_summary(job_number, plan)
    if not plan.entries:
        print_job_block(
            "COMBINED DELETE",
            job_number,
            upload.remote_path,
            f"{plan.phase_name}: no files need deletion",
        )
        return True

    STATE.delete_list_dir.mkdir(parents=True, exist_ok=True)
    groups: dict[bool, list[PlannedDeletion]] = defaultdict(list)
    for entry in plan.entries.values():
        groups[entry.delete_to_trash].append(entry)

    safe_name = remote_name_from_path(upload.remote_path)
    safe_phase = plan.phase_name.lower().replace(" ", "-")

    for delete_to_trash, entries in groups.items():
        entries.sort(key=lambda entry: entry.file.path)
        mode_name = "trash" if delete_to_trash else "hard"
        delete_list_path = (
            STATE.delete_list_dir
            / f"to-delete-{safe_phase}-{mode_name}-{safe_name}"
        )
        delete_list_path.write_text(
            "\n".join(entry.file.path for entry in entries) + "\n",
            encoding="utf-8",
        )

        option_target = CleanupTarget(
            path=upload.remote_path,
            delete_to_trash=delete_to_trash,
        )
        command = [
            "rclone",
            "delete",
            "--files-from",
            str(delete_list_path),
            upload.remote_path,
        ] + get_delete_mode_options(option_target)
        result = run_command(command, capture_output=True)
        output = (result.stdout or "") + (result.stderr or "")

        if output:
            print_job_block("COMBINED DELETE", job_number, upload.remote_path, output)

        if result.returncode != 0:
            detail = (
                f"Command: {' '.join(command)}\n"
                f"Return code: {result.returncode}\n"
                f"{command_error_summary(output)}"
            )
            record_stage_failure(upload.remote_path, stage_name, detail)
            print_job_block(
                "COMBINED DELETE",
                job_number,
                upload.remote_path,
                f"{plan.phase_name} delete failed.\n{detail}",
            )
            return False

        print_job_block(
            "COMBINED DELETE",
            job_number,
            upload.remote_path,
            (
                f"{plan.phase_name}: deleted {len(entries)} planned file(s) "
                f"using {mode_name} mode"
            ),
        )

    return True
