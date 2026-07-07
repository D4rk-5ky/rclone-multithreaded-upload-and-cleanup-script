"""Single-instance lock handling and configured delays."""

import os
import sys
import time

from .output import print_error, print_step
from .state import STATE


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
    print_error(f"Received signal {signum}, exiting.")
    release_lock()
    sys.exit(128 + signum)


def sleep_after_step():
    print_step(f"Sleeping for {STATE.sleep_after_step}s")
    time.sleep(STATE.sleep_after_step)
