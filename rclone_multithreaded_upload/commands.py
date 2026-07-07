"""Subprocess execution helpers."""

import subprocess


def run_command(
    command: list[str],
    capture_output: bool = False,
) -> subprocess.CompletedProcess:
    """Run a command without shell=True."""
    print(f"Running command: {' '.join(command)}", flush=True)
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
    )


def is_directory_not_found(result: subprocess.CompletedProcess) -> bool:
    output = ""
    if result.stdout:
        output += result.stdout
    if result.stderr:
        output += result.stderr
    return "directory not found" in output.lower()
