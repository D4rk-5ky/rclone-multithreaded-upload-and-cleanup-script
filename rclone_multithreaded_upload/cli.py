"""Command-line parsing and user-facing CLI help."""

import argparse
import sys
from collections.abc import Sequence

from . import VERSION


class FullHelpArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that prints the complete help text on CLI errors."""

    def error(self, message: str) -> None:
        """Print full option documentation before reporting a CLI syntax error."""
        self.print_help(sys.stderr)
        self.exit(2, f"\nerror: {message}\n")


def build_cli_parser() -> argparse.ArgumentParser:
    """Build the command-line parser with complete help for every supported flag."""
    parser = FullHelpArgumentParser(
        description=(
            "Upload CCTV files to rclone remotes and clean managed remote folders. "
            "A config file is required for normal runs and config validation."
        ),
        epilog=(
            "Examples:\n"
            "  %(prog)s --config ./config.json\n"
            "  %(prog)s -c ./config.json\n"
            "  %(prog)s --config ./config.json --validate-config\n"
            "  %(prog)s --version\n"
            "  %(prog)s --help\n\n"
            "CLI parse errors exit with status 2 and print this complete help text."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    parser.add_argument(
        "-h",
        "--help",
        action="help",
        help=(
            "Print this complete CLI help, including every supported flag, "
            "explanation, example, and usage line, then exit."
        ),
    )
    parser.add_argument(
        "-c",
        "--config",
        metavar="PATH",
        required=True,
        help=(
            "Path to the JSON configuration file. Required for normal runs and "
            "--validate-config. The filename extension is not enforced."
        ),
    )
    parser.add_argument(
        "--validate-config",
        action="store_true",
        help=(
            "Validate PATH, load all settings, print the effective startup summary, "
            "then exit without creating the lock file or running any rclone command."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {VERSION}",
        help="Print the application version and exit. A config file is not required.",
    )
    return parser


def parse_cli_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments; invalid input receives the complete help text."""
    return build_cli_parser().parse_args(argv)
