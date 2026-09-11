"""Shared data models for rclone-multithreaded-upload."""

from dataclasses import dataclass, field


@dataclass
class MqttConfig:
    """Optional MQTT result-publishing configuration."""

    enabled: bool = False
    host: str | None = None
    port: int = 1883
    topic: str = "rclone-multithreaded-upload/result"
    username: str | None = None
    password: str | None = None
    client_id: str | None = None
    qos: int = 1
    retain: bool = False
    keepalive: int = 60
    tls: bool = False
    tls_insecure: bool = False
    ca_certs: str | None = None
    publish_timeout: int = 10


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
class LocalUploadFile:
    """One filtered local source file returned by rclone lsjson."""

    path: str
    size: int
    modified: str


@dataclass(frozen=True)
class LocalUploadSnapshot:
    """Exact filtered local candidate set used for reservation and upload."""

    files: tuple[LocalUploadFile, ...]
    total_bytes: int

    @property
    def file_count(self) -> int:
        return len(self.files)


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
    reservation_trash_deleted: bool = False
    upload_trash_mode_attempted: bool = False
    post_cleanup_trash_deleted: bool = False
