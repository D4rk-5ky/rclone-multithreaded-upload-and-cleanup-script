"""Offline live-preview contract, lifecycle, discovery and worker tests."""
import ast
from dataclasses import asdict, replace
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event
import time
import types
import unittest
from unittest.mock import Mock, patch

from rclone_multithreaded_upload import ha_live_preview as h


def wait_for(predicate, timeout=2):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class LiveTests(unittest.TestCase):
    def live(self, **values):
        return h.LivePreview(h.LiveConfig(**values), name='NAS backup', job='backup', run_id='run-one')

    def test_identity_is_stable_and_separates_configs_and_hosts(self):
        first = h.make_device_id('NAS backup', '/etc/job.toml', 'nas')
        self.assertEqual(first, h.make_device_id('NAS backup', '/etc/job.toml', 'nas'))
        self.assertNotEqual(first, h.make_device_id('NAS backup', '/etc/job2.toml', 'nas'))
        self.assertNotEqual(first, h.make_device_id('NAS backup', '/etc/job.toml', 'other'))

    def test_config_validation_and_secret_repr(self):
        for change in ({'enabled': True}, {'host': 'mqtt://broker'}, {'device_id': 'Bad/id'},
                       {'topic_prefix': 'a/#'}, {'discovery_prefix': ''}, {'heartbeat_seconds': 0},
                       {'min_publish_interval': 11}, {'expire_after': 20}, {'final_wait': float('nan')},
                       {'qos': True}, {'timeout': -1}, {'tls': 'true'}, {'send_on_dry_run': 'false'},
                       {'password': 'secret'}, {'ca_certs': 'ca'}, {'tls': True, 'certfile': 'cert'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                h.LiveConfig(**change)
        self.assertNotIn('secret-token', repr(h.LiveConfig(username='user', password='secret-token')))

    def test_manufacturer_is_configurable_in_discovery_and_state(self):
        config = h.LiveConfig(manufacturer='My Backup Jobs', device_id='nas_one')
        messages = h.discovery_messages(config, 'NAS one')
        self.assertTrue(all(item['payload']['device']['manufacturer'] == 'My Backup Jobs' for item in messages))
        live = h.LivePreview(config, name='NAS one', job='backup')
        self.assertEqual(live.snapshot()['manufacturer'], 'My Backup Jobs')
        defaults = h.discovery_messages(replace(config, manufacturer='MQTT job template'), 'NAS one')
        self.assertEqual([item['payload']['unique_id'] for item in messages],
                         [item['payload']['unique_id'] for item in defaults])

    def test_invalid_manufacturer_names_are_rejected(self):
        for value in ('', ' ', ' leading', 'trailing ', 'bad\x00name', None, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                h.LiveConfig(manufacturer=value)

    def test_aggregate_sensors_have_stable_role_markers(self):
        messages = h.discovery_messages(h.LiveConfig(), 'NAS')
        tagged = [item['payload']['json_attributes_template'] for item in messages
                  if 'json_attributes_template' in item['payload']]
        self.assertEqual(len(tagged), 2)
        self.assertTrue(any('live_preview_role="status"' in text for text in tagged))
        self.assertTrue(any('live_preview_role="last_result"' in text for text in tagged))

    def test_disabled_module_maintains_local_progress_without_processes(self):
        live = self.live()
        with patch.object(h, 'Thread') as thread, patch.object(h.subprocess, 'Popen') as process:
            self.assertFalse(live.start())
            live.update(total=4, completed=1)
            self.assertEqual(live.snapshot()['status'], 'running')
            live.finish(0)
            live.close()
        thread.assert_not_called()
        process.assert_not_called()

    def test_progress_is_derived_and_unknown_is_null(self):
        live = self.live()
        self.assertIsNone(live.snapshot()['progress_percent'])
        live.start()
        live.update(total=4, completed=1, transferred_bytes=123, bytes_per_second=20.5)
        self.assertEqual(live.snapshot()['progress_percent'], 25)
        live.update(total=None)
        self.assertIsNone(live.snapshot()['progress_percent'])

    def test_rejects_bad_counters_and_direct_status_updates(self):
        live = self.live()
        for changes in ({'completed': True}, {'total': -1}, {'completed': 5, 'total': 4},
                        {'bytes_per_second': float('nan')}, {'status': 'success'}, {'exit_code': 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                live.update(**changes)

    def test_stderr_tail_and_utf8_replacement(self):
        live = self.live()
        live.append_stderr(b'x' * 5000 + b'\xff')
        self.assertEqual(len(live.snapshot()['stderr']), 4000)
        self.assertTrue(live.snapshot()['stderr'].endswith('\ufffd'))
        live.update(stderr='new output')
        self.assertEqual(live.snapshot()['stderr'], 'new output')

    def test_completed_success_has_typed_code_and_shared_identity(self):
        live = self.live()
        live.start()
        live.update(total=3, completed=3)
        live.finish(0, warning=True)
        state = live.snapshot()
        self.assertEqual(state['status'], 'success')
        self.assertIs(state['success'], True)
        self.assertEqual(type(state['exit_code']), int)
        self.assertEqual(state['run_id'], 'run-one')
        self.assertEqual(state['progress_percent'], 100)
        self.assertTrue(state['warning'])

    def test_failure_and_interrupt_do_not_force_progress_to_100(self):
        for code, interrupted, status in ((12, False, 'failure'), (130, True, 'interrupted')):
            with self.subTest(status=status):
                live = self.live()
                live.start()
                live.update(total=4, completed=1)
                live.finish(code, error='failed', stderr='worker output', interrupted=interrupted)
                state = live.snapshot()
                self.assertEqual(state['status'], status)
                self.assertIs(state['success'], False)
                self.assertEqual(state['exit_code'], code)
                self.assertEqual(state['progress_percent'], 25)

    def test_zero_code_with_remaining_work_is_incomplete(self):
        live = self.live()
        live.start()
        live.update(total=4, completed=2)
        live.finish(0)
        state = live.snapshot()
        self.assertEqual(state['status'], 'incomplete')
        self.assertIs(state['success'], False)
        self.assertEqual(state['exit_code'], 0)
        self.assertEqual(state['progress_percent'], 50)

    def test_finish_is_idempotent_freezes_runtime_and_rejects_late_updates(self):
        live = self.live()
        live.start()
        live.finish(1)
        runtime = live.snapshot()['runtime_seconds']
        time.sleep(0.02)
        self.assertEqual(live.snapshot()['runtime_seconds'], runtime)
        self.assertFalse(live.finish(0))
        self.assertFalse(live.update(completed=1))
        self.assertFalse(live.append_stderr('late'))
        self.assertFalse(live.start())
        self.assertEqual(live.snapshot()['exit_code'], 1)
        with self.assertRaises(ValueError):
            self.live().finish(True)

    def test_close_without_finish_does_not_invent_a_terminal_result(self):
        live = self.live()
        live.start()
        live.close()
        self.assertEqual(live.snapshot()['status'], 'running')
        self.assertIsNone(live.snapshot()['exit_code'])

    def test_discovery_separates_expiring_state_and_retained_result(self):
        config = h.LiveConfig(device_id='nas_backup')
        messages = h.discovery_messages(config, 'NAS backup')
        self.assertEqual(len(messages), 14)
        for item in messages:
            self.assertTrue(item['retain'])
            payload = item['payload']
            self.assertTrue(payload['default_entity_id'].startswith('sensor.job_live_nas_backup_'))
            if payload['state_topic'] == config.result_topic:
                self.assertNotIn('expire_after', payload)
            else:
                self.assertEqual(payload['expire_after'], 45)
        self.assertEqual(h.discovery_messages(replace(config, discovery=False), 'NAS'), [])

    def test_live_has_dataset_lists_and_counts(self):
        state = self.live().snapshot()
        for label in ('successful', 'failed', 'skipped'):
            self.assertEqual(state[label + '_datasets'], [])
            self.assertEqual(state[label + '_dataset_count'], 0)

    def test_bulk_dataset_reclassification_replaces_counts_and_keeps_reasons(self):
        live = self.live()
        skipped = [{'source': 'pool/b', 'destination': 'backup/b', 'reason': 'Already present'}]
        live.update(failed_datasets=[{'source': 'pool/a', 'destination': 'backup/a', 'reason': 'Receive failed'}],
                    skipped_datasets=skipped)
        self.assertEqual(live.snapshot()['failed_dataset_count'], 1)
        live.update(failed_datasets=[], successful_datasets=[{'source': 'pool/a', 'destination': 'backup/a'}])
        state = live.snapshot()
        self.assertEqual(state['successful_dataset_count'], 1)
        self.assertEqual(state['failed_dataset_count'], 0)
        self.assertEqual(state['skipped_dataset_count'], 1)
        self.assertEqual(state['skipped_datasets'][0]['reason'], 'Already present')
        self.assertIsNone(state['completed'])

    def test_dataset_lists_and_snapshots_are_detached(self):
        live = self.live()
        rows = [{'source': 'pool/a', 'destination': 'backup/a', 'details': {'note': 'original'}}]
        live.update(successful_datasets=rows)
        rows[0]['source'] = 'changed'
        state = live.snapshot()
        state['successful_datasets'][0]['details']['note'] = 'changed'
        self.assertEqual(live.snapshot()['successful_datasets'][0]['source'], 'pool/a')
        self.assertEqual(live.snapshot()['successful_datasets'][0]['details']['note'], 'original')

    def test_failed_datasets_prevent_zero_code_live_success(self):
        live = self.live()
        live.update(total=1, completed=1)
        live.update(failed_datasets=[{'source': 'pool/a', 'reason': 'Failed'}])
        live.finish(0)
        self.assertEqual(live.snapshot()['status'], 'incomplete')
        self.assertIs(live.snapshot()['success'], False)

    def test_dataset_input_validation_and_no_updates_after_finish(self):
        live = self.live()
        for rows in ('text', [None], [{'reason': float('nan')}], None):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                live.update(failed_datasets=rows)
        self.assertFalse(hasattr(live, 'record_dataset'))
        live.finish(0)
        self.assertFalse(live.update(successful_datasets=[{'source': 'pool/a'}]))

    def test_background_heartbeat_and_terminal_batch(self):
        live = self.live(enabled=True, host='broker', heartbeat_seconds=0.05,
                         min_publish_interval=0.01, timeout=0.1, expire_after=1)
        captured = []
        def send(messages):
            captured.append(messages)
            return True
        with patch.object(live, '_send', side_effect=send):
            live.start()
            self.assertTrue(wait_for(lambda: len(captured) >= 2))
            live.update(total=4, completed=2)
            live.finish(12, error='failed')
            live.close()
        self.assertEqual(live.last_delivery, 'sent')
        result = [item for batch in captured for item in batch if item['topic'] == live.config.result_topic]
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['payload']['exit_code'], 12)
        self.assertTrue(result[0]['retain'])
        state = [item for batch in captured for item in batch if item['topic'] == live.config.state_topic]
        self.assertTrue(all(not item['retain'] for item in state))

    def test_updates_are_local_and_coalesced_while_network_is_slow(self):
        live = self.live(enabled=True, host='broker', final_wait=0.05)
        entered, release = Event(), Event()
        captured = []
        def slow(messages):
            entered.set()
            release.wait(1)
            captured.append(messages)
            return True
        with patch.object(live, '_send', side_effect=slow):
            live.start()
            self.assertTrue(entered.wait(1))
            started = time.monotonic()
            for done in range(101):
                live.update(total=100, completed=done)
            self.assertLess(time.monotonic() - started, 0.2)
            release.set()
            live.finish(0)
            live.close()
        self.assertLess(len(captured), 10)

    def test_disabled_dry_run_does_not_start_worker(self):
        live = h.LivePreview(h.LiveConfig(enabled=True, host='broker'), name='Preview', job='preview', dry_run=True)
        with patch.object(h, 'Thread') as thread:
            self.assertFalse(live.start())
            live.finish(0)
        thread.assert_not_called()

    def test_opted_in_dry_run_never_overwrites_retained_last_result(self):
        live = h.LivePreview(h.LiveConfig(enabled=True, host='broker', send_on_dry_run=True),
                             name='Preview', job='preview', dry_run=True)
        batches = []
        with patch.object(live, '_send', side_effect=lambda batch: batches.append(batch) or True):
            live.start()
            live.finish(0)
            live.close()
        self.assertFalse(any(item['topic'] == live.config.result_topic for batch in batches for item in batch))

    def test_actual_worker_isolation_and_final_payload_with_fake_paho(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / 'paho' / 'mqtt'
            package.mkdir(parents=True)
            (root / 'paho' / '__init__.py').write_text('')
            (package / '__init__.py').write_text('')
            (package / 'publish.py').write_text(
                'import json, os\n'
                'def multiple(messages, **kwargs):\n'
                '    with open(os.environ["LIVE_CAPTURE"], "a") as f:\n'
                '        f.write(json.dumps({"messages": messages, "options": kwargs}) + "\\n")\n')
            capture = root / 'capture.jsonl'
            live = self.live(enabled=True, host='broker', timeout=2, final_wait=1,
                             username='user', password='dummy-secret')
            with patch.dict(os.environ, {'PYTHONPATH': temp, 'LIVE_CAPTURE': str(capture)}):
                live.start()
                self.assertTrue(wait_for(capture.exists))
                live.update(total=2, completed=1)
                live.finish(1, stderr='child failure')
                live.close()
            requests = [json.loads(line) for line in capture.read_text().splitlines()]
            result = [message for request in requests for message in request['messages']
                      if message['topic'] == live.config.result_topic]
            self.assertEqual(len(result), 1)
            self.assertEqual(json.loads(result[0]['payload'])['stderr'], 'child failure')
            self.assertEqual(requests[0]['options']['auth']['username'], 'user')

    def test_real_stalled_worker_is_killed_with_bounded_final_wait(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / 'paho' / 'mqtt'
            package.mkdir(parents=True)
            (root / 'paho' / '__init__.py').write_text('')
            (package / '__init__.py').write_text('')
            (package / 'publish.py').write_text('import time\ndef multiple(*args, **kwargs): time.sleep(5)\n')
            live = self.live(enabled=True, host='broker', final_wait=0.05, timeout=3)
            with patch.dict(os.environ, {'PYTHONPATH': temp}):
                live.start()
                self.assertTrue(wait_for(lambda: live._process is not None))
                process = live._process
                started = time.monotonic()
                live.finish(0)
                self.assertLess(time.monotonic() - started, 0.3)
                live.close()
                self.assertTrue(wait_for(lambda: process.poll() is not None))

    def test_failed_delivery_does_not_change_job_outcome(self):
        live = self.live(enabled=True, host='broker')
        with patch.object(live, '_send', return_value=False):
            live.start()
            live.finish(12, error='original failure')
            live.close()
        self.assertEqual(live.last_delivery, 'failed')
        self.assertEqual(live.snapshot()['exit_code'], 12)

    def test_frozen_launch_and_tls_worker_hook(self):
        live = self.live(enabled=True, host='broker')
        fake = Mock()
        fake.returncode = 0
        with patch.object(sys, 'frozen', True, create=True), patch.object(h.subprocess, 'Popen', return_value=fake) as popen:
            self.assertTrue(live._send([]))
        self.assertEqual(popen.call_args.args[0], [sys.executable, h.WORKER_FLAG])
        config = h.LiveConfig(enabled=True, host='broker', tls=True, ca_certs='ca', certfile='cert', keyfile='key')
        module = types.ModuleType('paho.mqtt.publish')
        module.multiple = Mock()
        with patch.dict(sys.modules, {'paho.mqtt.publish': module}), patch.object(sys, 'argv', ['app', h.WORKER_FLAG]), patch.object(sys, 'stdin', io.StringIO(json.dumps({'config': asdict(config), 'messages': []}))), patch.object(h.ssl, 'create_default_context') as context:
            with self.assertRaises(SystemExit) as caught:
                h.run_worker_if_requested()
        self.assertEqual(caught.exception.code, 0)
        context.assert_called_once_with(cafile='ca')
        context.return_value.load_cert_chain.assert_called_once_with('cert', 'key')

    def test_module_imports_without_completion_module_or_paho(self):
        code = '''
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'mqtt_status' or name.startswith('paho'):
        raise AssertionError('unexpected dependency: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import ha_live_preview
assert ha_live_preview.LiveConfig().enabled is False
'''
        result = subprocess.run([sys.executable, '-B', '-c', code], cwd=Path(__file__).resolve().parents[1] / "rclone_multithreaded_upload",
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        for filename in ('ha_live_preview.py',):
            ast.parse((Path(__file__).resolve().parents[1] / "rclone_multithreaded_upload" / filename).read_text(), feature_version=(3, 10))


if __name__ == '__main__':
    unittest.main()
