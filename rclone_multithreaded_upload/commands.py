"""Subprocess execution helpers."""

import subprocess

from .reporting import append_diagnostics
from .process_lifecycle import check_cancelled


def run_command(
    command: list[str],
    capture_output: bool = False,
) -> subprocess.CompletedProcess:
    """Run a command without shell=True."""
    print(f"Running command: {' '.join(command)}", flush=True)
    check_cancelled()
    result = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
    )

    if capture_output and result.stderr:
        append_diagnostics(result.stderr)
    return result


def is_directory_not_found(result: subprocess.CompletedProcess) -> bool:
    output = ""
    if result.stdout:
        output += result.stdout
    if result.stderr:
        output += result.stderr
    return "directory not found" in output.lower()
