from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from android_runner.adb import DeviceInfo
from android_runner.report import (
    ReportError,
    json_report_path,
    render_json,
    render_report,
    write_report,
)
from android_runner.runner import CheckResult, Status

_RAN_AT = datetime(2026, 10, 15, 14, 33, 7)

_SAMPLE = """\
Android Automation Report: emulator-5554
========================================
Run: 2026-10-15 14:33:07

Device
------
Device ID: emulator-5554
Android version: 14
Model: sdk_gphone64_arm64
Manufacturer: Google
Battery: 86%

Checks
------
Device is reachable: PASS
    Command: echo hello
    Result: hello
Android version: PASS
    Version: 14
    Minimum required: 10
Battery level: FAIL
    Battery: 15%
    Minimum required: 20%
    Log: logs/20261013-215301_battery_level.log
Application installation: FAIL
    Package: com.android.settings
    Installed: no
    Log: logs/20261013-215302_application_installation.log
Launch application: SKIP
    Package: com.android.settings
    Skipped: not installed

Totals
------
Passed: 2
Failed: 2
Skipped: 1
All: 5

Overall: FAIL
"""


def _info(**overrides: object) -> DeviceInfo:
    values: dict[str, object] = {
        "serial": "emulator-5554",
        "android_version": "14",
        "android_major": 14,
        "model": "sdk_gphone64_arm64",
        "manufacturer": "Google",
        "battery_percent": 86,
    }
    values.update(overrides)
    return DeviceInfo(**values)  # type: ignore[arg-type]


def _result(
    name: str,
    status: Status,
    explanation: str,
    log_path: Path | None = None,
    log_error: str | None = None,
    log_note: str | None = None,
) -> CheckResult:
    return CheckResult(name, status, explanation, log_path, log_error, log_note)


def _sample_results() -> list[CheckResult]:
    return [
        _result(
            "Device is reachable",
            Status.PASS,
            "Command: echo hello\nResult: hello",
        ),
        _result(
            "Android version",
            Status.PASS,
            "Version: 14\nMinimum required: 10",
        ),
        _result(
            "Battery level",
            Status.FAIL,
            "Battery: 15%\nMinimum required: 20%",
            Path("logs/20261013-215301_battery_level.log"),
        ),
        _result(
            "Application installation",
            Status.FAIL,
            "Package: com.android.settings\nInstalled: no",
            Path("logs/20261013-215302_application_installation.log"),
        ),
        _result(
            "Launch application",
            Status.SKIP,
            "Package: com.android.settings\nSkipped: not installed",
        ),
    ]


def test_sample_report_matches_the_fixed_layout() -> None:
    text = render_report(_info(), _sample_results(), ran_at=_RAN_AT)

    assert text == _SAMPLE


def test_overall_pass_when_nothing_failed_including_a_skip() -> None:
    results = [
        _result("Device is reachable", Status.PASS, "Ready."),
        _result("Android version", Status.PASS, "Ready."),
        _result("Battery level", Status.PASS, "Ready."),
        _result("Application installation", Status.PASS, "Ready."),
        _result("Launch application", Status.SKIP, "Skipped."),
    ]

    text = render_report(_info(), results)

    assert "Passed: 4" in text
    assert "Failed: 0" in text
    assert "Skipped: 1" in text
    assert "All: 5" in text
    assert text.endswith("Overall: PASS\n")
    assert "Log:" not in text
    assert "Log capture failed" not in text


def test_truncated_log_note_follows_the_log_path() -> None:
    results = [
        _result(
            "Battery level",
            Status.FAIL,
            "Battery: 15%\nMinimum required: 20%",
            Path("logs/20261013-215301_battery_level.log"),
            log_note="Log truncated to the most recent 1 MB.",
        )
    ]

    text = render_report(_info(), results)

    assert "    Log: logs/20261013-215301_battery_level.log" in text
    assert "    Log truncated to the most recent 1 MB." in text
    assert "Log capture failed" not in text


def test_log_capture_failure_is_a_separate_line() -> None:
    results = [
        _result(
            "Battery level",
            Status.FAIL,
            "Battery: 15%\nMinimum required: 20%",
            log_error="Log capture failed",
        )
    ]

    text = render_report(_info(), results)

    assert "    Battery: 15%" in text
    assert "    Minimum required: 20%" in text
    assert "    Log capture failed" in text
    assert "Log:" not in text
    assert "Overall: FAIL" in text


def test_missing_device_fields_and_zero_battery() -> None:
    info = _info(
        android_version=None,
        model=None,
        manufacturer="",
        battery_percent=0,
    )

    text = render_report(info, [])

    assert "Android version: unknown" in text
    assert "Model: unknown" in text
    assert "Manufacturer: unknown" in text
    assert "Battery: 0%" in text
    assert "Overall: PASS" in text


def test_write_report_rejects_a_directory(tmp_path: Path) -> None:
    path = tmp_path / "report.txt"
    path.mkdir()

    with pytest.raises(ReportError, match="directory") as caught:
        write_report(path, _info(), [])

    message = str(caught.value)
    assert "Traceback" not in message
    assert "\n" not in message
    assert message.endswith(".")


def test_write_report_rejects_a_parent_that_is_a_file(tmp_path: Path) -> None:
    parent = tmp_path / "not-a-directory"
    parent.write_text("x", encoding="utf-8")

    with pytest.raises(ReportError, match="could not be written") as caught:
        write_report(parent / "report.txt", _info(), [])

    assert "Traceback" not in str(caught.value)
    assert str(caught.value).endswith(".")


def test_write_report_overwrites_and_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "out" / "report.txt"
    write_report(path, _info(), _sample_results())
    write_report(path, _info(android_version="8.1.0"), [])

    text = path.read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert "Android version: 8.1.0" in text
    assert "Battery level" not in text
    assert payload["device"]["android_version"] == "8.1.0"
    assert payload["checks"] == []
    assert payload["overall"] == "PASS"


def test_json_report_matches_the_text_totals_and_log_paths() -> None:
    text = render_json(_info(), _sample_results(), ran_at=_RAN_AT)
    payload = json.loads(text)

    assert payload["ran_at"] == "2026-10-15 14:33:07"
    assert payload["device"] == {
        "id": "emulator-5554",
        "android_version": "14",
        "android_major": 14,
        "model": "sdk_gphone64_arm64",
        "manufacturer": "Google",
        "battery_percent": 86,
    }
    assert [check["status"] for check in payload["checks"]] == [
        "PASS",
        "PASS",
        "FAIL",
        "FAIL",
        "SKIP",
    ]
    assert payload["checks"][2]["log_path"] == "logs/20261013-215301_battery_level.log"
    assert payload["checks"][2]["log_note"] is None
    assert payload["checks"][2]["log_error"] is None
    assert payload["checks"][0]["log_path"] is None
    assert payload["totals"] == {"passed": 2, "failed": 2, "skipped": 1, "all": 5}
    assert payload["overall"] == "FAIL"
    assert text.endswith("\n")


def test_json_keeps_null_fields_a_zero_battery_and_log_details() -> None:
    info = _info(
        android_version=None,
        android_major=None,
        model=None,
        manufacturer="",
        battery_percent=0,
    )
    results = [
        _result(
            "Battery level",
            Status.FAIL,
            "Battery: unknown",
            log_error="Log capture failed",
            log_note="Log truncated to the most recent 1 MB.",
        )
    ]

    payload = json.loads(render_json(info, results))

    assert payload["device"]["android_version"] is None
    assert payload["device"]["android_major"] is None
    assert payload["device"]["model"] is None
    assert payload["device"]["manufacturer"] == ""
    assert payload["device"]["battery_percent"] == 0
    assert payload["checks"][0]["log_path"] is None
    assert payload["checks"][0]["log_error"] == "Log capture failed"
    assert payload["checks"][0]["log_note"] == "Log truncated to the most recent 1 MB."
    assert payload["overall"] == "FAIL"


def test_json_report_path_replaces_the_text_suffix(tmp_path: Path) -> None:
    assert json_report_path(tmp_path / "report.txt") == tmp_path / "report.json"
    assert json_report_path(tmp_path / "custom.log") == tmp_path / "custom.json"
    assert json_report_path(tmp_path / "report.json") == tmp_path / "report.json.json"


def test_write_report_rejects_a_json_path_that_is_a_directory(tmp_path: Path) -> None:
    (tmp_path / "report.json").mkdir()

    with pytest.raises(ReportError, match="report.json") as caught:
        write_report(tmp_path / "report.txt", _info(), [])

    message = str(caught.value)
    assert "directory" in message
    assert message.endswith(".")
    assert "Traceback" not in message
