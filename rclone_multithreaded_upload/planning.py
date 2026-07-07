"""Pure in-memory cleanup, quota, and upload-reservation planning."""

from datetime import datetime, timezone

from .delete_plan import add_planned_deletion
from .models import CleanupTarget, RemoteDeletePlan, RemoteSnapshot, UploadDirectory
from .remote_files import (
    files_below_relative_path,
    get_managed_snapshot_files,
    relative_cleanup_target_path,
)
from .reservation import transfer_cap_bytes
from .state import STATE
from .utils import (
    format_bytes,
    parse_rclone_age_cutoff,
    parse_rclone_modtime,
    parse_size_to_bytes,
    remote_file_oldest_sort_key,
)


def cleanup_targets_for_upload(
    cleanup_directories: list[CleanupTarget],
    upload: UploadDirectory,
) -> list[CleanupTarget]:
    return [
        target
        for target in cleanup_directories
        if target.owner_remote_path == upload.remote_path
    ]


def plan_cleanup_targets(
    upload: UploadDirectory,
    targets: list[CleanupTarget],
    snapshot: RemoteSnapshot,
    plan: RemoteDeletePlan,
    *,
    now: datetime | None = None,
) -> None:
    """Apply age then max_files/max_size rules to one working snapshot."""
    current_time = now or datetime.now(timezone.utc)
    age_cutoff = None
    if any(target.delete_old_files for target in targets):
        age_cutoff = parse_rclone_age_cutoff(STATE.delete_min_age, current_time)

    for target in targets:
        relative_path = relative_cleanup_target_path(upload, target)

        if target.delete_old_files and age_cutoff is not None:
            aged_files = sorted(
                (
                    file
                    for file in files_below_relative_path(snapshot, relative_path)
                    if parse_rclone_modtime(file.modified) < age_cutoff
                ),
                key=remote_file_oldest_sort_key,
            )
            for file in aged_files:
                add_planned_deletion(
                    plan,
                    snapshot,
                    file,
                    delete_to_trash=target.delete_to_trash,
                    reason=f"age:{target.path}",
                )

        if not target.delete_excess_files:
            continue
        if target.max_files is None and target.max_size is None:
            continue

        files = sorted(
            files_below_relative_path(snapshot, relative_path),
            key=remote_file_oldest_sort_key,
        )
        current_files = len(files)
        current_size = sum(file.size for file in files)
        max_size_bytes = (
            parse_size_to_bytes(target.max_size)
            if target.max_size is not None
            else None
        )

        for file in files:
            too_many_files = (
                target.max_files is not None and current_files > target.max_files
            )
            too_much_size = (
                max_size_bytes is not None and current_size > max_size_bytes
            )
            if not too_many_files and not too_much_size:
                break
            if add_planned_deletion(
                plan,
                snapshot,
                file,
                delete_to_trash=target.delete_to_trash,
                reason=f"limits:{target.path}",
            ):
                current_files -= 1
                current_size -= file.size


def plan_remote_quota_cleanup(
    upload: UploadDirectory,
    snapshot: RemoteSnapshot,
    plan: RemoteDeletePlan,
) -> None:
    """Plan oldest-first max_total_size cleanup from the current working snapshot."""
    if not upload.delete_excess_files or upload.max_total_size is None:
        return

    max_total_size_bytes = parse_size_to_bytes(upload.max_total_size)
    files = sorted(
        get_managed_snapshot_files(upload, snapshot),
        key=remote_file_oldest_sort_key,
    )
    current_size = sum(file.size for file in files)

    for file in files:
        if current_size <= max_total_size_bytes:
            break
        if add_planned_deletion(
            plan,
            snapshot,
            file,
            delete_to_trash=upload.delete_to_trash,
            reason="max_total_size",
        ):
            current_size -= file.size


def plan_upload_reservation(
    upload: UploadDirectory,
    snapshot: RemoteSnapshot,
    plan: RemoteDeletePlan,
    local_upload_bytes: int,
) -> dict[str, int]:
    """Reserve upload capacity using the already-cleaned working snapshot."""
    if not upload.delete_excess_files or upload.max_total_size is None:
        return {
            "current_size": 0,
            "required_free_bytes": 0,
            "selected_free_bytes": 0,
            "reserved_upload_bytes": transfer_cap_bytes(local_upload_bytes),
            "projected_temporary_size": 0,
            "max_total_size_bytes": 0,
        }

    max_total_size_bytes = parse_size_to_bytes(upload.max_total_size)
    reserved_upload_bytes = transfer_cap_bytes(local_upload_bytes)
    if reserved_upload_bytes + STATE.reservation_safety_headroom_bytes > max_total_size_bytes:
        raise ValueError(
            "Filtered local source cannot fit on an empty managed remote with the "
            "required reservation safety headroom. "
            f"Filtered local size={format_bytes(local_upload_bytes)}, "
            f"reserved upload cap={format_bytes(reserved_upload_bytes)}, "
            f"headroom={format_bytes(STATE.reservation_safety_headroom_bytes)}, "
            f"max_total_size={format_bytes(max_total_size_bytes)}"
        )

    files = sorted(
        get_managed_snapshot_files(upload, snapshot),
        key=remote_file_oldest_sort_key,
    )
    current_size = sum(file.size for file in files)
    required_free_bytes = max(
        0,
        current_size
        + reserved_upload_bytes
        + STATE.reservation_safety_headroom_bytes
        - max_total_size_bytes,
    )
    selected_free_bytes = 0

    for file in files:
        if selected_free_bytes >= required_free_bytes:
            break
        if add_planned_deletion(
            plan,
            snapshot,
            file,
            delete_to_trash=upload.delete_to_trash,
            reason="upload_reservation",
        ):
            selected_free_bytes += file.size

    if selected_free_bytes < required_free_bytes:
        raise ValueError(
            "Reservation could not select enough complete managed files to free the "
            "calculated byte deficit. "
            f"Required={format_bytes(required_free_bytes)}, "
            f"selected={format_bytes(selected_free_bytes)}"
        )

    current_size_after = current_size - selected_free_bytes
    return {
        "current_size": current_size,
        "required_free_bytes": required_free_bytes,
        "selected_free_bytes": selected_free_bytes,
        "reserved_upload_bytes": reserved_upload_bytes,
        "projected_temporary_size": current_size_after + reserved_upload_bytes,
        "max_total_size_bytes": max_total_size_bytes,
    }


def build_pre_upload_plan(
    upload: UploadDirectory,
    targets: list[CleanupTarget],
    snapshot: RemoteSnapshot,
    local_upload_bytes: int,
) -> tuple[RemoteDeletePlan, RemoteSnapshot, dict[str, int]]:
    """Plan pre-cleanup and reservation from one recursive remote snapshot."""
    from .remote_files import clone_remote_snapshot

    working = clone_remote_snapshot(snapshot)
    plan = RemoteDeletePlan(upload.remote_path, "PRE-UPLOAD")
    plan_cleanup_targets(upload, targets, working, plan)
    reservation = plan_upload_reservation(
        upload,
        working,
        plan,
        local_upload_bytes,
    )
    return plan, working, reservation


def build_post_upload_plan(
    upload: UploadDirectory,
    targets: list[CleanupTarget],
    snapshot: RemoteSnapshot,
) -> tuple[RemoteDeletePlan, RemoteSnapshot]:
    """Plan post-upload cleanup and max_total_size from one remote snapshot."""
    from .remote_files import clone_remote_snapshot

    working = clone_remote_snapshot(snapshot)
    plan = RemoteDeletePlan(upload.remote_path, "POST-UPLOAD")
    plan_cleanup_targets(upload, targets, working, plan)
    plan_remote_quota_cleanup(upload, working, plan)
    return plan, working
