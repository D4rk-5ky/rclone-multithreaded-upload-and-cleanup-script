"""Single-instance lock and stop-signal handling."""

import os
import sys

from .output import print_error
from .state import STATE
from .process_lifecycle import request_cancellation, notify_supervisor


def acquire_lock():
    """Create the configured lock file atomically."""
    try:
        STATE.lock_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            STATE.lock_file,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o644,
        )
        with os.fdopen(fd, "w") as lock_file:
            lock_file.write(f"pid={os.getpid()}\n")
        STATE.lock_created = True
    except FileExistsError:
        print("Lock file exists, exiting.")
        sys.exit(0)
    except Exception as error:
        print_error(f"Failed to create lock file: {error}")
        sys.exit(1)


def release_lock():
    if STATE.lock_created and STATE.lock_file.exists():
        try:
            STATE.lock_file.unlink()
            STATE.lock_created = False
        except Exception as error:
            print_error(f"Failed to remove lock file: {error}")


def signal_handler(signum, frame):
    del frame
    first = request_cancellation()
    # main finalizes both reporting channels before releasing the exclusive lock.
    if first:
        notify_supervisor(signum)
        sys.exit(128 + signum)
