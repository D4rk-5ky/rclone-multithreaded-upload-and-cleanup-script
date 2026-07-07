"""Command-line parsing."""

import argparse

from . import VERSION


def parse_cli_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Upload CCTV files to rclone remotes and clean managed remote folders.",
    )
    parser.add_argument(
        "-c",
        "--config",
        required=True,
        help="Path to JSON config file. The filename extension is not enforced.",
    )
    parser.add_argument(
        "--validate-config",
        action="store_true",
        help="Load the config, print the startup summary, and exit without lock/rclone commands.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {VERSION}",
    )
    return parser.parse_args()
