"""Build the text report from device info and check results."""

from __future__ import annotations

from pathlib import Path

from android_runner.adb import DeviceInfo
from android_runner.runner import CheckResult, Status


def render_report(info: DeviceInfo, results: list[CheckResult]) -> str:
    """Return the report text, including a trailing newline."""
    passed = sum(result.status is Status.PASS for result in results)
    failed = sum(result.status is Status.FAIL for result in results)
    skipped = sum(result.status is Status.SKIP for result in results)
    lines = [
        "Android Automation Report",
        "=========================",
        "",
        "Device",
        "------",
        f"Device ID: {info.serial}",
        f"Android version: {_text(info.android_version)}",
        f"Model: {_text(info.model)}",
        f"Manufacturer: {_text(info.manufacturer)}",
        f"Battery: {_battery(info.battery_percent)}",
        "",
        "Checks",
        "------",
    ]
    for result in results:
        lines.append(f"{result.name}: {result.status.value}")
        lines.append(f"    {result.explanation}")
        if result.log_path is not None:
            lines.append(f"    Log: {result.log_path.as_posix()}")
        if result.log_error is not None:
            lines.append(f"    {result.log_error}")
    lines.extend(
        [
            "",
            "Totals",
            "------",
            f"Passed: {passed}",
            f"Failed: {failed}",
            f"Skipped: {skipped}",
            f"All: {passed + failed + skipped}",
            "",
            f"Overall: {'FAIL' if failed else 'PASS'}",
        ]
    )
    return "\n".join(lines) + "\n"


def write_report(path: str | Path, info: DeviceInfo, results: list[CheckResult]) -> None:
    """Write :func:`render_report` to ``path``, creating parent directories."""
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(info, results), encoding="utf-8")


def _text(value: str | None) -> str:
    if value is None or value == "":
        return "unknown"
    return value


def _battery(percent: int | None) -> str:
    if percent is None:
        return "unknown"
    return f"{percent}%"
