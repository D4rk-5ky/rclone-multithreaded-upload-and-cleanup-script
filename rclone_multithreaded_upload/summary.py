"""Startup configuration and barriered execution summary."""

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
        print(f"Script name: {STATE.script_name}")
        print(f"Config file: {STATE.config_path}")
        print()
        print("Execution order with global stage barriers:")
        print("  1. PRE-UPLOAD snapshot + cleanup/reservation planning for all remotes")
        print("  2. Wait until every preparation worker has finished")
        print("  3. Optional pre-upload trash cleanup for all eligible remotes")
        print("  4. Wait until every eligible trash-cleanup worker has finished")
        print("  5. Upload all remotes whose prerequisite stages succeeded")
        print("  6. Wait until every upload worker has finished")
        print("  7. POST-UPLOAD snapshot + cleanup planning/deletion for all remotes")
        print("  8. Wait until every post-upload cleanup worker has finished")
        print("  9. Optional post-upload trash cleanup for all eligible remotes")
        print(" 10. Wait until every post-upload trash worker has finished")
        print(" 11. FINAL recursive snapshot and verification for all remotes")
        print()
        print("Normal successful target: exactly 3 recursive remote lsjson snapshots per remote.")
        print("Delete, upload, backend trash cleanup, and backend API pagination are separate rclone work.")
        print()
        print("Thread limits:")
        print(f"  Upload jobs             : {STATE.upload_threads}")
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
        print("MQTT final-result publishing:")
        print(f"  Enabled                 : {STATE.mqtt.enabled}")
        if STATE.mqtt.enabled:
            print(f"  Broker                  : {STATE.mqtt.host}:{STATE.mqtt.port}")
            print(f"  Topic                   : {STATE.mqtt.topic}")
            print(f"  QoS                     : {STATE.mqtt.qos}")
            print(f"  Retain                  : {STATE.mqtt.retain}")
            print(f"  TLS                     : {STATE.mqtt.tls}")
            print(f"  Username configured     : {STATE.mqtt.username is not None}")
            print(f"  Client ID               : {STATE.mqtt.client_id or '(auto)'}")
            print(f"  Publish timeout         : {STATE.mqtt.publish_timeout}s")

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
