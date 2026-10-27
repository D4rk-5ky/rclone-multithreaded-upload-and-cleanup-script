"""Rclone lifecycle adapter for the two independent template channels."""

import re
import uuid

from . import VERSION
from .ha_live_preview import LivePreview
from .output import print_error
from .state import STATE

STAGES = ("reservation", "upload", "post_cleanup", "final_quota")
JOB = "rclone-multithreaded-upload"


def sanitize(text: str) -> str:
    """Remove configured MQTT passwords and common credential flags from reports."""
    for secret in (STATE.mqtt.password, STATE.live_config.password):
        if secret:
            text = text.replace(secret, "[redacted]")
    return re.sub(
        r"(--[\w-]*(?:password|passwd|token|secret|access-key)[\w-]*(?:=|\s+))([^\s]+)",
        r"\1[redacted]", text, flags=re.IGNORECASE,
    )


def append_diagnostics(text: str) -> None:
    if not text:
        return
    text = sanitize(text)
    with STATE.reporting_lock:
        STATE.stderr_tail = (STATE.stderr_tail + text)[-4000:]
        if STATE.live_preview is not None:
            try:
                STATE.live_preview.append_stderr(text)
            except Exception:
                print_error("HA live preview diagnostic update failed; continuing the rclone job")


def start_reporting() -> None:
    STATE.run_id = str(uuid.uuid4())
    STATE.run_error = STATE.stderr_tail = ""
    STATE.live_preview = None
    if STATE.live_config.enabled:
        try:
            STATE.live_preview = LivePreview(
                STATE.live_config, name=STATE.script_name, job=JOB,
                version=VERSION, run_id=STATE.run_id,
            )
            STATE.live_preview.start()
            refresh_live()
        except Exception:
            if STATE.live_preview is not None:
                try:
                    STATE.live_preview.close()
                except Exception:
                    pass
            STATE.live_preview = None
            print_error("HA live preview could not start; continuing the rclone job")


def update_live(**values) -> None:
    if STATE.live_preview is not None:
        try:
            STATE.live_preview.update(**values)
        except Exception:
            print_error("HA live preview update failed; continuing the rclone job")


def refresh_live(current_item: str | None = None) -> None:
    """Count settled remote stages, and classify each remote from actual results.

    Holding the adapter lock across snapshot/update prevents concurrent workers
    publishing an older counter after a newer one. No network I/O occurs here.
    """
    if STATE.live_preview is None:
        return
    try:
        with STATE.reporting_lock:
            completed = 0
            groups = {"successful_datasets": [], "failed_datasets": [], "skipped_datasets": []}
            with STATE.run_results_lock:
                total = len(STATE.run_results) * len(STAGES)
                sources = {upload.remote_path: upload.local_path for upload in STATE.upload_directories}
                for result in STATE.run_results.values():
                    stages = [getattr(result, stage) for stage in STAGES]
                    statuses = [stage.status for stage in stages]
                    completed += sum(status != "PENDING" for status in statuses)
                    row = {"source": sources[result.remote_path], "destination": result.remote_path,
                           "name": result.name}
                    if "FAILED" in statuses:
                        row["reason"] = sanitize("\n".join(error for stage in stages for error in stage.errors))
                        groups["failed_datasets"].append(row)
                    elif all(status == "SUCCESS" for status in statuses):
                        groups["successful_datasets"].append(row)
                    elif "PENDING" not in statuses:
                        row["reason"] = "One or more remote stages were skipped"
                        groups["skipped_datasets"].append(row)
            values = {"completed": completed, "total": total, **groups}
            if current_item is not None:
                values["current_item"] = current_item
            STATE.live_preview.update(**values)
    except Exception:
        print_error("HA live preview update failed; continuing the rclone job")


def finish_live(exit_code: int, *, error: str, interrupted: bool, warning: bool = False) -> None:
    if STATE.live_preview is None:
        return
    try:
        refresh_live("")
        STATE.live_preview.finish(exit_code, error=error, stderr=STATE.stderr_tail,
                                  interrupted=interrupted, warning=warning)
    except Exception:
        print_error("HA live preview final update failed; continuing with the job result")
    finally:
        STATE.live_preview.close()
