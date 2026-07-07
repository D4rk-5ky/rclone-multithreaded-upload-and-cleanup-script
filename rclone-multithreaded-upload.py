#!/usr/bin/env python3
"""Compatibility entry point for rclone-multithreaded-upload."""

import sys

from rclone_multithreaded_upload.main import main


if __name__ == "__main__":
    sys.exit(main())
