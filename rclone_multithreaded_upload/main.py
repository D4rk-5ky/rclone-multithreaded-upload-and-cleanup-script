"""Top-level application orchestration."""

import atexit
import signal

from .cli import parse_cli_args
from .config import load_config
from .ha_live_preview import run_worker_if_requested as live_worker
from .lock import acquire_lock, release_lock, signal_handler
from .mqtt import build_result_payload, publish_final_result
from .mqtt_status import run_worker_if_requested as completion_worker
from .output import print_error, print_step
from .phases import (
    run_final_verification,
    run_post_upload_cleanup_phase,
    run_reservation_and_upload_phase,
)
from .reporting import finish_live, sanitize, start_reporting, update_live
from .results import initialize_run_results, mark_pending_stages_skipped, print_final_run_result
from .state import STATE
from .summary import print_startup_summary
from .targets import build_cleanup_directories


def main() -> int:
    completion_worker()
    live_worker()
    args = parse_cli_args()
    try:
        load_config(args.config)
    except Exception as error:
        print_error(f"Failed loading config: {error}")
        return 1

    cleanup_directories = build_cleanup_directories()
    initialize_run_results()
    if args.validate_config:
        print_startup_summary(cleanup_directories)
        print_step("Config validation successful; no lock file, remote snapshot, cleanup, "
                   "local sizing, trash cleanup, or upload was started")
        return 0

    atexit.register(release_lock)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    acquire_lock()  # An already-running invocation must not emit a new run result.
    exit_code, interrupted = 1, False
    try:
        start_reporting()
        print_startup_summary(cleanup_directories)
        print_step("Lockfile doesn't exist, script is starting")
        update_live(phase="reservation_and_upload")
        upload_success = run_reservation_and_upload_phase(cleanup_directories)
        update_live(phase="post_upload_cleanup")
        post_cleanup_success = run_post_upload_cleanup_phase(cleanup_directories)
        update_live(phase="final_verification")
        verification_success = run_final_verification(cleanup_directories)
        if not post_cleanup_success:
            print_error("One or more post-upload combined cleanup jobs failed")
        if not verification_success:
            print_error("Final cleanup/quota verification failed")
        if not upload_success:
            print_error("One or more pre-upload preparation, trash-cleanup, or upload jobs failed. "
                        "Other independent remote jobs continued, and post-upload cleanup/final "
                        "verification were still run because an upload may have transferred partial data.")
        exit_code = 0 if all((upload_success, post_cleanup_success, verification_success)) else 1
        if exit_code == 0:
            print_step("Pre-upload preparation, trash cleanup, upload, post-upload cleanup, "
                       "and final verification successful")
    except KeyboardInterrupt:
        exit_code, interrupted = 130, True
        STATE.run_error = "Interrupted by user"
    except SystemExit as error:
        exit_code = error.code if type(error.code) is int else (0 if error.code is None else 1)
        interrupted = exit_code in (130, 143)
        STATE.run_error = f"Run exited early with code {exit_code}"
    except Exception as error:
        exit_code = 1
        STATE.run_error = sanitize(f"Unexpected job error: {error}")
        print_error(STATE.run_error)
    finally:
        try:
            if exit_code != 0:
                mark_pending_stages_skipped()
            print_final_run_result(exit_code)
            error_text, warning = STATE.run_error, False
            try:
                payload = build_result_payload(exit_code)
                error_text, warning = payload["error"], payload["warning"]
            except Exception:
                print_error("Could not build completion report; preserving the rclone job exit code")
            try:
                finish_live(exit_code, error=error_text, interrupted=interrupted, warning=warning)
            except Exception:
                print_error("Could not finalize HA live preview; preserving the rclone job exit code")
            publish_final_result(exit_code)
        finally:
            STATE.live_preview = None
            release_lock()
    return exit_code
