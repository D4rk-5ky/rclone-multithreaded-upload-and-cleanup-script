"""Thread-safe console output helpers."""

import threading


OUTPUT_LOCK = threading.Lock()
OUTPUT_SEPARATOR = "=" * 80


def print_step(message: str):
    print(f"\n  {message}\n", flush=True)


def print_error(message: str):
    print(f"\n      ERROR: {message}\n", flush=True)


def print_job_block(job_type: str, job_number: int, target: str, message: str):
    """Print output from threaded jobs without mixing lines between threads."""
    message = message.rstrip()
    if not message:
        return

    with OUTPUT_LOCK:
        print()
        print(OUTPUT_SEPARATOR)
        print(f"{job_type} {job_number}")
        print(f"Target: {target}")
        print(OUTPUT_SEPARATOR)
        print(message)
        print(OUTPUT_SEPARATOR)
        print(flush=True)
