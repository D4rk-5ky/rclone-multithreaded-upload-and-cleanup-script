"""Top-level application orchestration."""

import atexit
import signal

from .cli import parse_cli_args
from .config import load_config
from .lock import acquire_lock, release_lock, signal_handler, sleep_after_step
from .output import print_error, print_step
from .phases import (
    run_cleanup_phase,
    run_final_verification,
    run_remote_quota_phase,
    run_reservation_and_upload_phase,
    run_trash_cleanup_phase,
)
from .results import (
    finalize_stage_for_all,
    initialize_run_results,
    mark_pending_stages_skipped,
    print_final_run_result,
)
from .summary import print_startup_summary
from .targets import build_cleanup_directories


def main() -> int:
    args = parse_cli_args()

    try:
        load_config(args.config)
    except Exception as error:
        print_error(f"Failed loading config: {error}")
        return 1

    cleanup_directories = build_cleanup_directories()
    initialize_run_results()

    if not args.validate_config:
        atexit.register(release_lock)
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        acquire_lock()

    print_startup_summary(cleanup_directories)

    if args.validate_config:
        print_step(
            "Config validation successful; no lock file, cleanup, local sizing, "
            "trash cleanup, or upload was started"
        )
        return 0

    print_step("Lockfile doesn't exist, script is starting")

    if not run_cleanup_phase(cleanup_directories, "PRE-UPLOAD CLEANUP"):
        print_error("One or more pre-upload cleanup_rules jobs failed")
        mark_pending_stages_skipped()
        print_final_run_result(1)
        return 1

    if not run_trash_cleanup_phase("PRE-UPLOAD TRASH CLEANUP"):
        print_error("One or more pre-upload trash cleanup jobs failed")
        mark_pending_stages_skipped()
        print_final_run_result(1)
        return 1

    sleep_after_step()

    upload_success = run_reservation_and_upload_phase()

    post_cleanup_success = run_cleanup_phase(
        cleanup_directories,
        "POST-UPLOAD CLEANUP",
    )
    post_quota_success = run_remote_quota_phase("POST-UPLOAD QUOTA CLEANUP")
    post_trash_success = run_trash_cleanup_phase("POST-UPLOAD TRASH CLEANUP")
    finalize_stage_for_all("post_cleanup")

    verification_success = run_final_verification(cleanup_directories)

    if not post_cleanup_success:
        print_error("One or more post-upload cleanup_rules jobs failed")
    if not post_quota_success:
        print_error("One or more post-upload max_total_size cleanup jobs failed")
    if not post_trash_success:
        print_error("One or more post-upload trash cleanup jobs failed")
    if not verification_success:
        print_error("Final cleanup/quota verification failed")
    if not upload_success:
        print_error(
            "One or more reservation/upload pipelines failed. Post-upload cleanup and "
            "final verification were still run because a failed upload may have "
            "transferred partial data."
        )

    overall_success = all(
        [
            upload_success,
            post_cleanup_success,
            post_quota_success,
            post_trash_success,
            verification_success,
        ]
    )
    exit_code = 0 if overall_success else 1

    if overall_success:
        print_step("Upload, post-upload cleanup, and final quota verification successful")

    print_final_run_result(exit_code)
    return exit_code
