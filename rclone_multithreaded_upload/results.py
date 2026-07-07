"""Per-remote stage accounting and final run summary."""

import re

from .models import RemoteRunResult, StageRunResult
from .output import OUTPUT_LOCK, OUTPUT_SEPARATOR
from .state import STATE


def initialize_run_results():
    """Create fresh per-remote stage results for the current run."""
    with STATE.run_results_lock:
        STATE.run_results.clear()
        for upload in STATE.upload_directories:
            display_name = upload.name or upload.remote_path
            STATE.run_results[upload.remote_path] = RemoteRunResult(
                name=display_name,
                remote_path=upload.remote_path,
            )


def get_stage_result(remote_path: str, stage_name: str) -> StageRunResult:
    result = STATE.run_results[remote_path]
    return getattr(result, stage_name)


def record_stage_success(remote_path: str, stage_name: str):
    with STATE.run_results_lock:
        stage = get_stage_result(remote_path, stage_name)
        if stage.status != "FAILED":
            stage.status = "SUCCESS"


def record_stage_failure(remote_path: str, stage_name: str, error: str):
    cleaned = (error or "Unknown failure").strip()
    with STATE.run_results_lock:
        stage = get_stage_result(remote_path, stage_name)
        stage.status = "FAILED"
        if cleaned and cleaned not in stage.errors:
            stage.errors.append(cleaned)


def record_stage_skipped(remote_path: str, stage_name: str):
    with STATE.run_results_lock:
        stage = get_stage_result(remote_path, stage_name)
        if stage.status == "PENDING":
            stage.status = "SKIPPED"


def finalize_stage_for_all(stage_name: str):
    with STATE.run_results_lock:
        for result in STATE.run_results.values():
            stage = getattr(result, stage_name)
            if stage.status == "PENDING":
                stage.status = "SUCCESS"


def mark_pending_stages_skipped():
    with STATE.run_results_lock:
        for result in STATE.run_results.values():
            for stage_name in (
                "reservation",
                "upload",
                "post_cleanup",
                "final_quota",
            ):
                stage = getattr(result, stage_name)
                if stage.status == "PENDING":
                    stage.status = "SKIPPED"


def command_error_summary(
    output: str,
    fallback: str = "No command error text was captured",
) -> str:
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    if not lines:
        return fallback

    error_pattern = re.compile(
        r"(?:error|failed|failure|fatal|quota|denied|forbidden|unauthorized|"
        r"not found|timeout|timed out|connection|cannot|could not|unable|"
        r"rate limit|exceeded|no space)",
        re.IGNORECASE,
    )
    selected = [line for line in lines if error_pattern.search(line)]
    if not selected:
        selected = lines[-20:]

    deduplicated: list[str] = []
    seen: set[str] = set()
    for line in selected:
        if line in seen:
            continue
        seen.add(line)
        deduplicated.append(line)
    return "\n".join(deduplicated[-40:])


def remote_result_label(result: RemoteRunResult) -> str:
    statuses = [
        result.reservation.status,
        result.upload.status,
        result.post_cleanup.status,
        result.final_quota.status,
    ]
    if "FAILED" in statuses:
        return "FAILED"
    if all(status == "SUCCESS" for status in statuses):
        return "SUCCESS"
    return "SKIPPED"


def print_final_run_result(exit_code: int):
    """Print final per-remote statistics and captured failures."""
    with OUTPUT_LOCK, STATE.run_results_lock:
        print()
        print(OUTPUT_SEPARATOR)
        print("FINAL RUN RESULT")
        print(OUTPUT_SEPARATOR)

        failed_remotes: list[str] = []
        for result in STATE.run_results.values():
            result_label = remote_result_label(result)
            if result_label == "FAILED":
                failed_remotes.append(result.remote_path)

            print()
            print(result.name)
            print(f"  Reservation : {result.reservation.status}")
            print(f"  Upload      : {result.upload.status}")
            print(f"  Post cleanup: {result.post_cleanup.status}")
            print(f"  Final quota : {result.final_quota.status}")
            print(f"  RESULT      : {result_label}")

            for label, stage in (
                ("Reservation", result.reservation),
                ("Upload", result.upload),
                ("Post cleanup", result.post_cleanup),
                ("Final quota", result.final_quota),
            ):
                if stage.status != "FAILED":
                    continue
                print(f"  {label} error:")
                for error in stage.errors:
                    for line in error.splitlines():
                        print(f"    {line}")

        print()
        print("-" * 80)
        print()
        print(f"OVERALL RESULT: {'SUCCESS' if exit_code == 0 else 'FAILED'}")
        print()
        print("Failed remotes:")
        if failed_remotes:
            for remote_path in failed_remotes:
                print(f"  {remote_path}")
        else:
            print("  (none)")
        print()
        print(f"Exit code: {exit_code}")
        print(OUTPUT_SEPARATOR)
        print(flush=True)
