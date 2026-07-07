"""Startup configuration and snapshot-pipeline summary."""

from .models import CleanupTarget
from .output import OUTPUT_LOCK, OUTPUT_SEPARATOR
from .rclone_backend import get_delete_mode_text
from .state import STATE
from .utils import format_bytes


def print_startup_summary(cleanup_directories: list[CleanupTarget]):
    with OUTPUT_LOCK:
        print()
        print(OUTPUT_SEPARATOR)
        print("STARTUP SUMMARY")
        print(OUTPUT_SEPARATOR)
        print(f"Config file: {STATE.config_path}")
        print()
        print("Execution order per remote:")
        print("  1. PRE-UPLOAD recursive lsjson snapshot")
        print("  2. Plan age cleanup + max_files/max_size + upload reservation in Python")
        print("  3. Execute one combined delete plan per required delete mode")
        print("  4. Optional trash cleanup")
        print("  5. Upload immediately when this remote is ready")
        print("  6. POST-UPLOAD recursive lsjson snapshot")
        print("  7. Plan age/rule/max_total_size cleanup in Python")
        print("  8. Execute one combined delete plan per required delete mode")
        print("  9. Optional trash cleanup")
        print(" 10. FINAL recursive lsjson snapshot and verify every configured limit")
        print()
        print("Normal successful target: exactly 3 recursive remote lsjson snapshots per remote.")
        print("Delete, upload, backend trash cleanup, and backend API pagination are separate rclone work.")
        print()
        print("Thread limits:")
        print(f"  Upload/pipeline jobs    : {STATE.upload_threads}")
        print(f"  Cleanup jobs            : {STATE.cleanup_threads}")
        print(f"  Remote quota jobs       : {STATE.remote_quota_cleanup_threads}")
        print(f"  Trash cleanup jobs      : {STATE.trash_cleanup_threads}")
        print()
        print("Global cleanup/reservation settings:")
        print(f"  Delete minimum age      : {STATE.delete_min_age}")
        print(
            "  Reservation headroom   : "
            f"{format_bytes(STATE.reservation_safety_headroom_bytes)}"
        )
        print(f"  Lock file               : {STATE.lock_file}")
        print(f"  Delete-list directory   : {STATE.delete_list_dir}")
        print(f"  Sleep after step        : {STATE.sleep_after_step}s")

        print()
        print(f"Upload destinations: {len(STATE.upload_directories)}")
        for index, upload in enumerate(STATE.upload_directories, start=1):
            print()
            print(f"UPLOAD DESTINATION {index}")
            print(f"  Name                  : {upload.name or '(not set)'}")
            print(f"  Local path            : {upload.local_path}")
            print(f"  Remote path           : {upload.remote_path}")
            print(f"  Upload command        : {upload.upload_command}")
            print(f"  Delete old files      : {upload.delete_old_files}")
            print(f"  Delete excess files   : {upload.delete_excess_files}")
            print(f"  Remote max total      : {upload.max_total_size}")
            print(f"  Delete mode           : {get_delete_mode_text(upload)}")
            print(f"  Empty trash           : {upload.empty_trash}")
            print(f"  Buffer size           : {upload.buffer_size}")
            print(f"  Upload options        : {upload.copy_options}")
            print(f"  Cleanup rules         : {len(upload.cleanup_rules)}")
            for rule_index, rule in enumerate(upload.cleanup_rules, start=1):
                print(f"    Rule {rule_index}")
                print(f"      Path                : {rule.path}")
                print(f"      Max files           : {rule.max_files}")
                print(f"      Max size            : {rule.max_size}")
                print(f"      Delete old override : {rule.delete_old_files}")
                print(f"      Delete excess over. : {rule.delete_excess_files}")
                print(f"      Delete trash over.  : {rule.delete_to_trash}")

        print()
        print(f"Generated cleanup targets: {len(cleanup_directories)}")
        for index, target in enumerate(cleanup_directories, start=1):
            print(f"  Target {index}: {target.path}")
            print(f"    Owner remote       : {target.owner_remote_path}")
            print(f"    Max files          : {target.max_files}")
            print(f"    Max size           : {target.max_size}")
            print(f"    Delete old files   : {target.delete_old_files}")
            print(f"    Delete excess files: {target.delete_excess_files}")
            print(f"    Delete mode        : {get_delete_mode_text(target)}")

        print(OUTPUT_SEPARATOR)
        print(flush=True)
