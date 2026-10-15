"""Build the text and JSON reports from device info and check results."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from android_runner.adb import DeviceInfo
from android_runner.runner import CheckResult, Status


class ReportError(Exception):
    """The report could not be written. The message is one sentence."""


def json_report_path(path: str | Path) -> Path:
    """JSON file written beside the text report.

    ``report.txt`` becomes ``report.json``. A text path that already ends in
    ``.json`` becomes ``<name>.json.json`` so the two files stay distinct.
    """
    report_path = Path(path)
    sibling = report_path.with_suffix(".json")
    if sibling == report_path:
        return report_path.with_name(f"{report_path.name}.json")
    return sibling


def render_report(
    info: DeviceInfo,
    results: list[CheckResult],
    ran_at: datetime | None = None,
) -> str:
    """Report text, including the trailing newline.

    The title names the connected serial. ``Run`` is local time with seconds.
    """
    passed, failed, skipped = _counts(results)
    title = f"Android Automation Report: {info.serial}"
    lines = [
        title,
        "=" * len(title),
        f"Run: {_stamp(ran_at)}",
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
        if result.log_note is not None:
            lines.append(f"    {result.log_note}")
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


def render_json(
    info: DeviceInfo,
    results: list[CheckResult],
    ran_at: datetime | None = None,
) -> str:
    """JSON report, including the trailing newline.

    Unread device fields stay ``null``. An empty string stays ``""``.
    ``log_path`` is a POSIX path when a failure log was saved.
    ``ran_at`` is local ``YYYY-MM-DD HH:MM:SS``.
    """
    passed, failed, skipped = _counts(results)
    payload = {
        "ran_at": _stamp(ran_at),
        "device": {
            "id": info.serial,
            "android_version": info.android_version,
            "android_major": info.android_major,
            "model": info.model,
            "manufacturer": info.manufacturer,
            "battery_percent": info.battery_percent,
        },
        "checks": [
            {
                "name": result.name,
                "status": result.status.value,
                "explanation": result.explanation,
                "log_path": (
                    None if result.log_path is None else result.log_path.as_posix()
                ),
                "log_note": result.log_note,
                "log_error": result.log_error,
            }
            for result in results
        ],
        "totals": {
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "all": passed + failed + skipped,
        },
        "overall": "FAIL" if failed else "PASS",
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def write_report(
    path: str | Path,
    info: DeviceInfo,
    results: list[CheckResult],
    ran_at: datetime | None = None,
) -> None:
    """Write the text report and the sibling JSON report.

    Parent directories are created. Raises ``ReportError`` when either path
    cannot be written. Text and JSON share one ``ran_at``.
    """
    report_path = Path(path)
    json_path = json_report_path(report_path)
    moment = datetime.now() if ran_at is None else ran_at
    text = render_report(info, results, ran_at=moment)
    payload = render_json(info, results, ran_at=moment)
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
    except IsADirectoryError:
        raise ReportError(f"Report path is a directory: {report_path}.") from None
    except PermissionError:
        raise ReportError(f"Report file is not writable: {report_path}.") from None
    except OSError as exc:
        detail = exc.strerror or exc.__class__.__name__
        raise ReportError(
            f"Report file could not be written: {report_path} ({detail})."
        ) from None
    _write_text(report_path, text)
    _write_text(json_path, payload)


def _write_text(path: Path, text: str) -> None:
    try:
        path.write_text(text, encoding="utf-8")
    except IsADirectoryError:
        raise ReportError(f"Report path is a directory: {path}.") from None
    except PermissionError:
        raise ReportError(f"Report file is not writable: {path}.") from None
    except OSError as exc:
        detail = exc.strerror or exc.__class__.__name__
        raise ReportError(
            f"Report file could not be written: {path} ({detail})."
        ) from None


def _stamp(ran_at: datetime | None) -> str:
    moment = datetime.now() if ran_at is None else ran_at
    return f"{moment:%Y-%m-%d %H:%M:%S}"


def _counts(results: list[CheckResult]) -> tuple[int, int, int]:
    passed = sum(result.status is Status.PASS for result in results)
    failed = sum(result.status is Status.FAIL for result in results)
    skipped = sum(result.status is Status.SKIP for result in results)
    return passed, failed, skipped


def _text(value: str | None) -> str:
    if value is None or value == "":
        return "unknown"
    return value


def _battery(percent: int | None) -> str:
    if percent is None:
        return "unknown"
    return f"{percent}%"
