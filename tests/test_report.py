from __future__ import annotations

from pathlib import Path

from android_runner.adb import DeviceInfo
from android_runner.report import render_report, write_report
from android_runner.runner import CheckResult, Status

_SAMPLE = """\
Android Automation Report
=========================

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
    Command 'echo hello' succeeded and returned 'hello'.
Android version: PASS
    Major version 14 is greater than or equal to minimum 10.
Battery level: FAIL
    Battery level is 15%, which is lower than 20%.
    Log: logs/20261013-215301_battery_level.log
Application installation: FAIL
    Package com.android.settings is not installed.
    Log: logs/20261013-215302_application_installation.log
Launch application: SKIP
    Skipped because com.android.settings is not installed.

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
) -> CheckResult:
    return CheckResult(name, status, explanation, log_path, log_error)


def _sample_results() -> list[CheckResult]:
    return [
        _result(
            "Device is reachable",
            Status.PASS,
            "Command 'echo hello' succeeded and returned 'hello'.",
        ),
        _result(
            "Android version",
            Status.PASS,
            "Major version 14 is greater than or equal to minimum 10.",
        ),
        _result(
            "Battery level",
            Status.FAIL,
            "Battery level is 15%, which is lower than 20%.",
            Path("logs/20261013-215301_battery_level.log"),
        ),
        _result(
            "Application installation",
            Status.FAIL,
            "Package com.android.settings is not installed.",
            Path("logs/20261013-215302_application_installation.log"),
        ),
        _result(
            "Launch application",
            Status.SKIP,
            "Skipped because com.android.settings is not installed.",
        ),
    ]


def test_sample_report_matches_the_fixed_layout() -> None:
    text = render_report(_info(), _sample_results())

    assert text == _SAMPLE
    assert text.count("Device ID:") == 1
    assert "Passed: 2" in text
    assert "Failed: 2" in text
    assert "Skipped: 1" in text
    assert "All: 5" in text
    assert text.endswith("Overall: FAIL\n")


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


def test_log_capture_failure_is_a_separate_line() -> None:
    results = [
        _result(
            "Battery level",
            Status.FAIL,
            "Battery level is 15%, which is lower than 20%.",
            log_error="Log capture failed",
        )
    ]

    text = render_report(_info(), results)

    assert "    Battery level is 15%, which is lower than 20%." in text
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


def test_write_report_overwrites_and_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "out" / "report.txt"
    write_report(path, _info(), _sample_results())
    write_report(path, _info(android_version="8.1.0"), [])

    text = path.read_text(encoding="utf-8")
    assert "Android version: 8.1.0" in text
    assert "Battery level" not in text
