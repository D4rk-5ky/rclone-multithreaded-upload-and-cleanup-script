"""Measured counters, current/average rates, card layout and real worker-stream tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch

from test_logic import StateSnapshot
import test_manufacturer_card as manufacturer_card
from rclone_multithreaded_upload.config import load_config
from rclone_multithreaded_upload.ha_live_preview import LiveConfig, LivePreview
from rclone_multithreaded_upload.models import UploadDirectory
from rclone_multithreaded_upload.results import initialize_run_results
from rclone_multithreaded_upload.reporting import refresh_live
from rclone_multithreaded_upload.state import STATE
from rclone_multithreaded_upload.transfer_stats import (
    live_stats_options, start_transfer, finish_transfer, record_transfer_stats,
)
from rclone_multithreaded_upload.upload import run_command_streamed

ROOT = Path(__file__).resolve().parents[1]
MIB = 1048576


def sample(size, speed, elapsed=1):
    return json.dumps({'level':'notice','msg':'Synthetic stats', 'stats':{'bytes':size,'speed':speed,'elapsedTime':elapsed}})


def wait_for(predicate, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class TransferStatsTests(unittest.TestCase):
    def setUp(self):
        self.saved = StateSnapshot()
        self.saved.__enter__()
        STATE.sleep_after_step = 0
        STATE.stderr_tail = ''
        STATE.live_config = LiveConfig()
        STATE.upload_directories = [UploadDirectory('/unused', 'remote:'+str(i), [], [], name=name, empty_trash=False)
                                    for i,name in enumerate(['OneDrive','GDrive','Mega'])]
        initialize_run_results()
        STATE.live_preview = LivePreview(LiveConfig(), name='Three destinations', job='rclone-multithreaded-upload')
        refresh_live()
        STATE.live_preview.start()

    def tearDown(self):
        STATE.live_preview.close()
        self.saved.__exit__(None,None,None)

    def test_cumulative_samples_are_independent_and_combined_without_double_counting(self):
        with patch('rclone_multithreaded_upload.transfer_stats.time.monotonic', return_value=100) as clock:
            for i in range(3): start_transfer('remote:'+str(i))
            self.assertIsNone(STATE.live_preview.snapshot()['transferred_bytes'])
            clock.return_value = 110
            for i in range(3):
                self.assertTrue(record_transfer_stats('remote:'+str(i), sample((i+1)*MIB,(i+1)*MIB,10)))
            # Replaying an absolute counter must never add its bytes again.
            record_transfer_stats('remote:0', sample(MIB,MIB,10))
            snapshot = STATE.live_preview.snapshot()
            self.assertEqual(snapshot['transferred_bytes'],6*MIB)
            self.assertEqual(snapshot['bytes_per_second'],6*MIB)
            self.assertEqual([row['transferred_bytes'] for row in snapshot['details']['remote_jobs']],[MIB,2*MIB,3*MIB])
            self.assertEqual(snapshot['details']['remote_jobs'][0]['average_bytes_per_second'],MIB/10)
            # Reordered/decreasing snapshots do not replace newer counters/rates.
            record_transfer_stats('remote:0',sample(5*MIB,99*MIB,9))
            record_transfer_stats('remote:0',sample(1,99*MIB,11))
            self.assertEqual(STATE.live_preview.snapshot()['transferred_bytes'],6*MIB)
            finish_transfer('remote:0')
            after = STATE.live_preview.snapshot()
            self.assertEqual(after['bytes_per_second'],5*MIB)
            self.assertEqual(after['transferred_bytes'],6*MIB)
            self.assertEqual(after['details']['remote_jobs'][0]['bytes_per_second'],0)
            clock.return_value = 120
            refresh_live()
            self.assertEqual(STATE.live_preview.snapshot()['details']['remote_jobs'][0]['average_bytes_per_second'],MIB/10)

    def test_invalid_packets_never_replace_measured_counters(self):
        start_transfer('remote:0')
        record_transfer_stats('remote:0',sample(100,10))
        for line in ['not json','[]','{"msg":"error"}',sample(True,1),sample(-1,1),sample(1,None),
                     sample(1,True),sample(1,-1),sample(1,float('nan')),sample(1,float('inf')),
                     sample(1,1,False),sample(1,1,-1),sample(1,10**500)]:
            with self.subTest(line=line[:80]):
                self.assertFalse(record_transfer_stats('remote:0',line))
        self.assertEqual(STATE.live_preview.snapshot()['details']['remote_jobs'][0]['transferred_bytes'],100)

    def test_stale_active_speed_becomes_unknown_and_finished_speed_is_zero(self):
        with patch('rclone_multithreaded_upload.transfer_stats.time.monotonic',return_value=100) as clock:
            start_transfer('remote:0')
            clock.return_value = 101
            record_transfer_stats('remote:0',sample(100,10))
            clock.return_value = 105
            refresh_live()
            row = STATE.live_preview.snapshot()['details']['remote_jobs'][0]
            self.assertTrue(row['transfer_stats_stale'])
            self.assertIsNone(row['bytes_per_second'])
            self.assertEqual(row['transferred_bytes'],100)
            finish_transfer('remote:0')
            record_transfer_stats('remote:0',sample(999,999,10))
            row = STATE.live_preview.snapshot()['details']['remote_jobs'][0]
            self.assertEqual(row['bytes_per_second'],0)
            self.assertEqual(row['transferred_bytes'],100)

    def test_live_logging_flags_are_owned_without_changing_transfer_filters(self):
        options=['--max-age','12h','--stats','30s','--quiet','--log-level=ERROR','--progress','--transfers','4']
        self.assertEqual(live_stats_options(options),options)
        STATE.live_config = LiveConfig(enabled=True,host='fake')
        normalized=live_stats_options(options)
        self.assertEqual(normalized[:len(options)],options)
        for flag in ['--use-json-log','--stats=1s','--stats-log-level=NOTICE','--progress=false','--quiet=false','--log-file=','--syslog=false']:
            self.assertIn(flag,normalized)
        self.assertEqual(options[3],'30s')
        literal=['--include','--stats','--transfers','4']
        self.assertEqual(live_stats_options(literal)[:len(literal)],literal)

    def test_redirecting_logs_is_rejected_before_lock_or_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json'
            for flag in ['--log-file=/tmp/example.log','--syslog','--syslog-facility=DAEMON']:
                path.write_text(json.dumps({'live':{'enabled':True,'host':'fake'},
                    'upload_directories':[{'local_path':folder,'remote_path':'fake:','copy_options':[flag]}]}))
                with self.assertRaisesRegex(ValueError,'redirects transfer stats'):
                    load_config(str(path))
            self.assertFalse((Path(folder)/'job.lock').exists())

    def test_stream_stats_are_not_failure_diagnostics_and_failure_keeps_partial_bytes(self):
        script='import sys\nprint('+repr(sample(1234,500))+',flush=True)\nprint("genuine diagnostic",file=sys.stderr,flush=True)\nsys.exit(7)'
        code,text=run_command_streamed([sys.executable,'-c',script],1,'remote:0')
        self.assertEqual(code,7)
        self.assertIn('genuine diagnostic',text)
        self.assertNotIn('Synthetic stats',text)
        self.assertNotIn('Synthetic stats',STATE.stderr_tail)
        row=STATE.live_preview.snapshot()['details']['remote_jobs'][0]
        self.assertEqual(row['transferred_bytes'],1234)
        self.assertEqual(row['bytes_per_second'],0)

    def test_monitor_failure_does_not_break_stats_reader_or_stream_exit(self):
        script='print('+repr(sample(42,10))+',flush=True)'
        with patch.object(STATE.live_preview,'update',side_effect=RuntimeError('offline monitor')):
            code,_=run_command_streamed([sys.executable,'-c',script],1,'remote:0')
        self.assertEqual(code,0)
        self.assertEqual(STATE.run_results['remote:0'].transferred_bytes,42)

    def test_heartbeat_refreshes_delayed_speed_without_new_worker_output(self):
        STATE.live_preview.close()
        STATE.live_preview=LivePreview(LiveConfig(enabled=True,host='fake',heartbeat_seconds=.02,min_publish_interval=.01),
            name='Heartbeat',job='rclone-multithreaded-upload',before_publish=refresh_live)
        delayed=Event()
        def capture(messages):
            for item in messages:
                packet=item['payload']
                if packet.get('bytes_per_second') is None and packet.get('transferred_bytes')==123:
                    delayed.set()
            return True
        start_transfer('remote:0')
        record_transfer_stats('remote:0',sample(123,10))
        STATE.run_results['remote:0'].transfer_sample_time=time.monotonic()-10
        with patch.object(STATE.live_preview,'_send',side_effect=capture):
            STATE.live_preview.start()
            self.assertTrue(delayed.wait(2))
            STATE.live_preview.close()

    def test_card_moves_combined_metrics_to_top_and_shows_each_destination(self):
        for i in range(3):
            start_transfer('remote:'+str(i))
            record_transfer_stats('remote:'+str(i),sample((i+1)*MIB,(i+1)*MIB))
        snapshot=STATE.live_preview.snapshot();snapshot['runtime_seconds']=10
        card=manufacturer_card.CardTests();card.setUp();card.add('metrics',live=snapshot,renamed=True)
        text=card.render()
        self.assertIn('**Transferred:** 6.00 MiB (all destinations)',text)
        self.assertIn('**Combined speed:** 6.00 MiB/s',text)
        self.assertIn('**Combined average speed:** 614.40 KiB/s',text)
        self.assertLess(text.index('**Combined average speed:**'),text.index('| Destination |'))
        self.assertNotIn('**Combined speed:**',text[text.index('#### OneDrive'):])
        self.assertNotIn('**Current speed:**',text)
        for i,name in enumerate(['OneDrive','GDrive','Mega'],1):
            self.assertIn(f'| {i:.2f} MiB | {i:.2f} MiB/s | PENDING |',text)
            self.assertIn('#### '+name,text)

    def test_real_cli_three_streams_publish_bytes_and_rates_through_fake_paho(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source';source.mkdir()
            sink=root/'packets.jsonl'
            fake=root/'rclone'
            fake.write_text('#!'+sys.executable+'\n'+'''import os,sys,json,time
from pathlib import Path
if sys.argv[1]=='lsjson': print('[]')
elif sys.argv[1]=='copy':
    assert '--stats=1s' in sys.argv and '--use-json-log' in sys.argv
    i=int(sys.argv[3].split(':')[1]);root=Path(os.environ['STATS_FIXTURE'])
    def emit(factor):
        print(json.dumps({'stats':{'bytes':i*1048576*factor,'speed':i*1048576,'elapsedTime':factor}}),flush=True)
    emit(1)
    (root/('ready'+str(i))).write_text('ready')
    until=time.monotonic()+10
    while not (root/('release'+str(i))).exists():
        if time.monotonic()>until: sys.exit(4)
        time.sleep(.01)
    emit(2)
''');fake.chmod(0o755)
            package=root/'paho'/'mqtt';package.mkdir(parents=True)
            (root/'paho'/'__init__.py').write_text('');(package/'__init__.py').write_text('')
            (package/'publish.py').write_text('import os,json\ndef multiple(messages,**kwargs):\n'
                '    fd=os.open(os.environ["STATS_SINK"],os.O_CREAT|os.O_APPEND|os.O_WRONLY,0o600)\n'
                '    os.write(fd,(json.dumps(messages)+"\\n").encode());os.close(fd)\n')
            cfg={'sleep_after_step':0,'lock_file':str(root/'job.lock'),'delete_list_dir':str(root/'lists'),
                 'thread_limits':{'upload_threads':3,'cleanup_threads':3},
                 'live':{'enabled':True,'host':'fake','min_publish_interval':.02,'heartbeat_seconds':.1,'final_wait':1,'timeout':1},
                 'upload_directories':[{'name':name,'local_path':str(source),'remote_path':'fake:'+str(i),
                    'delete_old_files':False,'delete_excess_files':False,'empty_trash':False}
                    for i,name in enumerate(['OneDrive','GDrive','Mega'],1)]}
            config=root/'config.json';config.write_text(json.dumps(cfg))
            env=dict(os.environ,PATH=str(root)+os.pathsep+os.environ['PATH'],PYTHONPATH=str(root),
                     STATS_FIXTURE=str(root),STATS_SINK=str(sink),PYTHONDONTWRITEBYTECODE='1')
            output=(root/'output.txt').open('w+')
            app=subprocess.Popen([sys.executable,'-B',str(ROOT/'rclone-multithreaded-upload.py'),'--config',str(config)],
                                 stdout=output,stderr=subprocess.STDOUT,env=env)
            def packets():
                rows=[]
                for line in sink.read_text().splitlines() if sink.exists() else []:
                    try: batch=json.loads(line)
                    except ValueError:continue
                    rows.extend(json.loads(item['payload']) for item in batch if 'schema_version' in item['payload'])
                return rows
            try:
                self.assertTrue(wait_for(lambda:any(p.get('transferred_bytes')==6*MIB and p.get('bytes_per_second')==6*MIB for p in packets())),(root/'output.txt').read_text())
                (root/'release1').touch()
                self.assertTrue(wait_for(lambda:any(p.get('transferred_bytes')==7*MIB and p.get('bytes_per_second')==5*MIB for p in packets())))
                (root/'release2').touch();(root/'release3').touch()
                self.assertEqual(app.wait(timeout=8),0,(root/'output.txt').read_text())
                final=[p for p in packets() if p.get('status')=='success'][-1]
                self.assertEqual(final['transferred_bytes'],12*MIB)
                self.assertEqual(final['bytes_per_second'],0)
                self.assertEqual([r['transferred_bytes'] for r in final['details']['remote_jobs']],[2*MIB,4*MIB,6*MIB])
                self.assertEqual(final['completed'],final['total'])
                self.assertTrue(all(r['average_bytes_per_second']>0 for r in final['details']['remote_jobs']))
                self.assertFalse((root/'job.lock').exists())
            finally:
                if app.poll() is None:
                    app.terminate()
                    app.wait(timeout=8)
                output.close()
