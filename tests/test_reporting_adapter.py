"""Offline checks of rclone configuration, lifecycle, concurrency and HA isolation."""
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch

from test_logic import StateSnapshot
from rclone_multithreaded_upload.config import load_config, parse_live_config, parse_mqtt_config
from rclone_multithreaded_upload.ha_live_preview import LiveConfig, LivePreview
from rclone_multithreaded_upload.lock import signal_handler
from rclone_multithreaded_upload.models import MqttConfig, UploadDirectory
from rclone_multithreaded_upload.mqtt import build_result_payload
from rclone_multithreaded_upload.mqtt_status import PublishResult, encode_report
from rclone_multithreaded_upload.reporting import append_diagnostics
from rclone_multithreaded_upload.results import initialize_run_results, record_stage_failure, record_stage_success
from rclone_multithreaded_upload.state import STATE

app = importlib.import_module('rclone_multithreaded_upload.main')
STAGES = ('reservation', 'upload', 'post_cleanup', 'final_quota')


class ConfigTests(unittest.TestCase):
    def test_combined_template_configuration_and_disabled_defaults(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('config.example.json', 'rclone-cctv-config.example.json'):
            with self.subTest(name=name), StateSnapshot():
                load_config(str(root / name))
                self.assertIsInstance(STATE.mqtt, MqttConfig)
                self.assertIsInstance(STATE.live_config, LiveConfig)
                self.assertFalse(STATE.mqtt.enabled)
                self.assertFalse(STATE.live_config.enabled)
                self.assertEqual(STATE.live_config.device_id, 'rclone_multithreaded_upload')
                self.assertEqual(STATE.live_config.manufacturer, 'rclone-multithreaded-upload')

    def test_tls_paths_resolve_beside_config_and_password_repr_is_private(self):
        result = parse_mqtt_config({'completion': {'tls': True, 'ca_certs': 'tls/ca.pem',
                                   'username': 'user', 'password': 'secret'}}, Path('/tmp/configdir'))
        live = parse_live_config({'live': {'tls': True, 'ca_certs': 'ca.pem'}}, Path('/tmp/configdir/job.json'))
        self.assertEqual(result.ca_certs, '/tmp/configdir/tls/ca.pem')
        self.assertEqual(live.ca_certs, '/tmp/configdir/ca.pem')
        self.assertNotIn('secret', repr(result))

    def test_default_live_identity_is_stable_and_separates_configs(self):
        a = parse_live_config({}, Path('/tmp/a.json')).device_id
        self.assertEqual(a, parse_live_config({}, Path('/tmp/a.json')).device_id)
        self.assertNotEqual(a, parse_live_config({}, Path('/tmp/b.json')).device_id)

    def test_rclone_manufacturer_defaults_for_existing_configs_and_can_be_overridden(self):
        default = parse_live_config({}, Path('/tmp/old-config.json'))
        self.assertEqual(default.manufacturer, 'rclone-multithreaded-upload')
        custom = parse_live_config({'live': {'manufacturer': 'Custom rclone jobs'}}, Path('/tmp/config.json'))
        self.assertEqual(custom.manufacturer, 'Custom rclone jobs')
        for value in ('', ' padded ', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_live_config({'live': {'manufacturer': value}}, Path('/tmp/config.json'))

    def test_legacy_mqtt_translation(self):
        result = parse_mqtt_config({'mqtt': {'enabled': True, 'host': 'broker', 'client_id': None,
                                  'publish_timeout': 7, 'keepalive': 20, 'retain': False, 'tls_insecure': False}})
        self.assertEqual(result.timeout, 7)
        self.assertEqual(result.qos, 1)
        self.assertEqual(result.client_id, '')
        self.assertEqual(result.topic, 'rclone-multithreaded-upload/result')

    def test_ambiguous_legacy_and_unknown_options_are_rejected(self):
        for config in ({'completion': {}, 'mqtt': {}}, {'completion': {'enabeld': True}},
                       {'mqtt': {'retain': True}}, {'mqtt': {'tls_insecure': True}},
                       {'mqtt': {'timeout': 5, 'publish_timeout': 5}}, {'completion': None}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                parse_mqtt_config(config)
        with self.assertRaises(ValueError):
            parse_live_config({'live': {'enabeld': True}}, Path('/tmp/config.json'))


class LifecycleTests(unittest.TestCase):
    def run_job(self, completion=False, live=False, outcome='success', validate=False,
                delivery_failure=False, occupied_lock=False):
        self.messages, self.reports = [], []
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            lock = root / 'job.lock'
            if occupied_lock:
                lock.write_text('existing')
            config = root / 'config.json'
            config.write_text(json.dumps({
                'script_name': 'Camera archives', 'comment': 'Nightly upload',
                'completion': {'enabled': completion, 'host': 'broker'},
                'live': {'enabled': live, 'host': 'broker', 'device_id': 'cameras', 'min_publish_interval': 0.01},
                'lock_file': str(lock), 'delete_list_dir': str(root / 'lists'),
                'upload_directories': [{'local_path': folder, 'remote_path': 'remote:archive'}],
            }))
            def reservation(_):
                self.assertTrue(lock.exists())
                append_diagnostics('rclone stderr: connection status\n')
                if outcome == 'exception':
                    raise RuntimeError('unexpected upload problem')
                if outcome == 'interrupt':
                    signal_handler(signal.SIGTERM, None)
                if outcome == 'keyboard':
                    raise KeyboardInterrupt()
                record_stage_success('remote:archive', 'reservation')
                if outcome == 'failure':
                    record_stage_failure('remote:archive', 'upload', 'Remote quota exceeded')
                    return False
                record_stage_success('remote:archive', 'upload')
                return True
            def cleanup(_):
                record_stage_success('remote:archive', 'post_cleanup')
                return True
            def verify(_):
                record_stage_success('remote:archive', 'final_quota')
                return True
            def send(_, messages):
                self.messages.extend(messages)
                return True
            def publish(_, report):
                self.assertTrue(lock.exists(), 'Lock must remain held during final reporting')
                encode_report(report)
                self.reports.append(report)
                return PublishResult('failed' if delivery_failure else 'sent', 'Offline test')
            with StateSnapshot(), patch.object(app, 'parse_cli_args', return_value=Namespace(config=str(config), validate_config=validate)), \
                 patch.object(app.signal, 'signal'), patch.object(app.atexit, 'register'), \
                 patch.object(app, 'run_reservation_and_upload_phase', side_effect=reservation) as phase, \
                 patch.object(app, 'run_post_upload_cleanup_phase', side_effect=cleanup), \
                 patch.object(app, 'run_final_verification', side_effect=verify), \
                 patch('rclone_multithreaded_upload.mqtt.publish_report', side_effect=publish), \
                 patch.object(LivePreview, '_send', send):
                if occupied_lock:
                    with self.assertRaises(SystemExit) as result:
                        app.main()
                    self.assertEqual(result.exception.code, 0)
                    self.assertEqual(lock.read_text(), 'existing')
                    code = 0
                else:
                    code = app.main()
                    self.assertFalse(lock.exists())
                if validate or occupied_lock:
                    phase.assert_not_called()
            return code

    def test_all_four_channel_combinations_are_independent(self):
        for completion in (False, True):
            for live in (False, True):
                with self.subTest(completion=completion, live=live):
                    self.assertEqual(self.run_job(completion, live), 0)
                    self.assertEqual(len(self.reports), int(completion))
                    finals = [row for row in self.messages if row['topic'].endswith('/last_result')]
                    self.assertEqual(len(finals), int(live))
                    if live:
                        final = finals[0]
                        self.assertTrue(final['retain'])
                        self.assertEqual(final['payload']['status'], 'success')
                        self.assertEqual(final['payload']['completed'], 4)
                        self.assertEqual(final['payload']['total'], 4)
                        self.assertEqual(final['payload']['progress_percent'], 100)
                        self.assertEqual(final['payload']['successful_dataset_count'], 1)
                        self.assertIsNone(final['payload']['transferred_bytes'])
                        self.assertIsNone(final['payload']['bytes_per_second'])
                        self.assertTrue(all(not row['retain'] for row in self.messages if row['topic'].endswith('/state')))
                        self.assertEqual(len([row for row in self.messages if row['topic'].endswith('/config')]), 14)
                    if completion:
                        report = self.reports[0]
                        self.assertEqual(report['name'], 'Camera archives')
                        self.assertEqual(report['title'], report['name'])
                        self.assertEqual(report['job'], 'rclone-multithreaded-upload')
                        self.assertEqual(report['comment'], 'Nightly upload')
                        self.assertIs(report['dry_run'], False)
                        self.assertIn('rclone stderr', report['stderr'])
                        self.assertNotIn('failed_datasets', report)
                        if live:
                            self.assertEqual(report['run_id'], finals[0]['payload']['run_id'])

    def test_stage_failures_and_exceptions_report_both_channels(self):
        for outcome, message in [('failure', 'Remote quota exceeded'), ('exception', 'unexpected upload problem')]:
            with self.subTest(outcome=outcome):
                self.assertEqual(self.run_job(True, True, outcome), 1)
                self.assertEqual(len(self.reports), 1)
                self.assertIn(message, self.reports[0]['error'])
                self.assertEqual(self.reports[0]['status'], 'failure')
                final = [row for row in self.messages if row['topic'].endswith('/last_result')][0]['payload']
                self.assertEqual(final['status'], 'failure')
                self.assertEqual(final['exit_code'], 1)
                if outcome == 'failure':
                    self.assertEqual(final['failed_dataset_count'], 1)

    def test_interrupts_finalize_before_releasing_the_lock(self):
        for outcome, code in [('interrupt', 143), ('keyboard', 130)]:
            with self.subTest(outcome=outcome):
                self.assertEqual(self.run_job(True, True, outcome), code)
                self.assertEqual(self.reports[0]['exit_code'], code)
                final = [row for row in self.messages if row['topic'].endswith('/last_result')][0]['payload']
                self.assertEqual(final['status'], 'interrupted')
                self.assertFalse(final['success'])
                self.assertEqual(final['skipped_dataset_count'], 1)

    def test_delivery_failure_preserves_success_and_failure_codes(self):
        self.assertEqual(self.run_job(True, True, delivery_failure=True), 0)
        self.assertEqual(self.run_job(True, True, outcome='failure', delivery_failure=True), 1)

    def test_validation_and_existing_lock_do_not_start_channels(self):
        for mode in ('validate', 'occupied_lock'):
            with self.subTest(mode=mode):
                self.assertEqual(self.run_job(True, True, **{mode: True}), 0)
                self.assertEqual(self.reports, [])
                self.assertEqual(self.messages, [])

    def test_live_update_failure_does_not_change_job_result(self):
        with patch.object(LivePreview, 'update', side_effect=RuntimeError('monitor failure')):
            self.assertEqual(self.run_job(True, True), 0)
            self.assertEqual(self.reports[0]['status'], 'success')


class ProgressTests(unittest.TestCase):
    def test_concurrent_stage_counters_are_consistent_and_monotonic(self):
        with StateSnapshot():
            STATE.upload_directories = [UploadDirectory('/tmp', f'remote:{n}', [], []) for n in range(12)]
            initialize_run_results()
            STATE.live_preview = LivePreview(LiveConfig(), name='Camera uploads', job='rclone')
            counts = []
            original = STATE.live_preview.update
            def update(**values):
                if 'completed' in values:
                    counts.append(values['completed'])
                return original(**values)
            with patch.object(STATE.live_preview, 'update', side_effect=update), ThreadPoolExecutor(max_workers=8) as pool:
                jobs = [pool.submit(record_stage_success, upload.remote_path, stage)
                        for upload in STATE.upload_directories for stage in STAGES]
                for job in jobs:
                    job.result()
            self.assertEqual(counts, sorted(counts))
            self.assertEqual(counts[-1], 48)
            self.assertEqual(STATE.live_preview.snapshot()['successful_dataset_count'], 12)

    def test_diagnostics_are_bounded_and_redacted_in_both_channels(self):
        with StateSnapshot():
            STATE.mqtt = MqttConfig(username='user', password='secret-value')
            STATE.live_config = LiveConfig(username='user', password='live-secret')
            STATE.live_preview = LivePreview(LiveConfig(), name='Camera uploads', job='rclone')
            STATE.run_error = STATE.stderr_tail = ''
            STATE.upload_directories = [UploadDirectory('/tmp', 'remote:root', [], [])]
            initialize_run_results()
            append_diagnostics('x' * 5000 + '\nsecret-value live-secret --drive-client-secret=hunter2\n')
            record_stage_failure('remote:root', 'upload', 'secret-value --drive-client-secret=hunter2')
            payload = build_result_payload(1)
            for text in (encode_report(payload), json.dumps(STATE.live_preview.snapshot())):
                for secret in ('secret-value', 'live-secret', 'hunter2'):
                    self.assertNotIn(secret, text)
            self.assertLessEqual(len(payload['stderr']), 4000)
            self.assertEqual(payload['stderr'], STATE.live_preview.snapshot()['stderr'])
