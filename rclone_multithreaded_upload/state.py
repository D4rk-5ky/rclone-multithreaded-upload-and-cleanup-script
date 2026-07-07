"""Mutable runtime state shared by the application modules.

The original single-file implementation updated module globals in load_config().
A single state object keeps those values live across imported modules and avoids
stale copies of integers and Path objects after configuration loading.
"""

from dataclasses import dataclass, field
from pathlib import Path
import threading

from .models import RemoteRunResult, UploadDirectory


@dataclass
class RuntimeState:
    upload_directories: list[UploadDirectory] = field(default_factory=list)
    config_path: Path | None = None
    delete_min_age: str = "31d"
    upload_threads: int = 2
    cleanup_threads: int = 2
    remote_quota_cleanup_threads: int = 2
    trash_cleanup_threads: int = 2
    lock_file: Path = Path("/var/lock/subsys/RcloneLockFile.run")
    delete_list_dir: Path = Path("/root/rclone")
    sleep_after_step: int = 5
    reservation_safety_headroom_bytes: int = 1 * 1024**2
    lock_created: bool = False
    reserved_upload_bytes: dict[str, int] = field(default_factory=dict)
    reserved_upload_bytes_lock: threading.Lock = field(
        default_factory=threading.Lock,
        repr=False,
    )
    run_results: dict[str, RemoteRunResult] = field(default_factory=dict)
    run_results_lock: threading.Lock = field(
        default_factory=threading.Lock,
        repr=False,
    )


STATE = RuntimeState()
