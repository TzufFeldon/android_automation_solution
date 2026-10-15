"""Entry point. Load configuration, run checks, and choose the exit code."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from android_runner.adb import AdbClient, AdbError
from android_runner.config import AppConfig, ConfigError, load_config
from android_runner.report import ReportError, json_report_path, write_report
from android_runner.runner import Status, run_checks


def main(argv: list[str] | None = None) -> int:
    """Run one pass, or repeat it when ``interval_seconds`` is set. Return 0, 1, or 2."""
    args = _parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    adb = AdbClient()
    selected = config.device_id
    last_code: int | None = None
    while True:
        try:
            code, serial = _run_once(adb, config, args, selected)
        except KeyboardInterrupt:
            return _stop(last_code)
        if code == 2 or config.interval_seconds is None:
            return code
        last_code = code
        selected = serial
        try:
            time.sleep(config.interval_seconds)
        except KeyboardInterrupt:
            return _stop(last_code)


def _run_once(
    adb: AdbClient,
    config: AppConfig,
    args: argparse.Namespace,
    selected: str | None,
) -> tuple[int, str | None]:
    try:
        serial = adb.select_device(selected)
        info = adb.read_device_info(serial)
    except AdbError as exc:
        print(exc, file=sys.stderr)
        return 2, None

    logs_dir = Path(args.logs)
    results = run_checks(adb, config, info, logs_dir)
    report_path = Path(args.report)
    json_path = json_report_path(report_path)
    try:
        write_report(report_path, info, results)
    except ReportError as exc:
        print(exc, file=sys.stderr)
        return 2, None
    failed = sum(result.status is Status.FAIL for result in results)
    overall = "FAIL" if failed else "PASS"
    print(f"Overall: {overall}; report written to {report_path} and {json_path}.")
    return (1 if failed else 0), serial


def _stop(last_code: int | None) -> int:
    if last_code is None:
        print("Stopped before a report was written.", file=sys.stderr)
        return 2
    print("Stopped.", file=sys.stderr)
    return last_code


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Android device checks and write a text report and a JSON report."
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
