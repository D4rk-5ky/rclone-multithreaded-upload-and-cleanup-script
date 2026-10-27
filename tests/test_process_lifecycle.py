"""Offline real-CLI process-tree termination checks with harmless subprocess fakes."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch
from concurrent.futures import CancelledError
from rclone_multithreaded_upload import process_lifecycle as life
from rclone_multithreaded_upload.commands import run_command
from rclone_multithreaded_upload.upload import run_command_streamed
ROOT = Path(__file__).resolve().parents[1]


def running(pid):
    """Treat a zombie awaiting the system reaper as stopped, never as executing work."""
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
    except FileNotFoundError:
        return False


def wait_for(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate(): return True
        time.sleep(0.02)
    return False


class CancellationTests(unittest.TestCase):
    def tearDown(self):
        life.reset_cancellation()

    def test_no_new_rclone_commands_start_after_cancellation(self):
        life.request_cancellation()
        with patch('subprocess.run') as run, patch('subprocess.Popen') as popen:
            with self.assertRaises(KeyboardInterrupt): run_command(['rclone', 'lsjson', 'fake:'])
            with self.assertRaises(KeyboardInterrupt):
                run_command_streamed(['rclone', 'copy', '/fake', 'fake:'], 1, 'fake:')
            run.assert_not_called(); popen.assert_not_called()

    def test_configured_worker_wait_is_interruptible(self):
        finished = Event()
        def waiting():
            try: life.wait_or_cancel(120)
            except KeyboardInterrupt: finished.set()
        worker = Thread(target=waiting)
        worker.start()
        try:
            life.request_cancellation()
            self.assertTrue(finished.wait(1))
        finally: worker.join(timeout=1)
        self.assertFalse(worker.is_alive())

    def test_interrupted_pool_cancels_queued_jobs_and_joins_running_worker(self):
        entered, release = Event(), Event()
        called = []
        def busy():
            entered.set(); release.wait(2)
        with self.assertRaises(KeyboardInterrupt):
            with life.StopAwareExecutor(max_workers=1) as pool:
                first = pool.submit(busy)
                self.assertTrue(entered.wait(1))
                queued = [pool.submit(called.append, i) for i in range(10)]
                life.request_cancellation(); release.set()
                raise KeyboardInterrupt()
        first.result()
        self.assertFalse(called)
        for future in queued:
            try: future.result()
            except (CancelledError, KeyboardInterrupt): pass
            else: self.fail('Queued work ran after cancellation')


@unittest.skipUnless(sys.platform.startswith('linux'), 'Real process-group checks use Linux /proc')
class ProcessTreeTests(unittest.TestCase):
    def stalled_job(self, repeat=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            marker = root / 'job.pid'
            script = root / 'stalled.py'
            script.write_text('import os,signal,sys,time\nfrom pathlib import Path\n'
                'from rclone_multithreaded_upload.process_lifecycle import supervised_main\n'
                'def body():\n'
                '    signal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
                '    Path(sys.argv[1]).write_text(str(os.getpid()))\n'
                '    while True: time.sleep(1)\n'
                'sys.exit(supervised_main(body))\n')
            app = subprocess.Popen([sys.executable, '-B', str(script), str(marker)],
                env=dict(os.environ, PYTHONPATH=str(ROOT)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                self.assertTrue(wait_for(marker.exists))
                job = int(marker.read_text())
                started = time.monotonic()
                app.terminate()
                if repeat:
                    time.sleep(0.1)
                    app.send_signal(signal.SIGINT)
                self.assertEqual(app.wait(timeout=8), 143)
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, 2 if repeat else 7)
                if not repeat:
                    self.assertGreaterEqual(elapsed, life.SHUTDOWN_GRACE_SECONDS - 0.2)
                self.assertFalse(running(job))
            finally:
                if app.poll() is None:
                    app.kill()
                    app.wait(timeout=2)

    def test_unresponsive_job_is_forced_out_at_shutdown_deadline(self):
        self.stalled_job()

    def test_second_stop_signal_forces_immediate_group_cleanup(self):
        self.stalled_job(repeat=True)

    def run_case(self, signum, *, target='supervisor', stubborn=False, stage='upload', mqtt=False, normal=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source'; source.mkdir()
            records = root / 'children.jsonl'
            helper = root / 'helper.py'
            helper.write_text('import signal,time\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
                              'signal.signal(signal.SIGINT,signal.SIG_IGN)\ntime.sleep(120)\n')
            (root/'fixture.py').write_text('''import os,json,subprocess,sys,time,signal
def hold(kind):
    pipes={'stdin':subprocess.DEVNULL,'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL} if os.environ.get('NORMAL')=='1' else {}
    child=subprocess.Popen([sys.executable,os.environ['CHILD_HELPER']],**pipes)
    if os.environ.get('STUBBORN')=='1':
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        signal.signal(signal.SIGINT,signal.SIG_IGN)
    row={'kind':kind,'pid':os.getpid(),'child':child.pid,'job':os.getsid(0)}
    fd=os.open(os.environ['CHILD_RECORDS'],os.O_CREAT|os.O_APPEND|os.O_WRONLY,0o600)
    os.write(fd,(json.dumps(row)+'\\n').encode());os.close(fd)
    if os.environ.get('NORMAL')=='1': return
    time.sleep(120)
''')
            rclone=root/'rclone'
            rclone.write_text('#!'+sys.executable+'\nimport os,sys\nfrom fixture import hold\n'
                "if sys.argv[1]=='lsjson':\n"
                "    if os.environ.get('HELD_STAGE')=='snapshot': hold('snapshot')\n"
                "    print('[]')\n"
                "elif sys.argv[1] in ('copy','move','sync'): hold('upload')\n"
                "elif sys.argv[1]=='config': print('{}')\n")
            rclone.chmod(0o755)
            package=root/'paho'/'mqtt';package.mkdir(parents=True)
            (root/'paho'/'__init__.py').write_text('');(package/'__init__.py').write_text('')
            (package/'publish.py').write_text('from fixture import hold\n'
                'def multiple(*a,**k): hold("mqtt-live")\ndef single(*a,**k): hold("mqtt-completion")\n')
            cfg={'lock_file':str(root/'job.lock'),'delete_list_dir':str(root/'lists'),
                 'sleep_after_step':0,'thread_limits':{'upload_threads':2,'cleanup_threads':2},
                 'upload_directories':[{'name':name,'local_path':str(source),'remote_path':f'fake:{name}',
                 'cleanup_rules':[],'empty_trash':False,'delete_old_files':False,'delete_excess_files':False}
                 for name in ['OneDrive','GDrive','Mega']]}
            if mqtt:
                cfg['live']={'enabled':True,'host':'fake','timeout':30,'final_wait':0.05,'expire_after':90}
                cfg['completion']={'enabled':True,'host':'fake','timeout':0.3}
            config=root/'config.json';config.write_text(json.dumps(cfg))
            env=dict(os.environ,PATH=str(root)+os.pathsep+os.environ['PATH'],PYTHONPATH=str(root),
                PYTHONDONTWRITEBYTECODE='1',CHILD_HELPER=str(helper),CHILD_RECORDS=str(records),
                STUBBORN='1' if stubborn else '0',HELD_STAGE=stage,NORMAL='1' if normal else '0')
            sentinel=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])
            output=(root/'output.txt').open('w+')
            app=subprocess.Popen([sys.executable,'-B',str(ROOT/'rclone-multithreaded-upload.py'),
                '--config',str(config)],env=env,stdout=output,stderr=subprocess.STDOUT)
            rows=[]
            def ready():
                nonlocal rows
                rows=[json.loads(line) for line in records.read_text().splitlines()] if records.exists() else []
                return (len([r for r in rows if r['kind']==stage]) >= (3 if normal else 2)
                        and (not mqtt or any(r['kind']=='mqtt-live' for r in rows)))
            try:
                self.assertTrue(wait_for(ready),'Fake operations did not start: '+(root/'output.txt').read_text())
                job=rows[0]['job']
                self.assertNotEqual(job,app.pid);self.assertNotEqual(job,os.getpgrp())
                started=time.monotonic()
                if not normal: os.kill(app.pid if target=='supervisor' else job,signum)
                code=app.wait(timeout=9)
                self.assertLess(time.monotonic()-started,8)
                rows=[json.loads(line) for line in records.read_text().splitlines()]
                pids={pid for row in rows for pid in [row['pid'],row['child'],row['job']]}
                self.assertTrue(wait_for(lambda:all(not running(p) for p in pids),2),
                    'Owned processes still running: '+str([p for p in pids if running(p)]))
                self.assertIsNone(sentinel.poll(),'Unrelated process was signalled')
                expected=0 if normal else -signal.SIGKILL if signum==signal.SIGKILL and target=='supervisor' else 128+signum
                self.assertEqual(code,expected)
                if not normal and not stubborn and signum!=signal.SIGKILL:
                    self.assertFalse((root/'job.lock').exists())
                    self.assertLessEqual(len([r for r in rows if r['kind']==stage]),2,
                        'A queued destination started after cancellation')
            finally:
                if app.poll() is None: app.kill();app.wait(timeout=2)
                if rows:
                    try: os.killpg(rows[0]['job'],signal.SIGKILL)
                    except ProcessLookupError: pass
                sentinel.terminate();sentinel.wait(timeout=2);output.close()

    def test_sigint_stops_uploads_descendants_and_queued_destination(self):
        self.run_case(signal.SIGINT)

    def test_sigterm_stops_snapshot_and_live_mqtt_process_trees(self):
        self.run_case(signal.SIGTERM,stage='snapshot',mqtt=True)

    def test_sigkill_of_supervisor_triggers_job_watchdog(self):
        self.run_case(signal.SIGKILL,mqtt=True)

    def test_sigkill_of_inner_job_is_cleaned_by_supervisor(self):
        self.run_case(signal.SIGKILL,target='job',mqtt=True)

    def test_sigterm_directly_to_inner_job_escalates_stubborn_workers(self):
        self.run_case(signal.SIGTERM,target='job',stubborn=True)

    def test_normal_exit_also_removes_lingering_descendants(self):
        self.run_case(signal.SIGTERM,normal=True)
