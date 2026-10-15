"""Entry point. Load configuration, run checks, and choose the exit code."""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

from android_runner.adb import AdbClient, AdbError
from android_runner.config import AppConfig, ConfigError, load_config
from android_runner.report import ReportError, json_report_path, write_report
from android_runner.runner import Status, run_checks

_CONFIG_PATH = Path("config.json")
_REPORT_PATH = Path("report.txt")
_LOGS_DIR = Path("logs")


def main() -> int:
    """Run one pass, or repeat it when ``interval_seconds`` is set. Return 0, 1, or 2."""
    try:
        config = load_config(_CONFIG_PATH)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    adb = AdbClient()
    selected = config.device_id
    last_code: int | None = None
    announce = True
    while True:
        try:
            code, serial = _run_once(adb, config, selected, announce=announce)
        except KeyboardInterrupt:
            return _stop(last_code)
        if code == 2 or config.interval_seconds is None:
            return code
        last_code = code
        selected = serial
        announce = False
        try:
            time.sleep(config.interval_seconds)
        except KeyboardInterrupt:
            return _stop(last_code)


def _run_once(
    adb: AdbClient,
    config: AppConfig,
    selected: str | None,
    announce: bool,
) -> tuple[int, str | None]:
    try:
        serial = adb.select_device(selected)
        if announce:
            print(f"Connected device: {serial}", flush=True)
        info = adb.read_device_info(serial)
    except AdbError as exc:
        print(exc, file=sys.stderr)
        return 2, None

    results = run_checks(adb, config, info, _LOGS_DIR)
    report_path = _REPORT_PATH
    json_path = json_report_path(report_path)
    ran_at = datetime.now()
    try:
        write_report(report_path, info, results, ran_at=ran_at)
    except ReportError as exc:
        print(exc, file=sys.stderr)
        return 2, None
    failed = sum(result.status is Status.FAIL for result in results)
    overall = "FAIL" if failed else "PASS"
    print(
        f"{ran_at:%Y-%m-%d %H:%M:%S} Overall: {overall}; "
        f"report written to {report_path} and {json_path}."
    )
    return (1 if failed else 0), serial


def _stop(last_code: int | None) -> int:
    if last_code is None:
        print("Stopped before a report was written.", file=sys.stderr)
        return 2
    print("Stopped.", file=sys.stderr)
    return last_code


if __name__ == "__main__":
    raise SystemExit(main())
