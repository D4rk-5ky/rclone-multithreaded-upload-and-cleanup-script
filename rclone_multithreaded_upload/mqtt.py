"""Rclone completion adapter using the supplied Syncerate JSON contract."""

from . import VERSION
from .mqtt_status import build_report, publish_report
from .output import print_error, print_step
from .reporting import JOB, sanitize
from .results import remote_result_label
from .progress import progress_snapshot_locked
from .state import STATE


def _stage_errors(result) -> list[dict[str, str]]:
    return [
        {"stage": stage_name, "error": sanitize(error)}
        for stage_name in ("reservation", "upload", "post_cleanup", "final_quota")
        for error in getattr(result, stage_name).errors
    ]


def build_result_payload(exit_code: int) -> dict:
    """Template fields plus the existing rclone per-remote diagnostics."""
    remotes, combined_errors, failed_remotes = [], [], []
    with STATE.run_results_lock:
        progress = progress_snapshot_locked()
        progress_by_remote = {row["destination"]: row for row in progress["remote_jobs"]}
        for result in STATE.run_results.values():
            result_label = remote_result_label(result)
            errors = _stage_errors(result)
            if result_label == "FAILED":
                failed_remotes.append(result.name)
            combined_errors.extend(f"{result.name} - {item['stage']}: {item['error']}" for item in errors)
            remotes.append({
                "name": result.name, "remote_path": result.remote_path,
                "status": result_label.lower(),
                "stages": {key: getattr(result, key).status for key in
                           ("reservation", "upload", "post_cleanup", "final_quota")},
                "errors": errors,
                "progress": progress_by_remote[result.remote_path],
            })
    if STATE.run_error:
        combined_errors.append(sanitize(STATE.run_error))
    if exit_code != 0 and not combined_errors:
        combined_errors.append(f"Run failed with exit code {exit_code}")
    return build_report(
        name=STATE.script_name, job=JOB, exit_code=exit_code,
        error_message="\n".join(combined_errors) if exit_code != 0 else "",
        stderr_text=STATE.stderr_tail, warning=any(row["status"] == "skipped" for row in remotes),
        dry_run=False, command=JOB, comment=STATE.comment, version=VERSION, run_id=STATE.run_id,
        extra={"event": "rclone_multithreaded_upload_result",
               "failed_remotes": failed_remotes, "remotes": remotes,
               "progress": {key: value for key, value in progress.items() if key != "remote_jobs"}},
    )


def publish_final_result(exit_code: int) -> bool | None:
    """Best-effort completion delivery, independent of live monitoring/job outcome."""
    if not STATE.completion.enabled:
        return None
    try:
        result = publish_report(STATE.completion, build_result_payload(exit_code))
        if result.status == "failed":
            print_error(f"MQTT completion delivery failed: {result.reason}")
            return False
        if result.status == "skipped":
            print_step(f"MQTT completion skipped: {result.reason}")
            return None
        print_step(f"MQTT result published for script '{STATE.script_name}' to {STATE.completion.topic}")
        return True
    except Exception:
        print_error("MQTT completion reporting failed; preserving the rclone job exit code")
        return False
