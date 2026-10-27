"""Isolate CLI jobs and stop their inherited process group when either owner dies.

No process-name matching or machine-wide killing is used. The CLI supervisor owns
one new session; rclone, MQTT workers and ordinary descendants inherit its group.
"""

from concurrent.futures import ThreadPoolExecutor
import ctypes
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
from threading import Event, Thread
import time

STOP_REQUESTED = Event()
INTERNAL_FLAG = "--internal-supervised-job"
SHUTDOWN_GRACE_SECONDS = 5.0
_JOB_NOTICE_FD = None


def reset_cancellation():
    """Start/finish an invocation without leaking cancellation into later calls."""
    STOP_REQUESTED.clear()


def request_cancellation():
    """Fence new work immediately; return whether this is the first stop request."""
    first = not STOP_REQUESTED.is_set()
    STOP_REQUESTED.set()
    return first


def notify_supervisor(signum):
    """Also supervise signals delivered directly to the inner job PID in htop."""
    if _JOB_NOTICE_FD is not None:
        try:
            os.write(_JOB_NOTICE_FD, bytes([signum]))
        except OSError:
            pass  # The owner watchdog handles a dead supervisor.


def check_cancelled():
    """Abort a queued operation before it can start more rclone work."""
    if STOP_REQUESTED.is_set():
        raise KeyboardInterrupt("Run is stopping")


def wait_or_cancel(seconds):
    """Keep configured waits interruptible, including waits inside pool threads."""
    if STOP_REQUESTED.wait(seconds):
        check_cancelled()


class StopAwareExecutor(ThreadPoolExecutor):
    """Preserve normal barriers, but cancel queued tasks when a stage is interrupted."""

    def submit(self, function, /, *args, **kwargs):
        def guarded():
            check_cancelled()
            return function(*args, **kwargs)
        check_cancelled()
        return super().submit(guarded)

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is not None or STOP_REQUESTED.is_set():
            request_cancellation()
            self.shutdown(wait=True, cancel_futures=True)
            return False
        return super().__exit__(exc_type, exc_value, traceback)


def _signal_group(group, signum):
    """Signal only the new process group created for this invocation."""
    try:
        os.killpg(group, signum)
    except ProcessLookupError:
        pass


def _watch_supervisor(fd):
    """EOF means the outer owner died, even through SIGKILL; stop our whole group."""
    try:
        while os.read(fd, 1):
            pass
    finally:
        # This watcher exists only in the isolated job session. Killing our own
        # group also stops ourselves, so no Python finally/notification is claimed.
        _signal_group(os.getpid(), signal.SIGKILL)


def _enable_subreaper():
    """On Linux adopt orphaned job descendants so the supervisor can reap them."""
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise OSError(ctypes.get_errno(), "Could not enable job child reaping")


def _kill_group_children(group):
    """On Linux force stopped-job children out of blocking pipes, retaining the job.

    Match the isolated group/session and use pidfds to avoid PID reuse races.
    The job can then finalize its interrupted report and release its own lock.
    """
    if not sys.platform.startswith("linux"):
        return  # The final process-group deadline still applies on other POSIX OSes.
    for path in Path("/proc").iterdir():
        if not path.name.isdecimal() or int(path.name) == group:
            continue
        fd = None
        try:
            fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) != group or int(fields[3]) != group:
                continue
            fd = os.pidfd_open(int(path.name))
            fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == group and int(fields[3]) == group:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
        except OSError:
            pass
        finally:
            if fd is not None:
                os.close(fd)


def _reap_group(group):
    """Reap adopted children after group cleanup without waiting indefinitely."""
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            pid, _ = os.waitpid(-group, os.WNOHANG)
        except ChildProcessError:
            return
        if not pid:
            time.sleep(0.01)


def supervised_main(main):
    """Run normal CLI work in an owned session with two-way death protection.

    Help/version/validation and template worker hooks retain their direct behavior.
    A normal stop gets five seconds for cleanup/final reporting before force-kill.
    """
    global _JOB_NOTICE_FD
    args = sys.argv[1:]
    if args[:1] == [INTERNAL_FLAG]:
        fd = int(args[1])
        if os.name != "posix" or os.getpgrp() != os.getpid():
            raise RuntimeError("Internal job must be an isolated session leader")
        os.fstat(fd)
        os.set_inheritable(fd, False)
        _JOB_NOTICE_FD = int(args[2])
        os.fstat(_JOB_NOTICE_FD)
        os.set_inheritable(_JOB_NOTICE_FD, False)
        os.set_blocking(_JOB_NOTICE_FD, False)
        sys.argv[1:] = args[3:]
        Thread(target=_watch_supervisor, args=(fd,), daemon=True,
               name="job-owner-watchdog").start()
        return main()
    if any(arg in {"--help", "-h", "--version", "--validate-config"} for arg in args) or not args:
        return main()
    if args in (["--mqtt-status-worker"], ["--ha-live-preview-worker"]):
        return main()
    if os.name != "posix":
        raise RuntimeError("Supervised jobs require POSIX process groups")

    _enable_subreaper()
    read_fd, write_fd = os.pipe()
    notice_read, notice_write = os.pipe()
    child = None
    received, deadline, children_deadline = None, None, None

    def forward(signum, frame):
        nonlocal received, deadline, children_deadline
        del frame
        repeated = received is not None
        received = received or signum
        deadline = time.monotonic() + SHUTDOWN_GRACE_SECONDS if not repeated else time.monotonic()
        children_deadline = time.monotonic() + 0.3
        if child is not None:
            _signal_group(child.pid, signal.SIGKILL if repeated else signum)

    watched = [signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT]
    old_handlers = {sig: signal.signal(sig, forward) for sig in watched}
    try:
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command += ["-B", str(Path(sys.argv[0]).resolve())]
        command += [INTERNAL_FLAG, str(read_fd), str(notice_write), *args]
        child = subprocess.Popen(command, pass_fds=(read_fd, notice_write), start_new_session=True)
        os.close(read_fd)
        read_fd = None
        os.close(notice_write)
        notice_write = None
        if received is not None:
            _signal_group(child.pid, received)
        while child.poll() is None:
            if select.select([notice_read], [], [], 0)[0]:
                notices = os.read(notice_read, 1024)
                if received is None and notices and notices[0] in watched:
                    forward(notices[0], None)
            if children_deadline is not None and time.monotonic() >= children_deadline:
                _kill_group_children(child.pid)
                children_deadline = None
            if deadline is not None and time.monotonic() >= deadline:
                _signal_group(child.pid, signal.SIGKILL)
            time.sleep(0.02)
        return 128 + received if received is not None else (
            child.returncode if child.returncode >= 0 else 128 - child.returncode)
    finally:
        if child is not None:
            _signal_group(child.pid, signal.SIGKILL)
            child.wait()
            _reap_group(child.pid)
        os.close(write_fd)
        if read_fd is not None:
            os.close(read_fd)
        os.close(notice_read)
        if notice_write is not None:
            os.close(notice_write)
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
