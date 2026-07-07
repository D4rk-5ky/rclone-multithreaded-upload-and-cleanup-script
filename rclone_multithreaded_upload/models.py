"""Shared data models for rclone-multithreaded-upload.

These dataclasses are intentionally free of rclone execution and workflow
orchestration so the application modules can share the same data structures.
"""

from dataclasses import dataclass, field


@dataclass
class DirectoryCleanupRule:
    """
    One cleanup rule owned by one UploadDirectory.

    path is relative to the owning upload.remote_path. A path of "/" means the
    root of that remote_path.

    Optional boolean values override the owning upload defaults. None means
    inherit the upload-level setting.
    """
    path: str
    max_files: int | None = None
    max_size: str | None = None
    delete_old_files: bool | None = None
    delete_excess_files: bool | None = None
    delete_to_trash: bool | None = None


@dataclass
class UploadDirectory:
    """
    One local source and one rclone destination.
    """
    local_path: str
    remote_path: str
    copy_options: list[str]
    cleanup_rules: list[DirectoryCleanupRule]

    # Optional short name used in the final per-remote result summary.
    # When omitted, remote_path is used.
    name: str | None = None

    # Optional rclone per-transfer buffer size for this destination.
    # Examples: "16M", "64M", "128M". None disables the explicit option.
    buffer_size: str | None = None

    upload_command: str = "copy"
    delete_old_files: bool = True
    delete_excess_files: bool = True
    max_total_size: str | None = None
    delete_to_trash: bool = False
    empty_trash: bool = True


@dataclass
class CleanupTarget:
    """
    A generated full remote cleanup target.
    """
    path: str
    max_files: int | None = None
    max_size: str | None = None
    delete_old_files: bool = True
    delete_excess_files: bool = True
    delete_to_trash: bool = False

    # Owning upload remote. Used only for per-remote result accounting.
    owner_remote_path: str = ""


@dataclass
class RemoteFile:
    path: str
    size: int
    modified: str


@dataclass
class RemoteQuotaFile:
    """
    File relative to UploadDirectory.remote_path.
    """
    path: str
    size: int
    modified: str
    source_folder: str


@dataclass
class StageRunResult:
    """One final-summary stage for one upload remote."""
    status: str = "PENDING"
    errors: list[str] = field(default_factory=list)


@dataclass
class RemoteRunResult:
    """Per-remote state retained until FINAL RUN RESULT is printed."""
    name: str
    remote_path: str
    reservation: StageRunResult = field(default_factory=StageRunResult)
    upload: StageRunResult = field(default_factory=StageRunResult)
    post_cleanup: StageRunResult = field(default_factory=StageRunResult)
    final_quota: StageRunResult = field(default_factory=StageRunResult)
