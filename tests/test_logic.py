"""Non-destructive regression tests for the snapshot planner and stage barriers."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from rclone_multithreaded_upload.config import load_config
from rclone_multithreaded_upload.delete_plan import execute_delete_plan
from rclone_multithreaded_upload.models import (
    CleanupTarget,
    DirectoryCleanupRule,
    RemoteDeletePlan,
    RemoteFile,
    PlannedDeletion,
    RemoteSnapshot,
    UploadDirectory,
)
from rclone_multithreaded_upload.planning import build_pre_upload_plan
from rclone_multithreaded_upload.remote_files import get_managed_snapshot_files
from rclone_multithreaded_upload.reservation import (
    clear_local_size_cache,
    get_filtered_local_upload_size,
    get_size_filter_options,
)
from rclone_multithreaded_upload.results import initialize_run_results
from rclone_multithreaded_upload.state import STATE
from rclone_multithreaded_upload.utils import parse_duration_to_timedelta, parse_size_to_bytes


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
            "lock_created": STATE.lock_created,
            "reserved_upload_bytes": dict(STATE.reserved_upload_bytes),
            "run_results": dict(STATE.run_results),
        }
        return self

    def __exit__(self, exc_type, exc, tb):
        clear_local_size_cache()
        for key, value in self.values.items():
            setattr(STATE, key, value)


class LogicTests(unittest.TestCase):
    def test_parse_size_and_duration_helpers(self):
        self.assertEqual(parse_size_to_bytes("1K"), 1024)
        self.assertEqual(parse_size_to_bytes("1.5M"), int(1.5 * 1024**2))
        self.assertEqual(parse_size_to_bytes("12G"), 12 * 1024**3)
        self.assertEqual(parse_duration_to_timedelta("31d").days, 31)
        self.assertEqual(parse_duration_to_timedelta("2h45m").total_seconds(), 9900)
        self.assertEqual(parse_duration_to_timedelta("1M").days, 30)
        with self.assertRaises(ValueError):
            parse_duration_to_timedelta("1d12h")

    def test_size_filter_options_only_copy_source_selection_filters(self):
        upload = UploadDirectory(
            local_path="/tmp/source",
            remote_path="remote:path",
            copy_options=[
                "--max-age", "12h", "--stats", "10s", "--stats-one-line",
                "--transfers", "4", "--exclude", "/Home/OldCamera/**",
                "--ignore-case", "--include=*.mp4",
            ],
            cleanup_rules=[],
        )
        self.assertEqual(
            get_size_filter_options(upload),
            [
                "--max-age", "12h", "--exclude", "/Home/OldCamera/**",
                "--ignore-case", "--include=*.mp4",
            ],
        )

    def test_managed_snapshot_union_deduplicates_overlapping_rules(self):
        upload = UploadDirectory(
            local_path="/tmp/source",
            remote_path="remote:root",
            copy_options=[],
            cleanup_rules=[
                DirectoryCleanupRule(path="CameraA"),
                DirectoryCleanupRule(path="/"),
            ],
        )
        snapshot = RemoteSnapshot(
            "remote:root",
            {
                "CameraA/a.mp4": RemoteFile("CameraA/a.mp4", 100, "2026-07-01T00:00:00Z"),
                "CameraB/b.mp4": RemoteFile("CameraB/b.mp4", 200, "2026-07-02T00:00:00Z"),
            },
        )
        managed = get_managed_snapshot_files(upload, snapshot)
        self.assertEqual({file.path for file in managed}, {"CameraA/a.mp4", "CameraB/b.mp4"})
        self.assertEqual(len(managed), 2)

    def test_pre_plan_reuses_one_snapshot_for_age_limits_and_reservation(self):
        upload = UploadDirectory(
            local_path="/tmp/source",
            remote_path="remote:root",
            copy_options=[],
            cleanup_rules=[DirectoryCleanupRule(path="/")],
            max_total_size="1000B",
        )
        target = CleanupTarget(
            path="remote:root/",
            max_size="700B",
            delete_old_files=True,
            delete_excess_files=True,
            owner_remote_path="remote:root",
        )
        snapshot = RemoteSnapshot(
            "remote:root",
            {
                "age-old": RemoteFile("age-old", 100, "2026-01-01T00:00:00Z"),
                "limit-old": RemoteFile("limit-old", 300, "2026-06-10T00:00:00Z"),
                "reserve-old": RemoteFile("reserve-old", 300, "2026-06-11T00:00:00Z"),
                "new": RemoteFile("new", 300, "2026-07-06T00:00:00Z"),
            },
        )
        with StateSnapshot():
            STATE.delete_min_age = "31d"
            STATE.reservation_safety_headroom_bytes = 10
            with patch(
                "rclone_multithreaded_upload.planning.datetime"
            ) as fake_datetime:
                fake_datetime.now.return_value = datetime(2026, 7, 7, tzinfo=timezone.utc)
                plan, working, reservation = build_pre_upload_plan(
                    upload, [target], snapshot, local_upload_bytes=500
                )

        self.assertEqual(
            set(plan.entries),
            {"age-old", "limit-old", "reserve-old"},
        )
        self.assertEqual(set(working.files_by_path), {"new"})
        self.assertEqual(reservation["current_size"], 600)
        self.assertEqual(reservation["required_free_bytes"], 111)
        self.assertEqual(reservation["selected_free_bytes"], 300)
        self.assertEqual(reservation["projected_temporary_size"], 801)

    def test_local_size_single_flight_scans_identical_source_once(self):
        with StateSnapshot(), tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            (source / "a.bin").write_bytes(b"x")
            uploads = [
                UploadDirectory(str(source), f"r{i}:root", ["--max-age", "12h"], [])
                for i in range(3)
            ]
            STATE.upload_directories = uploads
            initialize_run_results()
            calls = []

            def fake_calculate(upload):
                calls.append(upload.remote_path)
                time.sleep(0.10)
                return 123, 4

            clear_local_size_cache()
            with patch(
                "rclone_multithreaded_upload.reservation._calculate_filtered_local_upload_size",
                side_effect=fake_calculate,
            ):
                with ThreadPoolExecutor(max_workers=3) as executor:
                    results = list(executor.map(
                        lambda item: get_filtered_local_upload_size(item[0], item[1]),
                        enumerate(uploads, start=1),
                    ))

            self.assertEqual(results, [(123, 4), (123, 4), (123, 4)])
            self.assertEqual(len(calls), 1)

    def test_combined_delete_plan_uses_one_delete_for_one_mode(self):
        upload = UploadDirectory("/tmp", "remote:root", [], [], delete_to_trash=False)
        plan = RemoteDeletePlan("remote:root", "PRE-UPLOAD")
        plan.entries = {
            "a": PlannedDeletion(
                RemoteFile("a", 10, "2026-01-01T00:00:00Z"), False, "age"
            ),
            "b": PlannedDeletion(
                RemoteFile("b", 20, "2026-01-02T00:00:00Z"), False, "reservation"
            ),
        }
        completed = subprocess.CompletedProcess([], 0, "", "")
        with StateSnapshot(), tempfile.TemporaryDirectory() as temp_dir:
            STATE.delete_list_dir = Path(temp_dir)
            initialize_run_results()
            STATE.upload_directories = [upload]
            initialize_run_results()
            with (
                patch("rclone_multithreaded_upload.delete_plan.run_command", return_value=completed) as run,
                patch("rclone_multithreaded_upload.delete_plan.get_delete_mode_options", return_value=[]),
            ):
                self.assertTrue(execute_delete_plan(1, upload, plan, "reservation"))
            self.assertEqual(run.call_count, 1)
            command = run.call_args.args[0]
            delete_list = Path(command[command.index("--files-from") + 1])
            self.assertEqual(delete_list.read_text(encoding="utf-8"), "a\nb\n")

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
            self.assertIn("--buffer-size", command)
            cap_index = command.index("--max-transfer")
            self.assertEqual(command[cap_index + 1], "1235B")
            self.assertEqual(command[cap_index + 2 : cap_index + 4], ["--cutoff-mode", "CAUTIOUS"])

    def test_stage_barriers_wait_for_all_preparation_and_trash_jobs(self):
        from rclone_multithreaded_upload import phases

        fast = UploadDirectory("/tmp", "fast:root", [], [])
        slow = UploadDirectory("/tmp", "slow:root", [], [])
        slow_preparation_finished = threading.Event()
        slow_trash_finished = threading.Event()
        fast_trash_saw_preparation_barrier = []
        fast_upload_saw_trash_barrier = []

        def fake_snapshot(remote_path):
            if remote_path == "slow:root":
                time.sleep(0.20)
                slow_preparation_finished.set()
            return RemoteSnapshot(remote_path, {})

        def fake_trash(job_number, upload, phase_name=""):
            del job_number, phase_name
            if upload.remote_path == "fast:root":
                fast_trash_saw_preparation_barrier.append(
                    slow_preparation_finished.is_set()
                )
            if upload.remote_path == "slow:root":
                time.sleep(0.20)
                slow_trash_finished.set()
            return True

        def fake_upload(job_number, upload):
            del job_number
            if upload.remote_path == "fast:root":
                fast_upload_saw_trash_barrier.append(slow_trash_finished.is_set())
            return True

        with StateSnapshot():
            STATE.upload_directories = [fast, slow]
            STATE.upload_threads = 2
            STATE.cleanup_threads = 2
            STATE.remote_quota_cleanup_threads = 2
            STATE.trash_cleanup_threads = 2
            STATE.sleep_after_step = 0
            initialize_run_results()
            with (
                patch.object(phases, "fetch_remote_snapshot", side_effect=fake_snapshot),
                patch.object(phases, "execute_delete_plan", return_value=True),
                patch.object(phases, "cleanup_one_trash_remote", side_effect=fake_trash),
                patch.object(phases, "upload_one_directory", side_effect=fake_upload),
            ):
                self.assertTrue(phases.run_reservation_and_upload_phase([]))

        self.assertEqual(fast_trash_saw_preparation_barrier, [True])
        self.assertEqual(fast_upload_saw_trash_barrier, [True])

    def test_failed_preparation_does_not_cancel_other_uploads(self):
        from rclone_multithreaded_upload import phases

        uploads = [
            UploadDirectory("/tmp", "good-a:root", [], []),
            UploadDirectory("/tmp", "broken:root", [], []),
            UploadDirectory("/tmp", "good-b:root", [], []),
        ]
        uploaded = []

        def fake_snapshot(remote_path):
            if remote_path == "broken:root":
                raise RuntimeError("simulated remote listing failure")
            return RemoteSnapshot(remote_path, {})

        def fake_upload(job_number, upload):
            del job_number
            uploaded.append(upload.remote_path)
            return True

        with StateSnapshot():
            STATE.upload_directories = uploads
            STATE.upload_threads = 3
            STATE.cleanup_threads = 3
            STATE.remote_quota_cleanup_threads = 3
            STATE.trash_cleanup_threads = 3
            STATE.sleep_after_step = 0
            initialize_run_results()
            with (
                patch.object(phases, "fetch_remote_snapshot", side_effect=fake_snapshot),
                patch.object(phases, "execute_delete_plan", return_value=True),
                patch.object(phases, "cleanup_one_trash_remote", return_value=True),
                patch.object(phases, "upload_one_directory", side_effect=fake_upload),
            ):
                self.assertFalse(phases.run_reservation_and_upload_phase([]))

            self.assertEqual(set(uploaded), {"good-a:root", "good-b:root"})
            self.assertEqual(STATE.run_results["broken:root"].reservation.status, "FAILED")
            self.assertEqual(STATE.run_results["broken:root"].upload.status, "SKIPPED")

    def test_upload_failure_does_not_cancel_other_upload_workers(self):
        from rclone_multithreaded_upload import phases

        uploads = [
            UploadDirectory("/tmp", "good-a:root", [], []),
            UploadDirectory("/tmp", "broken-upload:root", [], []),
            UploadDirectory("/tmp", "good-b:root", [], []),
        ]
        upload_calls = []

        def fake_upload(job_number, upload):
            del job_number
            upload_calls.append(upload.remote_path)
            if upload.remote_path == "broken-upload:root":
                from rclone_multithreaded_upload.results import record_stage_failure
                record_stage_failure(upload.remote_path, "upload", "simulated upload failure")
                return False
            from rclone_multithreaded_upload.results import record_stage_success
            record_stage_success(upload.remote_path, "upload")
            return True

        with StateSnapshot():
            STATE.upload_directories = uploads
            STATE.upload_threads = 3
            STATE.cleanup_threads = 3
            STATE.remote_quota_cleanup_threads = 3
            STATE.trash_cleanup_threads = 3
            STATE.sleep_after_step = 0
            initialize_run_results()
            with (
                patch.object(phases, "fetch_remote_snapshot", side_effect=lambda remote: RemoteSnapshot(remote, {})),
                patch.object(phases, "execute_delete_plan", return_value=True),
                patch.object(phases, "cleanup_one_trash_remote", return_value=True),
                patch.object(phases, "upload_one_directory", side_effect=fake_upload),
            ):
                self.assertFalse(phases.run_reservation_and_upload_phase([]))

            self.assertEqual(set(upload_calls), {upload.remote_path for upload in uploads})
            self.assertEqual(STATE.run_results["good-a:root"].upload.status, "SUCCESS")
            self.assertEqual(STATE.run_results["broken-upload:root"].upload.status, "FAILED")
            self.assertEqual(STATE.run_results["good-b:root"].upload.status, "SUCCESS")

    def test_post_cleanup_barrier_waits_before_trash_cleanup(self):
        from rclone_multithreaded_upload import phases

        fast = UploadDirectory("/tmp", "fast:root", [], [])
        slow = UploadDirectory("/tmp", "slow:root", [], [])
        slow_cleanup_finished = threading.Event()
        fast_trash_saw_cleanup_barrier = []

        def fake_snapshot(remote_path):
            if remote_path == "slow:root":
                time.sleep(0.20)
                slow_cleanup_finished.set()
            return RemoteSnapshot(remote_path, {})

        def fake_trash(job_number, upload, phase_name=""):
            del job_number, phase_name
            if upload.remote_path == "fast:root":
                fast_trash_saw_cleanup_barrier.append(slow_cleanup_finished.is_set())
            return True

        with StateSnapshot():
            STATE.upload_directories = [fast, slow]
            STATE.cleanup_threads = 2
            STATE.remote_quota_cleanup_threads = 2
            STATE.trash_cleanup_threads = 2
            initialize_run_results()
            with (
                patch.object(phases, "fetch_remote_snapshot", side_effect=fake_snapshot),
                patch.object(phases, "execute_delete_plan", return_value=True),
                patch.object(phases, "cleanup_one_trash_remote", side_effect=fake_trash),
            ):
                self.assertTrue(phases.run_post_upload_cleanup_phase([]))

        self.assertEqual(fast_trash_saw_cleanup_barrier, [True])

    def test_post_cleanup_failure_does_not_cancel_other_remotes(self):
        from rclone_multithreaded_upload import phases

        uploads = [
            UploadDirectory("/tmp", "good-a:root", [], []),
            UploadDirectory("/tmp", "broken:root", [], []),
            UploadDirectory("/tmp", "good-b:root", [], []),
        ]
        trash_calls = []

        def fake_snapshot(remote_path):
            if remote_path == "broken:root":
                raise RuntimeError("simulated post-cleanup snapshot failure")
            return RemoteSnapshot(remote_path, {})

        def fake_trash(job_number, upload, phase_name=""):
            del job_number, phase_name
            trash_calls.append(upload.remote_path)
            return True

        with StateSnapshot():
            STATE.upload_directories = uploads
            STATE.cleanup_threads = 3
            STATE.remote_quota_cleanup_threads = 3
            STATE.trash_cleanup_threads = 3
            initialize_run_results()
            with (
                patch.object(phases, "fetch_remote_snapshot", side_effect=fake_snapshot),
                patch.object(phases, "execute_delete_plan", return_value=True),
                patch.object(phases, "cleanup_one_trash_remote", side_effect=fake_trash),
            ):
                self.assertFalse(phases.run_post_upload_cleanup_phase([]))

            self.assertEqual(set(trash_calls), {"good-a:root", "good-b:root"})
            self.assertEqual(STATE.run_results["broken:root"].post_cleanup.status, "FAILED")

    def test_packaged_example_config_loads_expected_runtime_state(self):
        project_root = Path(__file__).resolve().parents[1]
        with StateSnapshot():
            load_config(str(project_root / "config.example.json"))
            self.assertEqual(STATE.upload_threads, 2)
            self.assertEqual(STATE.cleanup_threads, 2)
            self.assertEqual(STATE.remote_quota_cleanup_threads, 2)
            self.assertEqual(STATE.trash_cleanup_threads, 2)
            self.assertEqual(
                [upload.name for upload in STATE.upload_directories],
                ["Primary archive", "Mirror archive", "Move completed exports"],
            )
            self.assertEqual(
                [upload.max_total_size for upload in STATE.upload_directories],
                ["500G", "250G", None],
            )


if __name__ == "__main__":
    unittest.main()
