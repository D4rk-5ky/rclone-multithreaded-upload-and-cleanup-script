"""Observational per-destination step tracking; never decides what work may run."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from inspect import signature

from .models import StepRunResult
from .output import print_error
from .state import STATE

STAGES = ("reservation", "upload", "post_cleanup", "final_quota")
TERMINAL = {"SUCCESS", "FAILED", "SKIPPED", "INTERRUPTED"}
CURRENT_REMOTE = ContextVar("rclone_progress_remote", default=None)


def rule_step_id(prefix: str, path: str) -> str:
    """Keep repeated pre/post cleanup rules distinct using their owned full path."""
    return f"{prefix}_rule:{path}"


def plan_steps(upload, targets) -> dict[str, StepRunResult]:
    """Derive a fixed per-run denominator from the existing effective config."""
    steps = {}
    def add(key, label, stage, kind):
        steps[key] = StepRunResult(label=label, stage=stage, kind=kind)
    quota = upload.delete_excess_files and upload.max_total_size is not None
    add("pre_snapshot", "Pre-upload remote snapshot", "reservation", "snapshot")
    if quota:
        add("local_snapshot", "Acquire filtered local file snapshot", "reservation", "snapshot")
    for target in targets:
        add(rule_step_id("pre", target.path), f"Plan pre-upload cleanup: {target.path}", "reservation", "cleanup")
    if quota:
        add("reservation", "Reserve space for selected upload files", "reservation", "planning")
    add("pre_delete", "Execute pre-upload delete plan", "reservation", "cleanup")
    if upload.empty_trash:
        add("pre_trash", "Pre-upload trash cleanup", "reservation", "cleanup")
    if STATE.sleep_after_step > 0:
        add("pre_delay", "Wait before upload barrier", "reservation", "wait")
    add("upload", f"Upload files ({upload.upload_command})", "upload", "upload")
    add("post_snapshot", "Post-upload remote snapshot", "post_cleanup", "snapshot")
    for target in targets:
        add(rule_step_id("post", target.path), f"Plan post-upload cleanup: {target.path}", "post_cleanup", "cleanup")
    if quota:
        add("post_quota", "Plan post-upload total-size cleanup", "post_cleanup", "planning")
    add("post_delete", "Execute post-upload delete plan", "post_cleanup", "cleanup")
    if upload.empty_trash:
        add("post_trash", "Post-upload trash cleanup", "post_cleanup", "cleanup")
    add("final_snapshot", "Final remote snapshot", "final_quota", "snapshot")
    add("verification", "Verify all cleanup and remote limits", "final_quota", "verification")
    return steps


def set_step(remote_path: str, key: str, status: str, error: str = "") -> None:
    """Monotonic, locked observation update; missing fixtures and monitor errors are nonfatal."""
    try:
        with STATE.run_results_lock:
            result = STATE.run_results.get(remote_path)
            task = result.steps.get(key) if result else None
            if task is None or task.status in TERMINAL:
                return
            if status not in TERMINAL | {"RUNNING"}:
                raise ValueError("Invalid step observation")
            now = datetime.now(timezone.utc).isoformat()
            task.status = status
            if status == "RUNNING":
                task.started_at = task.started_at or now
            else:
                task.finished_at = now
            if error:
                task.error = error[-4000:]
        from .reporting import refresh_live
        refresh_live()
    except Exception:
        print_error("Progress observation failed; continuing the rclone job")


@contextmanager
def step(remote_path: str, key: str):
    """Observe actual execution and route captured diagnostics to this worker's destination."""
    token = CURRENT_REMOTE.set(remote_path)
    set_step(remote_path, key, "RUNNING")
    try:
        yield
    except BaseException as exc:
        interrupted = isinstance(exc, (KeyboardInterrupt, SystemExit))
        set_step(remote_path, key, "INTERRUPTED" if interrupted else "FAILED", str(exc) or type(exc).__name__)
        raise
    else:
        set_step(remote_path, key, "SUCCESS")
    finally:
        CURRENT_REMOTE.reset(token)


def observe_step(key, *, none_is_failure=False):
    """Wrap an existing operation without changing its arguments, return value or exception."""
    def decorate(function):
        parameters = signature(function)
        @wraps(function)
        def observed(*args, **kwargs):
            bound = parameters.bind(*args, **kwargs)
            bound.apply_defaults()
            remote = bound.arguments["upload"].remote_path
            task_key = key(bound.arguments) if callable(key) else key
            with step(remote, task_key):
                result = function(*args, **kwargs)
                if result is False or (none_is_failure and result is None):
                    set_step(remote, task_key, "FAILED", "Operation reported failure; see destination errors")
                return result
        return observed
    return decorate


def skip_step(remote_path: str, key: str, reason: str) -> None:
    """Settle a planned step honestly when no command/work is required."""
    set_step(remote_path, key, "SKIPPED", reason)


def settle_stage(remote_path: str, stage_name: str, *, failed: bool, reason: str) -> None:
    """Reflect the existing stage decision; block no work and infer no successful execution."""
    try:
        with STATE.run_results_lock:
            result = STATE.run_results.get(remote_path)
            if result is None:
                return
            tasks = [(key, task) for key, task in result.steps.items() if task.stage == stage_name]
            active = [key for key, task in tasks if task.status == "RUNNING"]
            pending = [key for key, task in tasks if task.status == "PENDING"]
        if failed:
            # An error between observed operations belongs to the stage, not
            # to an unstarted task. The destination snapshot carries it below.
            for key in active:
                set_step(remote_path, key, "FAILED", reason)
        for key in pending:
            skip_step(remote_path, key, reason)
    except Exception:
        print_error("Progress stage update failed; continuing the rclone job")


def finish_steps(exit_code: int, interrupted: bool = False) -> None:
    """Settle unresolved observations at shutdown without inventing completed work."""
    with STATE.run_results_lock:
        unresolved = [(remote, key, task.status) for remote, result in STATE.run_results.items()
                      for key, task in result.steps.items() if task.status not in TERMINAL]
    for remote, key, status in unresolved:
        if status == "RUNNING":
            set_step(remote, key, "INTERRUPTED" if interrupted else "FAILED",
                     f"Job ended during this step (exit code {exit_code})")
        else:
            skip_step(remote, key, "Step not reached before the job ended")


def remote_progress(result, source: str = "") -> dict:
    """Build a detached destination snapshot; caller holds the run-results lock."""
    from .reporting import sanitize
    tasks = [{"id": key, "label": task.label, "stage": task.stage, "kind": task.kind,
              "status": task.status, "error": sanitize(task.error),
              "started_at": task.started_at, "finished_at": task.finished_at}
             for key, task in result.steps.items()]
    rules = {}
    for task in tasks:
        prefix, separator, path = task["id"].partition("_rule:")
        if separator and prefix in {"pre", "post"}:
            rules.setdefault(path, {})[prefix] = task["status"]
    cleanup_jobs = [{"path": path, "completed": sum(status in TERMINAL for status in phases.values()),
                     "total": len(phases), "running": sum(status == "RUNNING" for status in phases.values()),
                     "failed": sum(status in {"FAILED", "INTERRUPTED"} for status in phases.values()),
                     "skipped": sum(status == "SKIPPED" for status in phases.values()),
                     "pre_status": phases.get("pre", "PENDING"), "post_status": phases.get("post", "PENDING")}
                    for path, phases in rules.items()]
    done = sum(row["status"] in TERMINAL for row in tasks)
    running = [row for row in tasks if row["status"] == "RUNNING"]
    failed = sum(row["status"] in {"FAILED", "INTERRUPTED"} for row in tasks)
    statuses = [getattr(result, stage).status for stage in STAGES]
    status = ("running" if running else "waiting" if done < len(tasks) and done else
              "pending" if done < len(tasks) else "failure" if failed or "FAILED" in statuses else
              "success" if all(item == "SUCCESS" for item in statuses) else "skipped")
    return {"name": result.name, "source": source, "destination": result.remote_path,
            "status": status, "completed": done, "total": len(tasks),
            "progress_percent": round(100 * done / len(tasks), 2) if tasks else None,
            "running": len(running), "succeeded": sum(row["status"] == "SUCCESS" for row in tasks),
            "failed": failed, "skipped": sum(row["status"] == "SKIPPED" for row in tasks),
            "pending": sum(row["status"] == "PENDING" for row in tasks),
            "active_steps": [row["label"] for row in running], "steps": tasks, "cleanup_jobs": cleanup_jobs,
            "stages": {stage: getattr(result, stage).status for stage in STAGES},
            "stderr": sanitize(result.stderr_tail),
            "error": sanitize("\n".join(error for stage in STAGES for error in getattr(result, stage).errors)),
            "has_failures": bool(failed or "FAILED" in statuses)}


def progress_snapshot_locked() -> dict:
    """Sum all destination denominators/counters from one locked observation snapshot."""
    sources = {upload.remote_path: upload.local_path for upload in STATE.upload_directories}
    rows = [remote_progress(result, sources.get(remote, "")) for remote, result in STATE.run_results.items()]
    return {"schema_version": 1, "unit": "steps", "destination_count": len(rows),
            "active_destination_count": sum(row["running"] > 0 for row in rows),
            "completed": sum(row["completed"] for row in rows), "total": sum(row["total"] for row in rows),
            "running": sum(row["running"] for row in rows), "failed": sum(row["failed"] for row in rows),
            "skipped": sum(row["skipped"] for row in rows),
            "completed_stages": sum(getattr(result, stage).status != "PENDING"
                                    for result in STATE.run_results.values() for stage in STAGES),
            "total_stages": len(rows) * len(STAGES), "remote_jobs": rows}
