"""Shared data models for rclone-multithreaded-upload."""

from dataclasses import dataclass, field


@dataclass
class DirectoryCleanupRule:
    """One cleanup rule owned by one UploadDirectory."""

    path: str
    max_files: int | None = None
    max_size: str | None = None
    delete_old_files: bool | None = None
    delete_excess_files: bool | None = None
    delete_to_trash: bool | None = None


@dataclass
class UploadDirectory:
    """One local source and one rclone destination."""

    local_path: str
    remote_path: str
    copy_options: list[str]
    cleanup_rules: list[DirectoryCleanupRule]
    name: str | None = None
    buffer_size: str | None = None
    upload_command: str = "copy"
    delete_old_files: bool = True
    delete_excess_files: bool = True
    max_total_size: str | None = None
    delete_to_trash: bool = False
    empty_trash: bool = True


@dataclass
class CleanupTarget:
    """A generated full remote cleanup target."""

    path: str
    max_files: int | None = None
    max_size: str | None = None
    delete_old_files: bool = True
    delete_excess_files: bool = True
    delete_to_trash: bool = False
    owner_remote_path: str = ""


@dataclass(frozen=True)
class RemoteFile:
    """One file returned by the recursive remote lsjson snapshot."""

    path: str
    size: int
    modified: str


@dataclass(frozen=True)
class RemoteQuotaFile:
    """Compatibility model for a managed file relative to one upload root."""

    path: str
    size: int
    modified: str
    source_folder: str


@dataclass
class RemoteSnapshot:
    """One in-memory recursive file snapshot for an upload remote root."""

    remote_path: str
    files_by_path: dict[str, RemoteFile] = field(default_factory=dict)


@dataclass
class PlannedDeletion:
    """One file selected for deletion from a working snapshot."""

    file: RemoteFile
    delete_to_trash: bool
    reason: str


@dataclass
class RemoteDeletePlan:
    """Combined deletion plan for one remote and one snapshot phase."""

    remote_path: str
    phase_name: str
    entries: dict[str, PlannedDeletion] = field(default_factory=dict)


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
