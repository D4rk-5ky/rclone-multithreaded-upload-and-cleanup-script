"""Remote recursive listing and in-memory snapshot helpers."""

import json

from .commands import is_directory_not_found, run_command
from .models import CleanupTarget, RemoteFile, RemoteQuotaFile, RemoteSnapshot, UploadDirectory
from .output import print_job_block
from .utils import normalize_relative_path, parse_rclone_modtime


def get_remote_file_entries(remote_path: str) -> list[RemoteFile]:
    """Read one recursive rclone lsjson array and validate Path/Size/ModTime."""
    command = [
        "rclone",
        "lsjson",
        "--recursive",
        "--files-only",
        "--no-mimetype",
        remote_path,
    ]
    result = run_command(command, capture_output=True)

    if result.returncode != 0:
        if is_directory_not_found(result):
            print_job_block(
                "REMOTE SNAPSHOT",
                0,
                remote_path,
                "Remote folder does not exist yet, treating it as empty",
            )
            return []
        detail = (result.stderr or result.stdout or "unknown rclone lsjson error").strip()
        raise RuntimeError(
            f"rclone lsjson failed for {remote_path}. "
            f"Return code: {result.returncode}. {detail}"
        )

    try:
        raw_entries = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"rclone lsjson returned invalid JSON for {remote_path}: {error}"
        ) from error

    if not isinstance(raw_entries, list):
        raise RuntimeError(f"rclone lsjson did not return a JSON array for {remote_path}")

    files: list[RemoteFile] = []
    for index, item in enumerate(raw_entries, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(
                f"rclone lsjson entry {index} for {remote_path} is not an object"
            )
        if item.get("IsDir") is True:
            raise RuntimeError(
                f"rclone lsjson entry {index} for {remote_path} is a directory despite --files-only"
            )

        path = item.get("Path")
        size = item.get("Size")
        modified = item.get("ModTime")
        if not isinstance(path, str) or not path:
            raise RuntimeError(
                f"rclone lsjson entry {index} for {remote_path} has invalid Path"
            )
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise RuntimeError(
                f"rclone lsjson entry {index} for {remote_path} has invalid Size"
            )
        if not isinstance(modified, str) or not modified:
            raise RuntimeError(
                f"rclone lsjson entry {index} for {remote_path} has invalid ModTime"
            )

        parse_rclone_modtime(modified)
        files.append(
            RemoteFile(
                path=normalize_relative_path(path),
                size=size,
                modified=modified,
            )
        )

    return files


def fetch_remote_snapshot(remote_path: str) -> RemoteSnapshot:
    """Fetch the single recursive listing used by one planning/verification phase."""
    files = get_remote_file_entries(remote_path)
    return RemoteSnapshot(
        remote_path=remote_path,
        files_by_path={file.path: file for file in files},
    )


def clone_remote_snapshot(snapshot: RemoteSnapshot) -> RemoteSnapshot:
    """Create a working copy that planners may mutate without changing the source."""
    return RemoteSnapshot(snapshot.remote_path, dict(snapshot.files_by_path))


def relative_cleanup_target_path(upload: UploadDirectory, target: CleanupTarget) -> str:
    """Return target.path relative to upload.remote_path."""
    root = upload.remote_path.rstrip("/")
    full_target = target.path.rstrip("/")
    if full_target == root:
        return ""
    prefix = f"{root}/"
    if not full_target.startswith(prefix):
        raise ValueError(
            f"Cleanup target {target.path} is not below owner remote {upload.remote_path}"
        )
    return normalize_relative_path(full_target[len(prefix):])


def files_below_relative_path(
    snapshot: RemoteSnapshot,
    relative_path: str,
) -> list[RemoteFile]:
    """Return current snapshot files at or below one relative rule path."""
    rule_path = normalize_relative_path(relative_path)
    if not rule_path:
        return list(snapshot.files_by_path.values())
    prefix = f"{rule_path}/"
    return [
        file
        for file in snapshot.files_by_path.values()
        if file.path == rule_path or file.path.startswith(prefix)
    ]


def get_managed_snapshot_files(
    upload: UploadDirectory,
    snapshot: RemoteSnapshot,
) -> list[RemoteFile]:
    """Return the deduplicated union of files covered by upload.cleanup_rules."""
    managed_paths = [normalize_relative_path(rule.path) for rule in upload.cleanup_rules]
    if not managed_paths:
        return []

    files: list[RemoteFile] = []
    for file in snapshot.files_by_path.values():
        for rule_path in managed_paths:
            if not rule_path or file.path == rule_path or file.path.startswith(f"{rule_path}/"):
                files.append(file)
                break
    return files


def get_upload_remote_quota_entries(upload: UploadDirectory) -> list[RemoteQuotaFile]:
    """Compatibility helper: one live root snapshot followed by managed filtering."""
    snapshot = fetch_remote_snapshot(upload.remote_path)
    managed_rule_paths = [normalize_relative_path(rule.path) for rule in upload.cleanup_rules]
    entries: list[RemoteQuotaFile] = []
    for file in get_managed_snapshot_files(upload, snapshot):
        source_folder = "/"
        for rule_path in managed_rule_paths:
            if not rule_path:
                source_folder = "/"
                break
            if file.path == rule_path or file.path.startswith(f"{rule_path}/"):
                source_folder = rule_path
                break
        entries.append(
            RemoteQuotaFile(file.path, file.size, file.modified, source_folder)
        )
    return entries
