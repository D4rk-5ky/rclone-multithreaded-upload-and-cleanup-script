"""Build effective cleanup targets from upload-owned cleanup rules."""

from .models import CleanupTarget
from .state import STATE
from .utils import join_rclone_remote_path


def build_cleanup_directories() -> list[CleanupTarget]:
    """Build cleanup targets from each upload destination's own cleanup_rules."""
    cleanup_directories: list[CleanupTarget] = []

    for upload in STATE.upload_directories:
        for directory in upload.cleanup_rules:
            cleanup_directories.append(
                CleanupTarget(
                    path=join_rclone_remote_path(upload.remote_path, directory.path),
                    max_files=directory.max_files,
                    max_size=directory.max_size,
                    delete_old_files=(
                        upload.delete_old_files
                        if directory.delete_old_files is None
                        else directory.delete_old_files
                    ),
                    delete_excess_files=(
                        upload.delete_excess_files
                        if directory.delete_excess_files is None
                        else directory.delete_excess_files
                    ),
                    delete_to_trash=(
                        upload.delete_to_trash
                        if directory.delete_to_trash is None
                        else directory.delete_to_trash
                    ),
                    owner_remote_path=upload.remote_path,
                )
            )

    return cleanup_directories
