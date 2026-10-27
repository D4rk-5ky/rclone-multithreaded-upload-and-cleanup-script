"""Observe per-rclone-process JSON statistics; never drive transfer decisions."""

import json
import math
import time

from .state import STATE

STATS_INTERVAL = 1
STALE_SECONDS = 3
REDIRECT_OPTIONS = {"--log-file", "--syslog", "--syslog-facility"}


def live_stats_options(options: list[str]) -> list[str]:
    """Own display/logging flags when live reporting needs JSON stats on stderr."""
    if not STATE.live_config.enabled:
        return list(options)
    # Append explicit final values. Do not parse/remove arbitrary option tokens:
    # a source-filter argument may itself look like a logging flag.
    return list(options) + ["--use-json-log", f"--stats={STATS_INTERVAL}s",
                            "--stats-log-level=NOTICE", "--log-level=NOTICE", "--progress=false",
                            "--quiet=false", "--log-file=", "--syslog=false"]


def start_transfer(remote: str) -> None:
    """Mark an actually launched upload; unknown counters stay unknown until sampled."""
    with STATE.run_results_lock:
        result = STATE.run_results.get(remote)
        if result is None:
            return
        result.transfer_started = time.monotonic()
        result.transfer_finished = result.transfer_sample_time = result.transfer_sample_elapsed = None
        result.transferred_bytes = result.bytes_per_second = None
    from .reporting import refresh_live
    refresh_live()


def finish_transfer(remote: str) -> None:
    """Freeze the worker duration and stop advertising its final speed as current."""
    with STATE.run_results_lock:
        result = STATE.run_results.get(remote)
        if result is None or result.transfer_started is None:
            return
        result.transfer_finished = result.transfer_finished or time.monotonic()
        result.bytes_per_second = 0.0
    from .reporting import refresh_live
    refresh_live()


def record_transfer_stats(remote: str, line: str) -> bool:
    """Consume valid cumulative bytes/speed/elapsedTime; reject invalid/stale samples."""
    try:
        packet = json.loads(line)
    except (ValueError, TypeError, RecursionError):
        return False
    stats = packet.get("stats") if isinstance(packet, dict) else None
    if not isinstance(stats, dict):
        return False
    size, speed, elapsed = stats.get("bytes"), stats.get("speed"), stats.get("elapsedTime")
    if type(size) is not int or size < 0:
        return False
    try:
        if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
               for value in (speed, elapsed)):
            return False
    except OverflowError:
        return False
    with STATE.run_results_lock:
        result = STATE.run_results.get(remote)
        if result is None or result.transfer_started is None or result.transfer_finished is not None:
            return True
        if (result.transfer_sample_elapsed is not None and elapsed < result.transfer_sample_elapsed
                or result.transferred_bytes is not None and size < result.transferred_bytes):
            return True
        result.transferred_bytes = size  # Absolute counter: never add repeated snapshots.
        result.bytes_per_second = float(speed)
        result.transfer_sample_elapsed = float(elapsed)
        result.transfer_sample_time = time.monotonic()
    from .reporting import refresh_live
    refresh_live()
    return True


def transfer_snapshot(result, now: float) -> dict:
    """Read locked counters with independent duration/average and stale-speed handling."""
    started, ended = result.transfer_started, result.transfer_finished
    elapsed = max(0, (ended if ended is not None else now) - started) if started is not None else 0.0
    stale = (started is not None and ended is None and result.transfer_sample_time is not None
             and now - result.transfer_sample_time > STALE_SECONDS)
    speed = 0.0 if ended is not None else None if stale else result.bytes_per_second
    average = result.transferred_bytes / elapsed if result.transferred_bytes is not None and elapsed > 0 else None
    return {"transferred_bytes": result.transferred_bytes, "bytes_per_second": speed,
            "average_bytes_per_second": average, "transfer_elapsed_seconds": round(elapsed, 3),
            "transfer_stats_received": result.transfer_sample_time is not None,
            "transfer_stats_stale": stale}
