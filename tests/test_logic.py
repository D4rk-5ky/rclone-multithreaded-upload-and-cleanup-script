"""Non-destructive regression tests for the modular v0.0.18 layout."""

from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from rclone_multithreaded_upload.config import load_config
from rclone_multithreaded_upload.models import (
    CleanupTarget,
    DirectoryCleanupRule,
    RemoteFile,
    RemoteQuotaFile,
    UploadDirectory,
)
from rclone_multithreaded_upload.remote_files import (
    get_upload_remote_quota_entries,
    make_delete_list,
)
from rclone_multithreaded_upload.reservation import (
    get_size_filter_options,
    make_upload_reservation_delete_list,
)
from rclone_multithreaded_upload.results import initialize_run_results
from rclone_multithreaded_upload.state import STATE
from rclone_multithreaded_upload.utils import parse_size_to_bytes


class StateSnapshot:
    def __enter__(self):
        self.values = {
            "upload_directories": STATE.upload_directories,
            "config_path": STATE.config_path,
            "delete_min_age": STATE.delete_min_age,
            "upload_threads": STATE.upload_threads,
            "cleanup_threads": STATE.cleanup_threads,
            "remote_quota_cleanup_threads": STATE.remote_quota_cleanup_threads,
            "trash_cleanup_threads": STATE.trash_cleanup_threads,
            "lock_file": STATE.lock_file,
            "delete_list_dir": STATE.delete_list_dir,
            "sleep_after_step": STATE.sleep_after_step,
            "reservation_safety_headroom_bytes": STATE.reservation_safety_headroom_bytes,
            "max_reservation_cleanup_passes": STATE.max_reservation_cleanup_passes,
            "lock_created": STATE.lock_created,
            "reserved_upload_bytes": dict(STATE.reserved_upload_bytes),
            "run_results": dict(STATE.run_results),
        }
        return self

    def __exit__(self, exc_type, exc, tb):
        for key, value in self.values.items():
            setattr(STATE, key, value)


class LogicTests(unittest.TestCase):
    def test_parse_size_to_bytes_preserves_binary_units(self):
        self.assertEqual(parse_size_to_bytes("1K"), 1024)
        self.assertEqual(parse_size_to_bytes("1.5M"), int(1.5 * 1024**2))
        self.assertEqual(parse_size_to_bytes("12G"), 12 * 1024**3)
        self.assertEqual(parse_size_to_bytes("1TB"), 1024**4)

    def test_size_filter_options_only_copy_source_selection_filters(self):
        upload = UploadDirectory(
            local_path="/tmp/source",
            remote_path="remote:path",
            copy_options=[
                "--max-age",
                "12h",
                "--stats",
                "10s",
                "--stats-one-line",
                "--transfers",
                "4",
                "--exclude",
                "/Home/OldCamera/**",
                "--ignore-case",
                "--include=*.mp4",
            ],
            cleanup_rules=[],
        )
        self.assertEqual(
            get_size_filter_options(upload),
            [
                "--max-age",
                "12h",
                "--exclude",
                "/Home/OldCamera/**",
                "--ignore-case",
                "--include=*.mp4",
            ],
        )

    def test_managed_remote_listing_uses_one_root_listing_and_deduplicates(self):
        upload = UploadDirectory(
            local_path="/tmp/source",
            remote_path="remote:root",
            copy_options=[],
            cleanup_rules=[
                DirectoryCleanupRule(path="CameraA"),
                DirectoryCleanupRule(path="/"),
            ],
        )
        files = [
            RemoteFile("CameraA/a.mp4", 100, "2026-07-01T00:00:00Z"),
            RemoteFile("CameraB/b.mp4", 200, "2026-07-02T00:00:00Z"),
        ]
        calls = []

        def fake_listing(remote_path):
            calls.append(remote_path)
            return files

        with patch(
            "rclone_multithreaded_upload.remote_files.get_remote_file_entries",
            side_effect=fake_listing,
        ):
            managed = get_upload_remote_quota_entries(upload)

        self.assertEqual(calls, ["remote:root"])
        self.assertEqual({item.path for item in managed}, {"CameraA/a.mp4", "CameraB/b.mp4"})
        self.assertEqual(len(managed), 2)
        self.assertEqual(
            {item.path: item.source_folder for item in managed},
            {"CameraA/a.mp4": "CameraA", "CameraB/b.mp4": "/"},
        )

    def test_cleanup_delete_list_selects_oldest_complete_files_until_limits_pass(self):
        target = CleanupTarget(
            path="remote:root",
            max_files=2,
            max_size="250B",
            delete_excess_files=True,
        )
        files = [
            RemoteFile("new.mp4", 100, "2026-07-03T00:00:00Z"),
            RemoteFile("old.mp4", 100, "2026-07-01T00:00:00Z"),
            RemoteFile("middle.mp4", 100, "2026-07-02T00:00:00Z"),
        ]

        with StateSnapshot(), tempfile.TemporaryDirectory() as temp_dir:
            STATE.delete_list_dir = Path(temp_dir)
            with patch(
                "rclone_multithreaded_upload.remote_files.get_remote_file_entries",
                return_value=files,
            ):
                delete_list = make_delete_list(target)
            self.assertEqual(delete_list.read_text(encoding="utf-8"), "old.mp4\n")

    def test_reservation_delete_list_uses_exact_deficit_and_complete_files(self):
        upload = UploadDirectory(
            local_path="/tmp/source",
            remote_path="remote:root",
            copy_options=[],
            cleanup_rules=[DirectoryCleanupRule(path="/")],
            max_total_size="1000B",
        )
        managed = [
            RemoteQuotaFile("old-a", 75, "2026-07-01T00:00:00Z", ""),
            RemoteQuotaFile("old-b", 50, "2026-07-02T00:00:00Z", ""),
            RemoteQuotaFile("new", 675, "2026-07-03T00:00:00Z", ""),
        ]

        with StateSnapshot(), tempfile.TemporaryDirectory() as temp_dir:
            STATE.delete_list_dir = Path(temp_dir)
            STATE.reservation_safety_headroom_bytes = 10
            with patch(
                "rclone_multithreaded_upload.reservation.get_upload_remote_quota_entries",
                return_value=managed,
            ):
                path, current, required, selected = make_upload_reservation_delete_list(
                    upload,
                    local_upload_bytes=300,
                )

            # 800 current + 301 transfer cap + 10 headroom - 1000 = 111 bytes.
            self.assertEqual(current, 800)
            self.assertEqual(required, 111)
            self.assertEqual(selected, 125)
            self.assertEqual(path.read_text(encoding="utf-8"), "old-a\nold-b\n")

    def test_upload_command_keeps_buffer_filters_and_reserved_transfer_cap(self):
        from rclone_multithreaded_upload import upload as upload_module

        with StateSnapshot(), tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            remote_path = "remote:root"
            upload = UploadDirectory(
                local_path=str(source),
                remote_path=remote_path,
                copy_options=["--max-age", "12h", "--transfers", "4"],
                cleanup_rules=[DirectoryCleanupRule(path="/")],
                buffer_size="64M",
                max_total_size="12G",
            )
            STATE.upload_directories = [upload]
            STATE.reserved_upload_bytes = {remote_path: 1234}
            initialize_run_results()
            captured = []

            def fake_stream(command, thread_number, remote_path):
                captured.append((command, thread_number, remote_path))
                return 0, ""

            with patch.object(upload_module, "run_command_streamed", side_effect=fake_stream):
                self.assertTrue(upload_module.upload_one_directory(2, upload))

            command = captured[0][0]
            self.assertEqual(command[:4], ["rclone", "copy", str(source), remote_path])
            self.assertIn("--max-age", command)
            self.assertIn("--buffer-size", command)
            self.assertIn("64M", command)
            cap_index = command.index("--max-transfer")
            self.assertEqual(command[cap_index + 1], "1235B")
            self.assertEqual(command[cap_index + 2 : cap_index + 4], ["--cutoff-mode", "CAUTIOUS"])

    def test_independent_remote_pipeline_has_no_global_reservation_barrier(self):
        from rclone_multithreaded_upload import phases

        fast = UploadDirectory("/tmp", "fast:root", [], [])
        slow = UploadDirectory("/tmp", "slow:root", [], [])
        slow_reservation_finished = threading.Event()
        fast_upload_started_before_slow_finished = []

        def fake_reserve(job_number, upload):
            if upload.remote_path == "slow:root":
                time.sleep(0.20)
                slow_reservation_finished.set()
            return True

        def fake_trash(job_number, upload, phase_name=""):
            return True

        def fake_upload(job_number, upload):
            if upload.remote_path == "fast:root":
                fast_upload_started_before_slow_finished.append(
                    not slow_reservation_finished.is_set()
                )
            return True

        with StateSnapshot():
            STATE.upload_directories = [fast, slow]
            STATE.upload_threads = 2
            STATE.sleep_after_step = 0
            initialize_run_results()
            with (
                patch.object(phases, "reserve_one_upload_remote_space", side_effect=fake_reserve),
                patch.object(phases, "cleanup_one_trash_remote", side_effect=fake_trash),
                patch.object(phases, "upload_one_directory", side_effect=fake_upload),
            ):
                self.assertTrue(phases.run_reservation_and_upload_phase())

        self.assertEqual(fast_upload_started_before_slow_finished, [True])

    def test_production_config_loads_expected_runtime_state(self):
        project_root = Path(__file__).resolve().parents[1]
        with StateSnapshot():
            load_config(str(project_root / "config.json"))
            self.assertEqual(STATE.upload_threads, 4)
            self.assertEqual(STATE.cleanup_threads, 4)
            self.assertEqual(STATE.remote_quota_cleanup_threads, 4)
            self.assertEqual(STATE.trash_cleanup_threads, 4)
            self.assertEqual(
                [upload.name for upload in STATE.upload_directories],
                ["GDrive", "Mega", "OneDrive"],
            )
            self.assertEqual(
                [upload.max_total_size for upload in STATE.upload_directories],
                ["12G", "12G", "50G"],
            )


if __name__ == "__main__":
    unittest.main()
