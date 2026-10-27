#!/usr/bin/env python3
"""CLI entry point for rclone-multithreaded-upload."""

import sys

from rclone_multithreaded_upload.main import main
from rclone_multithreaded_upload.process_lifecycle import supervised_main


if __name__ == "__main__":
    sys.exit(supervised_main(main))
