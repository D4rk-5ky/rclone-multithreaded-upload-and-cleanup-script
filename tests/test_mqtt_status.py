"""Contract, policy, transport-boundary and actual worker-isolation checks.

No external broker or network access is used. Run: python3 -m unittest -v
"""
import ast
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

from rclone_multithreaded_upload import mqtt_status as m


class ReportTests(unittest.TestCase):
    def report(self, code=0, **kwargs):
        return m.build_report(name='NAS backup', job='nas-backup', exit_code=code, **kwargs)

    def test_syncerate_core_fields_are_present_and_typed(self):
        payload = self.report()
        expected = {'status', 'success', 'title', 'name', 'job', 'dry_run',
                    'exit_code', 'error', 'stderr', 'warning'}
        self.assertTrue(expected <= payload.keys())
        self.assertEqual(payload['title'], payload['name'])
        self.assertEqual(payload['status'], 'success')
        self.assertIs(payload['success'], True)
        self.assertEqual(payload['error'], '')
        self.assertEqual(json.loads(m.build_mqtt_status_payload(name='x', job='x', exit_code=0))['exit_code'], 0)

    def test_generic_completion_report_contains_no_dataset_sections(self):
        for key in ('planned_datasets', 'skipped_datasets', 'failed_datasets',
                    'successful_datasets', 'completed_datasets'):
            self.assertNotIn(key, self.report())
        self.assertEqual(json.loads(m.encode_report(self.report()))['status'], 'success')

    def test_explicit_syncerate_diagnostics_remain_supported_including_empty_lists(self):
        payload = self.report(12, planned_datasets=[], skipped_datasets=[],
                              failed_datasets=[{'source': 'pool/a', 'destination': 'backup/a', 'reason': 'Out of space'}])
        self.assertEqual(payload['planned_datasets'], [])
        self.assertEqual(payload['skipped_datasets'], [])
        self.assertEqual(payload['failed_datasets'][0]['reason'], 'Out of space')
        self.assertEqual(json.loads(m.encode_report(payload)), payload)

    def test_invalid_optional_dataset_extensions_are_rejected(self):
        for value in (None, 'text', ['text'], {'source': 'a'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                m.encode_report({**self.report(), 'failed_datasets': value})

    def test_failures_preserve_nonzero_and_signal_codes(self):
        for code in (1, 12, 23, 130, -15):
            with self.subTest(code=code):
                payload = self.report(code, error_message='failed')
                self.assertEqual(payload['status'], 'failure')
                self.assertIs(payload['success'], False)
                self.assertEqual(payload['exit_code'], code)

    def test_rejects_boolean_or_string_exit_codes(self):
        for code in (True, False, '0', 0.0, None):
            with self.subTest(code=code), self.assertRaises(ValueError):
                self.report(code)

    def test_warning_and_preview_are_explicit_and_do_not_change_outcome(self):
        payload = self.report(warning=True, dry_run=True)
        self.assertTrue(payload['warning'])
        self.assertTrue(payload['dry_run'])
        self.assertEqual(payload['status'], 'success')
        with self.assertRaises(ValueError):
            self.report(dry_run='false')

    def test_bounded_unicode_stderr_and_null_normalization(self):
        payload = self.report(stderr_text=(b'x' * 5000) + b'\xff', error_message=None)
        self.assertEqual(len(payload['stderr']), m.STDERR_LIMIT)
        self.assertTrue(payload['stderr'].endswith('\ufffd'))
        self.assertEqual(payload['error'], '')
        text = m.encode_report(self.report(title='Danish backup æøå'))
        self.assertIn('æøå', text)

    def test_extra_project_details_and_datasets_are_detached(self):
        extra = {'script_name': 'Camera uploads', 'remotes': [{'name': 'Cloud'}]}
        datasets = [{'source': 'pool/a', 'destination': 'backup/a'}]
        payload = self.report(extra=extra, skipped_datasets=datasets)
        extra['remotes'][0]['name'] = 'changed'
        datasets[0]['source'] = 'changed'
        self.assertEqual(payload['remotes'][0]['name'], 'Cloud')
        self.assertEqual(payload['skipped_datasets'][0]['source'], 'pool/a')

    def test_extensions_cannot_override_contract(self):
        for field in ('status', 'exit_code', 'name', 'dry_run', 'schema_version'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.report(extra={field: 'wrong'})

    def test_rejects_non_json_extensions(self):
        for value in (float('nan'), float('inf'), object(), b'bytes', {1: 'bad-key'}):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                self.report(extra={'value': value})

    def test_encoder_rejects_mutated_inconsistent_reports(self):
        for changes in ({'status': 'failed'}, {'success': False}, {'exit_code': True},
                        {'dry_run': 'false'}, {'warning': 0}, {'name': ''},
                        {'failed_datasets': ['text']}, {'schema_version': True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                m.encode_report({**self.report(), **changes})


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.config = m.MQTTConfig(enabled=True, host='broker.example', topic='jobs/nas/status',
                                   username='user', password='DO-NOT-LOG-THIS', timeout=2)
        self.report = m.build_report(name='NAS backup', job='nas-backup', exit_code=0)

    def test_config_rejects_invalid_values(self):
        cases = [{'host': ''}, {'host': 'mqtt://broker'}, {'topic': 'jobs/#'}, {'topic': 'a\x00b'},
                 {'port': True}, {'port': 0}, {'qos': 3}, {'qos': True}, {'timeout': float('nan')},
                 {'timeout': 0}, {'enabled': 'true'}, {'on_success': 1}, {'certfile': 'cert'},
                 {'tls': False, 'ca_certs': 'ca'}, {'username': ''}]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(self.config, **values)
        self.assertNotIn(self.config.password, repr(self.config))

    def test_disabled_mqtt_does_no_work_even_without_a_valid_report(self):
        with patch.object(m.subprocess, 'run') as run:
            result = m.publish_report(m.MQTTConfig(), {})
        self.assertEqual(result.status, 'skipped')
        run.assert_not_called()

    def test_on_success_false_suppresses_only_success(self):
        cfg = replace(self.config, on_success=False)
        with patch.object(m.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(m.publish_report(cfg, self.report).status, 'skipped')
            run.assert_not_called()
            failure = m.build_report(name='NAS backup', job='nas-backup', exit_code=12)
            self.assertTrue(m.publish_report(cfg, failure).sent)
            self.assertEqual(json.loads(run.call_args.kwargs['input'])['report']['exit_code'], 12)

    def test_preview_suppression_is_independent(self):
        for code in (0, 1):
            preview = m.build_report(name='preview', job='preview', exit_code=code, dry_run=True)
            with patch.object(m.subprocess, 'run') as run:
                result = m.publish_report(replace(self.config, send_on_dry_run=False), preview)
                self.assertEqual(result.status, 'skipped')
                run.assert_not_called()

    def test_credentials_use_stdin_and_parent_sets_timeout(self):
        with patch.object(m.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            result = m.publish_report(self.config, self.report)
        args, kwargs = run.call_args
        self.assertNotIn(self.config.password, ' '.join(args[0]))
        self.assertEqual(json.loads(kwargs['input'])['config']['password'], self.config.password)
        self.assertEqual(kwargs['timeout'], self.config.timeout)
        self.assertEqual(result.status, 'sent')

    def test_timeout_and_worker_failures_are_nonfatal_and_sanitized(self):
        original = json.loads(m.encode_report(self.report))
        for effect in (subprocess.TimeoutExpired(['worker'], 2), OSError('DO-NOT-LOG-THIS')):
            with patch.object(m.subprocess, 'run', side_effect=effect):
                result = m.publish_report(self.config, self.report)
            self.assertEqual(result.status, 'failed')
            self.assertNotIn(self.config.password, result.reason)
        with patch.object(m.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, stderr=self.config.password)):
            self.assertEqual(m.publish_report(self.config, self.report).status, 'failed')
        self.assertEqual(self.report, original)

    def test_invalid_report_does_not_launch_a_worker(self):
        with patch.object(m.subprocess, 'run') as run:
            result = m.publish_report(self.config, {**self.report, 'status': 'failure'})
        self.assertEqual(result.status, 'failed')
        run.assert_not_called()

    def test_frozen_worker_reexecutes_binary(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(m.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            m.publish_report(self.config, self.report)
        self.assertEqual(run.call_args.args[0], [sys.executable, m.WORKER_FLAG])

    def test_actual_subprocess_worker_and_timeout_with_fake_paho(self):
        # A disposable package substitutes only the broker helper; this exercises
        # the real process launch, stdin decoding, report serialization and kill.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / 'paho' / 'mqtt'
            package.mkdir(parents=True)
            (root / 'paho' / '__init__.py').write_text('')
            (package / '__init__.py').write_text('')
            (package / 'publish.py').write_text(
                'import json, os, time\n'
                'def single(*args, **kwargs):\n'
                '    if os.environ.get("TEMPLATE_TEST_STALL") == "1": time.sleep(5)\n'
                '    with open(os.environ["TEMPLATE_TEST_CAPTURE"], "w") as f:\n'
                '        json.dump({"topic": args[0], **kwargs}, f)\n')
            capture = root / 'capture.json'
            env = {'PYTHONPATH': temp, 'TEMPLATE_TEST_CAPTURE': str(capture)}
            with patch.dict(os.environ, env):
                result = m.publish_report(self.config, self.report)
                self.assertEqual(result.status, 'sent')
                request = json.loads(capture.read_text())
                self.assertFalse(request['retain'])
                self.assertEqual(request['qos'], 0)
                self.assertEqual(request['topic'], self.config.topic)
                self.assertEqual(json.loads(request['payload']), self.report)
                self.assertEqual(request['auth']['username'], 'user')
                with patch.dict(os.environ, {'TEMPLATE_TEST_STALL': '1'}):
                    started = time.monotonic()
                    timed = m.publish_report(replace(self.config, timeout=0.5), self.report)
                    self.assertEqual(timed.status, 'failed')
                    self.assertIn('timed out', timed.reason)
                    self.assertLess(time.monotonic() - started, 3)

    def test_tls_verifies_certificates_and_loads_client_pair(self):
        fake_single = Mock()
        publish = types.ModuleType('paho.mqtt.publish')
        publish.single = fake_single
        with patch.dict(sys.modules, {'paho.mqtt.publish': publish}), patch.object(m.ssl, 'create_default_context') as context:
            cfg = replace(self.config, tls=True, ca_certs='ca.pem', certfile='cert.pem', keyfile='key.pem')
            m._publish_once(cfg, self.report)
            context.assert_called_once_with(cafile='ca.pem')
            context.return_value.load_cert_chain.assert_called_once_with('cert.pem', 'key.pem')
        self.assertIs(fake_single.call_args.kwargs['tls'], context.return_value)
        self.assertIs(fake_single.call_args.kwargs['retain'], False)

    def test_import_does_not_require_paho_or_launch_processes(self):
        code = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith('paho'):
        raise AssertionError('Paho imported during ordinary module import')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import mqtt_status
assert mqtt_status.MQTTConfig().enabled is False
"""
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                                cwd=Path(__file__).resolve().parents[1] / "rclone_multithreaded_upload", timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_normal_worker_hook_does_not_consume_application_arguments(self):
        with patch.object(sys, 'argv', ['app.py', '--dry-run']):
            self.assertIs(m.run_worker_if_requested(), False)

    def test_source_syntax_is_python_310_compatible(self):
        for name in ('mqtt_status.py',):
            ast.parse((Path(__file__).resolve().parents[1] / "rclone_multithreaded_upload" / name).read_text(), feature_version=(3, 10))


if __name__ == '__main__':
    unittest.main()
