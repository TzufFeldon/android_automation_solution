"""Entry point. Load configuration, run checks, and choose the exit code."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from android_runner.adb import AdbClient, AdbError
from android_runner.config import ConfigError, load_config
from android_runner.report import write_report
from android_runner.runner import Status, run_checks


def main(argv: list[str] | None = None) -> int:
    """Run one pass and return 0, 1, or 2.

    Configuration is loaded before any ADB call. Setup failures print one
    sentence to stderr and do not write the report. Any other exception
    propagates.
    """
    args = _parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    adb = AdbClient()
    try:
        serial = adb.select_device(config.device_id)
        info = adb.read_device_info(serial)
    except AdbError as exc:
        print(exc, file=sys.stderr)
        return 2

    logs_dir = Path(args.logs)
    results = run_checks(adb, config, info, logs_dir)
    report_path = Path(args.report)
    write_report(report_path, info, results)
    failed = sum(result.status is Status.FAIL for result in results)
    overall = "FAIL" if failed else "PASS"
    print(f"Overall: {overall}; report written to {report_path}.")
    return 1 if failed else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Android device checks and write a text report."
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to the JSON configuration file.",
    )
    parser.add_argument(
        "--report",
        default="report.txt",
        help="Path of the report file to write.",
    )
    parser.add_argument(
        "--logs",
        default="logs",
        help="Directory for logcat files saved after a failed check.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
