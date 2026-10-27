"""Real operation observers, concurrent destination snapshots and HA card rendering."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from test_logic import StateSnapshot
from rclone_multithreaded_upload import APPLICATION_NAME
from rclone_multithreaded_upload.cleanup import cleanup_one_trash_remote
from rclone_multithreaded_upload.ha_live_preview import LiveConfig, LivePreview
from rclone_multithreaded_upload.models import UploadDirectory, DirectoryCleanupRule
from rclone_multithreaded_upload.mqtt import build_result_payload
from rclone_multithreaded_upload.mqtt_status import encode_report
from rclone_multithreaded_upload.progress import step, finish_steps, skip_step
from rclone_multithreaded_upload.reporting import append_diagnostics, refresh_live, finish_live
from rclone_multithreaded_upload.results import (
    initialize_run_results, record_stage_failure, record_stage_skipped,
    record_stage_success, record_delete_plan_trash_deleted,
)
from rclone_multithreaded_upload.state import STATE
from rclone_multithreaded_upload.upload import upload_one_directory


class ParallelProgressTests(unittest.TestCase):
    def setUp(self):
        self.saved = StateSnapshot()
        self.saved.__enter__()
        self.folder = tempfile.TemporaryDirectory()
        STATE.sleep_after_step = 0
        STATE.run_error = STATE.stderr_tail = ''
        STATE.script_name = 'Parallel Frigate run'
        STATE.upload_directories = [
            UploadDirectory(self.folder.name, 'remote:a', [], [], name='OneDrive', empty_trash=False),
            UploadDirectory(self.folder.name, 'remote:b', [], [DirectoryCleanupRule('/', max_size='2M')],
                            name='GDrive', max_total_size='2M', empty_trash=False),
            UploadDirectory(self.folder.name, 'remote:c', [], [DirectoryCleanupRule('/', max_size='2M'),
                            DirectoryCleanupRule('Camera', max_files=10)], name='Mega', max_total_size='2M'),
        ]
        initialize_run_results()
        STATE.live_preview = LivePreview(LiveConfig(manufacturer=APPLICATION_NAME), name=STATE.script_name, job=APPLICATION_NAME)
        refresh_live()
        STATE.live_preview.start()

    def tearDown(self):
        STATE.live_preview.close()
        self.folder.cleanup()
        self.saved.__exit__(None, None, None)

    def _render_snapshot(self, snapshot):
        from test_manufacturer_card import CardTests, NativeEnvironment
        if NativeEnvironment is None:
            self.skipTest('Card rendering needs requirements-test.txt')
        card = CardTests()
        card.setUp()
        card.add('parallel_frigate', name=STATE.script_name, live=snapshot, renamed=True)
        return card.render()

    def test_each_destination_total_depends_on_its_own_config_and_overall_is_sum(self):
        snapshot = STATE.live_preview.snapshot()
        rows = snapshot['details']['remote_jobs']
        self.assertEqual([row['total'] for row in rows], [7, 12, 16])
        self.assertEqual(snapshot['total'], 35)
        self.assertEqual(snapshot['completed'], 0)
        self.assertNotIn('total_stages', snapshot['details'])
        self.assertTrue(all('stages' not in row for row in rows))
        self.assertEqual(rows[2]['steps'][2]['kind'], 'cleanup')
        self.assertEqual(len([s for s in rows[2]['steps'] if '_rule:' in s['id']]), 4)
        # A returned snapshot is detached from the state maintained by workers.
        rows[0]['steps'][0]['status'] = 'FAILED'
        self.assertEqual(STATE.live_preview.snapshot()['details']['remote_jobs'][0]['steps'][0]['status'], 'PENDING')

    def _hold_three_operations(self, cleanup=False, render=False):
        uploads = STATE.upload_directories
        if cleanup:
            for upload in uploads:
                upload.empty_trash = True
            initialize_run_results()
            for upload in uploads:
                record_delete_plan_trash_deleted(upload.remote_path, 'reservation')
            refresh_live()
        started = {upload.remote_path: Event() for upload in uploads}
        release = {upload.remote_path: Event() for upload in uploads}
        def hold(remote):
            append_diagnostics(f'{remote} private diagnostic\n')
            started[remote].set()
            if not release[remote].wait(5):
                raise RuntimeError('Offline concurrency fixture timed out')
        def stream(command, thread_number, remote_path):
            hold(remote_path)
            return 0, ''
        def command(args, capture_output=False):
            hold(args[-1])
            return subprocess.CompletedProcess(args, 0, stdout='', stderr='')
        target = 'rclone_multithreaded_upload.cleanup.run_command' if cleanup else 'rclone_multithreaded_upload.upload.run_command_streamed'
        with patch(target, side_effect=command if cleanup else stream), ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(cleanup_one_trash_remote, i, upload, 'POST-RESERVATION TRASH CLEANUP')
                       if cleanup else pool.submit(upload_one_directory, i, upload)
                       for i, upload in enumerate(uploads, 1)]
            try:
                for event in started.values():
                    self.assertTrue(event.wait(3), 'Every destination must be independently running')
                snapshot = STATE.live_preview.snapshot()
                detail = snapshot['details']
                self.assertEqual(detail['active_destination_count'], 3)
                self.assertEqual(detail['running'], 3)
                self.assertEqual(detail['activity_counts'], {'Trash cleanup' if cleanup else 'Upload': 3})
                self.assertEqual(snapshot['total'], sum(row['total'] for row in detail['remote_jobs']))
                for row in detail['remote_jobs']:
                    self.assertEqual(row['status'], 'running')
                    self.assertEqual(row['running'], 1)
                    self.assertIn(row['name'], snapshot['current_item'])
                    self.assertIn(row['destination'] + ' private diagnostic', row['stderr'])
                    for other in detail['remote_jobs']:
                        if other is not row:
                            self.assertNotIn(other['destination'] + ' private diagnostic', row['stderr'])
                    active = next(task for task in row['steps'] if task['status'] == 'RUNNING')
                    self.assertEqual(active['id'], 'pre_trash' if cleanup else 'upload')
                    self.assertIsNotNone(active['started_at'])
                    self.assertIsNone(active['finished_at'])
                if render:
                    text = self._render_snapshot(snapshot)
                    self.assertIn('**Destinations:** 3', text)
                    self.assertIn('**Active destinations:** 3', text)
                    self.assertIn('**Steps:** 0 / 35 settled', text)
                    for name, count in [('OneDrive', 7), ('GDrive', 12), ('Mega', 16)]:
                        self.assertIn('#### ' + name + ' — RUNNING', text)
                        self.assertIn(f'**Progress:** 0 / {count} steps settled', text)
                        self.assertIn(f'| {name} | Upload: Upload files (copy) | 0 / {count} | 0.0% | 0 B | 0 B/s | RUNNING |', text)
                    self.assertEqual(text.count('| 🟢 RUNNING |'), 3)
                    self.assertIn('| Step | Phase | State | Detail |\n|:---|:---|:---|:---|\n| Pre-upload remote snapshot', text)
                release[uploads[0].remote_path].set()
                self.assertTrue(futures[0].result(timeout=3))
                snapshot = STATE.live_preview.snapshot()
                self.assertEqual(snapshot['details']['active_destination_count'], 2)
                self.assertEqual(snapshot['completed'], 1)
                self.assertEqual(snapshot['details']['remote_jobs'][0]['completed'], 1)
                self.assertEqual(snapshot['details']['remote_jobs'][1]['completed'], 0)
            finally:
                for event in release.values():
                    event.set()
            for future in futures:
                self.assertTrue(future.result(timeout=3))
        self.assertEqual(STATE.live_preview.snapshot()['details']['running'], 0)

    def test_each_cleanup_rule_has_independent_pre_and_post_planning_counts(self):
        remote = 'remote:c'
        rows = STATE.live_preview.snapshot()['details']['remote_jobs'][2]['cleanup_jobs']
        self.assertEqual([row['total'] for row in rows], [2, 2])
        self.assertEqual([row['completed'] for row in rows], [0, 0])
        first_path = rows[0]['path']
        with step(remote, 'pre_rule:' + first_path):
            pass
        rows = STATE.live_preview.snapshot()['details']['remote_jobs'][2]['cleanup_jobs']
        self.assertEqual([row['completed'] for row in rows], [1, 0])
        self.assertEqual(rows[0]['pre_status'], 'SUCCESS')
        self.assertEqual(rows[0]['post_status'], 'PENDING')
        with step(remote, 'post_rule:' + first_path):
            pass
        snapshot = STATE.live_preview.snapshot()
        self.assertEqual(snapshot['completed'], 2)  # Nested views are not counted twice.
        self.assertEqual(snapshot['total'], 35)
        self.assertEqual([row['completed'] for row in snapshot['details']['remote_jobs'][2]['cleanup_jobs']], [2, 0])

    def test_all_three_actual_upload_workers_are_reported_at_once(self):
        self._hold_three_operations()

    def test_all_three_actual_trash_cleanup_workers_are_reported_at_once(self):
        self._hold_three_operations(cleanup=True)

    def test_manufacturer_card_shows_every_parallel_destination_and_its_steps(self):
        self._hold_three_operations(render=True)

    def test_overall_and_table_show_upload_cleanup_and_trash_running_together(self):
        with ExitStack() as active:
            for remote, key in [('remote:a', 'upload'), ('remote:b', 'pre_delete'), ('remote:c', 'pre_trash')]:
                active.enter_context(step(remote, key))
            snapshot = STATE.live_preview.snapshot()
            self.assertEqual(snapshot['details']['activity_counts'], {'Upload': 1, 'Cleanup': 1, 'Trash cleanup': 1})
            text = self._render_snapshot(snapshot)
            self.assertIn('**Phase:** Upload: 1; Cleanup: 1; Trash cleanup: 1', text)
            self.assertIn('| OneDrive | Upload: Upload files (copy) | 0 / 7', text)
            self.assertIn('| GDrive | Cleanup: Execute pre-upload delete plan | 0 / 12', text)
            self.assertIn('| Mega | Trash cleanup: Pre-upload trash cleanup | 0 / 16', text)
            self.assertLess(text.index('**Overall run progress**'), text.index('| Destination |'))
            self.assertLess(text.index('| Destination |'), text.index('#### OneDrive'))
        snapshot = STATE.live_preview.snapshot()
        self.assertEqual(snapshot['completed'], 3)
        self.assertEqual(snapshot['details']['activity_counts'], {})
        self.assertEqual(snapshot['details']['activity'], 'Waiting for next phase')

    def test_overview_rows_and_totals_follow_added_and_removed_config_destinations(self):
        uploads = list(STATE.upload_directories)
        uploads.append(UploadDirectory(self.folder.name, 'remote:d', [], [], name='Nextcloud', empty_trash=False))
        for count in [1, 2, 3, 4, 2]:
            with self.subTest(destinations=count):
                STATE.upload_directories = uploads[:count]
                initialize_run_results()
                refresh_live()
                snapshot = STATE.live_preview.snapshot()
                rows = snapshot['details']['remote_jobs']
                text = self._render_snapshot(snapshot)
                self.assertEqual(snapshot['details']['destination_count'], count)
                self.assertEqual(snapshot['total'], sum([7, 12, 16, 7][:count]))
                self.assertEqual(text.count(' | Waiting for next phase | '), count)
                for row in rows:
                    self.assertIn(f'| {row["name"]} | Waiting for next phase | 0 / {row["total"]} | 0.0% | 0 B | 0 B/s | PENDING |', text)
                for upload in uploads[count:]:
                    self.assertNotIn('| ' + upload.name + ' |', text)

    def test_finished_run_has_no_active_activity_or_stale_upload_label(self):
        with step('remote:a', 'upload'):
            self.assertEqual(STATE.live_preview.snapshot()['details']['activity'], 'Upload: 1')
        finish_steps(0)
        snapshot = STATE.live_preview.snapshot()
        self.assertEqual(snapshot['details']['activity'], 'Finished')
        self.assertEqual(snapshot['details']['activity_counts'], {})
        self.assertEqual(snapshot['completed'], snapshot['total'])
        self.assertTrue(all(row['activity'] == 'Finished' for row in snapshot['details']['remote_jobs']))

    def test_overview_escapes_configured_names_with_current_details(self):
        with step('remote:a', 'upload'):
            snapshot = STATE.live_preview.snapshot()
        snapshot['details']['remote_jobs'][0]['name'] = 'OneDrive | <backup>\nPrimary'
        text = self._render_snapshot(snapshot)
        self.assertIn('| OneDrive &#124; &lt;backup&gt; Primary | Upload: Upload files (copy) | 0 / 7', text)
        self.assertIn('| Mega | Waiting for next phase | 0 / 16', text)

    def test_failed_preparation_skips_its_dependents_but_leaves_post_cleanup_available(self):
        remote = 'remote:b'
        with self.assertRaisesRegex(RuntimeError, 'Snapshot failed'):
            with step(remote, 'pre_snapshot'):
                raise RuntimeError('Snapshot failed')
        record_stage_failure(remote, 'reservation', 'Snapshot failed')
        record_stage_skipped(remote, 'upload')
        tasks = STATE.run_results[remote].steps
        self.assertEqual(tasks['pre_snapshot'].status, 'FAILED')
        self.assertTrue(all(task.status == 'SKIPPED' for key, task in tasks.items()
                            if task.stage == 'reservation' and key != 'pre_snapshot'))
        self.assertEqual(tasks['upload'].status, 'SKIPPED')
        self.assertEqual(tasks['post_snapshot'].status, 'PENDING')
        self.assertTrue(all(task.status == 'PENDING' for task in STATE.run_results['remote:a'].steps.values()))
        for key, task in tasks.items():
            if task.stage in ('post_cleanup', 'final_quota'):
                with step(remote, key):
                    pass
        finish_steps(1)
        detail = STATE.live_preview.snapshot()['details']
        self.assertEqual(detail['completed'], detail['total'])
        self.assertEqual(detail['failed'], 1)
        self.assertEqual(detail['active_destination_count'], 0)

    def test_interruption_preserves_active_step_outcome_and_never_leaves_ghost_workers(self):
        with self.assertRaises(KeyboardInterrupt):
            with step('remote:a', 'upload'):
                raise KeyboardInterrupt()
        finish_steps(130, interrupted=True)
        finish_live(130, error='Interrupted', interrupted=True)
        payload = STATE.live_preview.snapshot()
        self.assertEqual(payload['status'], 'interrupted')
        self.assertEqual(payload['details']['running'], 0)
        self.assertEqual(payload['completed'], payload['total'])
        self.assertEqual(STATE.run_results['remote:a'].steps['upload'].status, 'INTERRUPTED')
        self.assertEqual(payload['details']['failed'], 1)

    def test_zero_selected_files_are_skipped_without_a_transfer_but_keep_legacy_success(self):
        upload = STATE.upload_directories[1]
        STATE.planned_upload_files[upload.remote_path] = ()
        with patch('rclone_multithreaded_upload.upload.run_command_streamed') as transfer:
            self.assertTrue(upload_one_directory(1, upload))
            transfer.assert_not_called()
        self.assertEqual(STATE.run_results[upload.remote_path].upload.status, 'SUCCESS')
        self.assertEqual(STATE.run_results[upload.remote_path].steps['upload'].status, 'SKIPPED')
        row = STATE.live_preview.snapshot()['details']['remote_jobs'][1]
        self.assertEqual(row['completed'], 1)
        self.assertEqual(row['skipped'], 1)
        self.assertEqual(row['succeeded'], 0)

    def test_live_details_are_validated_detached_and_retained_in_completion_extensions(self):
        with self.assertRaises(ValueError):
            STATE.live_preview.update(details=[])
        with self.assertRaises(ValueError):
            STATE.live_preview.update(details={'bad': float('nan')})
        with step('remote:a', 'upload'):
            report = build_result_payload(1)
            self.assertEqual(report['progress']['running'], 1)
            self.assertEqual(report['progress']['total'], 35)
            self.assertEqual(report['remotes'][0]['progress']['active_steps'], ['Upload files (copy)'])
            self.assertEqual(json.loads(encode_report(report))['status'], 'failure')
        self.assertEqual(report['remotes'][0]['progress']['running'], 1)
        self.assertEqual(STATE.live_preview.snapshot()['details']['running'], 0)

    def test_concurrent_step_updates_keep_all_aggregate_and_destination_counts_monotonic(self):
        captures = []
        original = STATE.live_preview.update
        def capture(**values):
            if 'details' in values:
                detail = values['details']
                self.assertEqual(values['completed'], sum(row['completed'] for row in detail['remote_jobs']))
                self.assertEqual(values['total'], sum(row['total'] for row in detail['remote_jobs']))
                self.assertEqual(detail['running'], sum(row['running'] for row in detail['remote_jobs']))
                captures.append(values['completed'])
            return original(**values)
        def perform(remote, key):
            with step(remote, key):
                pass
        with patch.object(STATE.live_preview, 'update', side_effect=capture), ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(perform, remote, key) for remote, result in STATE.run_results.items() for key in result.steps]
            for future in futures:
                future.result(timeout=3)
        self.assertEqual(captures, sorted(captures))
        self.assertEqual(captures[-1], 35)

    def test_shared_local_snapshot_keeps_each_destination_acquisition_step_visible(self):
        from rclone_multithreaded_upload.reservation import clear_local_size_cache, get_filtered_local_upload_snapshot
        from rclone_multithreaded_upload.models import LocalUploadSnapshot
        import time
        clear_local_size_cache()
        entered, release = Event(), Event()
        def calculate(upload):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Shared-source fixture timed out')
            return LocalUploadSnapshot((), 0)
        with patch('rclone_multithreaded_upload.reservation._calculate_filtered_local_upload_snapshot', side_effect=calculate) as scan, ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(get_filtered_local_upload_snapshot, i, upload) for i, upload in enumerate(STATE.upload_directories[1:], 1)]
            try:
                self.assertTrue(entered.wait(3))
                deadline = time.monotonic() + 3
                while STATE.live_preview.snapshot()['details']['active_destination_count'] != 2 and time.monotonic() < deadline:
                    Event().wait(0.01)
                rows = STATE.live_preview.snapshot()['details']['remote_jobs']
                self.assertEqual([row['running'] for row in rows], [0, 1, 1])
                self.assertTrue(all(next(task for task in row['steps'] if task['id'] == 'local_snapshot')['status'] == 'RUNNING' for row in rows[1:]))
            finally:
                release.set()
            for future in futures:
                self.assertEqual(future.result(timeout=3).file_count, 0)
        self.assertEqual(scan.call_count, 1)
        self.assertEqual(STATE.live_preview.snapshot()['completed'], 2)

    def test_later_observations_cannot_erase_a_failed_step(self):
        with self.assertRaises(RuntimeError):
            with step('remote:a', 'upload'):
                raise RuntimeError('Actual transfer failed')
        record_stage_failure('remote:a', 'upload', 'Actual transfer failed')
        with step('remote:a', 'upload'):
            pass
        record_stage_success('remote:a', 'upload')
        self.assertEqual(STATE.run_results['remote:a'].steps['upload'].status, 'FAILED')
        self.assertEqual(STATE.run_results['remote:a'].upload.status, 'FAILED')

    def test_observer_failure_does_not_change_a_real_upload_return_value(self):
        with patch.object(STATE.live_preview, 'update', side_effect=RuntimeError('Monitor unavailable')), \
             patch('rclone_multithreaded_upload.upload.run_command_streamed', return_value=(0, '')):
            self.assertTrue(upload_one_directory(1, STATE.upload_directories[0]))
        self.assertEqual(STATE.run_results['remote:a'].upload.status, 'SUCCESS')
