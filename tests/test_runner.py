from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from android_runner.adb import AdbError, CommandResult, DeviceInfo
from android_runner.config import AppConfig
from android_runner.runner import (
    _LOG_MAX_BYTES,
    _TRUNCATED_LOG,
    InstallCause,
    Status,
    run_checks,
)

_PACKAGE = "com.android.settings"


class _Clock:
    @staticmethod
    def now() -> datetime:
        return datetime(2026, 10, 13, 21, 53, 1)


class FakeAdb:
    def __init__(
        self,
        responses: dict[object, CommandResult | AdbError],
        logcat_text: str = "log line\n",
        logcat_error: AdbError | None = None,
    ) -> None:
        self.responses = responses
        self.calls: list[tuple[object, ...]] = []
        self.logcat_text = logcat_text
        self.logcat_error = logcat_error
        self.log_texts = [logcat_text]

    def shell(self, serial: str, *args: str) -> CommandResult:
        self.calls.append(("shell", serial, args))
        return self._take(args)

    def launch_package(self, serial: str, package_name: str) -> CommandResult:
        self.calls.append(("launch", serial, package_name))
        return self._take(("launch", package_name))

    def logcat(self, serial: str) -> str:
        self.calls.append(("logcat", serial))
        if self.logcat_error is not None:
            raise self.logcat_error
        if len(self.log_texts) > 1:
            return self.log_texts.pop(0)
        return self.log_texts[0]

    def _take(self, key: object) -> CommandResult:
        if key not in self.responses:
            raise AssertionError(f"unexpected call {key}")
        outcome = self.responses[key]
        if isinstance(outcome, AdbError):
            raise outcome
        return outcome


def _config(**overrides: object) -> AppConfig:
    values: dict[str, object] = {
        "package_name": _PACKAGE,
        "minimum_android_version": 10,
        "minimum_battery": 20,
        "device_id": None,
    }
    values.update(overrides)
    return AppConfig(**values)  # type: ignore[arg-type]


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


def _ok(stdout: str = "", stderr: str = "", code: int = 0) -> CommandResult:
    return CommandResult(code, stdout, stderr)


def _passing_adb(
    overrides: dict[object, CommandResult | AdbError] | None = None,
) -> FakeAdb:
    responses: dict[object, CommandResult | AdbError] = {
        ("echo", "hello"): _ok("hello\n"),
        ("pm", "path", _PACKAGE): _ok("package:/data/app/com.android.settings/base.apk\n"),
        ("launch", _PACKAGE): _ok("Events injected: 1\n"),
    }
    if overrides:
        responses.update(overrides)
    return FakeAdb(responses)


def _names(results: list[object]) -> list[str]:
    return [result.name for result in results]  # type: ignore[attr-defined]


def test_passing_run_does_not_call_adb_for_version_or_battery_or_logcat(
    tmp_path: Path,
) -> None:
    adb = _passing_adb()

    results = run_checks(adb, _config(), _info(), tmp_path / "logs")

    assert _names(results) == [
        "Device is reachable",
        "Android version",
        "Battery level",
        "Application installation",
        "Launch application",
    ]
    assert [result.status for result in results] == [Status.PASS] * 5
    assert all(result.log_path is None and result.log_error is None for result in results)
    assert ("logcat", "emulator-5554") not in adb.calls
    assert not (tmp_path / "logs").exists()
    shell_args = [call[2] for call in adb.calls if call[0] == "shell"]
    assert shell_args == [("echo", "hello"), ("pm", "path", _PACKAGE)]
    assert ("launch", "emulator-5554", _PACKAGE) in adb.calls
    assert results[1].explanation == (
        "Major version 14 is greater than or equal to minimum 10."
    )


@pytest.mark.parametrize(
    ("stdout", "code", "status"),
    [
        ("hello\n", 0, Status.PASS),
        ("  hello\r\n", 0, Status.PASS),
        ("Hello", 0, Status.FAIL),
        ("hello there", 0, Status.FAIL),
        ("hello", 1, Status.FAIL),
        ("", 0, Status.FAIL),
    ],
)
def test_reachable_output(tmp_path: Path, stdout: str, code: int, status: Status) -> None:
    adb = _passing_adb({("echo", "hello"): _ok(stdout, code=code)})

    results = run_checks(adb, _config(), _info(), tmp_path / "logs")

    assert results[0].status is status
    if status is Status.FAIL:
        assert results[0].log_path is not None
        assert results[0].log_path.read_text(encoding="utf-8") == "log line\n"
    else:
        assert results[0].log_path is None


def test_reachable_timeout_still_runs_the_remaining_checks(tmp_path: Path) -> None:
    adb = _passing_adb({("echo", "hello"): AdbError("timed out", timed_out=True)})

    results = run_checks(adb, _config(), _info(), tmp_path / "logs")

    assert results[0].status is Status.FAIL
    assert results[0].explanation == "Command 'echo hello' timed out."
    assert [result.status for result in results[1:]] == [Status.PASS] * 4
    assert ("pm", "path", _PACKAGE) in [call[2] for call in adb.calls if call[0] == "shell"]


@pytest.mark.parametrize(
    ("version", "major", "minimum", "status", "snippet"),
    [
        ("14", 14, 10, Status.PASS, "greater than or equal to minimum 10"),
        ("8.1.0", 8, 8, Status.PASS, "greater than or equal to minimum 8"),
        ("8.1.0", 8, 10, Status.FAIL, "lower than minimum 10"),
        ("Next", None, 10, Status.FAIL, "could not be parsed"),
        (None, None, 10, Status.FAIL, "could not be read"),
    ],
)
def test_version_check(
    tmp_path: Path,
    version: str | None,
    major: int | None,
    minimum: int,
    status: Status,
    snippet: str,
) -> None:
    results = run_checks(
        _passing_adb(),
        _config(minimum_android_version=minimum),
        _info(android_version=version, android_major=major),
        tmp_path / "logs",
    )

    assert results[1].status is status
    assert snippet in results[1].explanation


@pytest.mark.parametrize(
    ("level", "minimum", "status"),
    [
        (21, 20, Status.PASS),
        (20, 20, Status.FAIL),
        (15, 20, Status.FAIL),
        (100, 100, Status.FAIL),
        (99, 100, Status.FAIL),
        (0, 0, Status.FAIL),
        (1, 0, Status.PASS),
        (None, 20, Status.FAIL),
    ],
)
def test_battery_threshold(
    tmp_path: Path,
    level: int | None,
    minimum: int,
    status: Status,
) -> None:
    results = run_checks(
        _passing_adb(),
        _config(minimum_battery=minimum),
        _info(battery_percent=level),
        tmp_path / "logs",
    )

    battery = results[2]
    assert battery.status is status
    if level is None:
        assert battery.explanation == "Battery level could not be read."
    elif level == minimum:
        assert battery.explanation == (
            f"Battery level is {level}%, which is equal to {minimum}% "
            "and must be above it."
        )
    elif status is Status.FAIL:
        assert battery.explanation == (
            f"Battery level is {level}%, which is lower than {minimum}%."
        )
    else:
        assert battery.explanation == (
            f"Battery level is {level}%, which is greater than {minimum}%."
        )
    if status is Status.FAIL:
        assert battery.log_path is not None
    else:
        assert battery.log_path is None


def test_missing_package_fails_install_and_skips_launch_without_monkey(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("android_runner.runner.datetime", _Clock)
    adb = _passing_adb({("pm", "path", _PACKAGE): _ok("", "not found", 1)})
    logs = tmp_path / "logs"

    results = run_checks(adb, _config(), _info(), logs)

    assert results[3].status is Status.FAIL
    assert results[3].install_cause is InstallCause.MISSING
    assert results[3].explanation == f"Package {_PACKAGE} is not installed."
    assert results[4].status is Status.SKIP
    assert results[4].install_cause is None
    assert results[4].explanation == f"Skipped because {_PACKAGE} is not installed."
    assert results[4].log_path is None
    assert ("launch", "emulator-5554", _PACKAGE) not in adb.calls
    log_path = logs / "20261013-215301_application_installation.log"
    assert results[3].log_path == log_path
    assert results[3].log_note is None
    assert log_path.read_text(encoding="utf-8") == "log line\n"


def test_package_line_with_leading_space_passes(tmp_path: Path) -> None:
    adb = _passing_adb(
        {("pm", "path", _PACKAGE): _ok("warning\n  package:/data/app/base.apk\n")}
    )

    results = run_checks(adb, _config(), _info(), tmp_path / "logs")

    assert results[3].status is Status.PASS


def test_install_adb_failure_does_not_claim_the_package_is_missing(
    tmp_path: Path,
) -> None:
    adb = _passing_adb(
        {("pm", "path", _PACKAGE): AdbError("adb is not available")}
    )

    results = run_checks(adb, _config(), _info(), tmp_path / "logs")

    assert results[3].status is Status.FAIL
    assert results[3].install_cause is InstallCause.UNCONFIRMED
    assert results[3].explanation == f"The installation check for {_PACKAGE} failed."
    assert "not installed" not in results[3].explanation
    assert results[4].status is Status.SKIP
    assert "could not be confirmed" in results[4].explanation
    assert "not installed" not in results[4].explanation
    assert ("launch", "emulator-5554", _PACKAGE) not in adb.calls


def test_install_timeout_skips_launch_and_does_not_claim_the_package_is_missing(
    tmp_path: Path,
) -> None:
    adb = _passing_adb(
        {("pm", "path", _PACKAGE): AdbError("timed out", timed_out=True)}
    )

    results = run_checks(adb, _config(), _info(), tmp_path / "logs")

    assert results[3].status is Status.FAIL
    assert results[3].install_cause is InstallCause.UNCONFIRMED
    assert "timed out" in results[3].explanation
    assert results[4].status is Status.SKIP
    assert "could not be confirmed" in results[4].explanation
    assert ("launch", "emulator-5554", _PACKAGE) not in adb.calls


@pytest.mark.parametrize(
    ("code", "stdout", "stderr", "snippet"),
    [
        (0, "Events injected: 1\n", "", "Launched"),
        (0, "No activities found\n", "", "No launcher activity"),
        (1, "", "monkey aborted", "monkey aborted"),
        (1, "", "", "failed"),
        (0, "ok", "No activities found", "No launcher activity"),
    ],
)
def test_launch_output(
    tmp_path: Path,
    code: int,
    stdout: str,
    stderr: str,
    snippet: str,
) -> None:
    adb = _passing_adb({("launch", _PACKAGE): _ok(stdout, stderr, code)})

    results = run_checks(adb, _config(), _info(), tmp_path / "logs")

    assert snippet in results[4].explanation
    if code == 0 and snippet == "Launched":
        assert results[4].status is Status.PASS
        assert results[4].log_path is None
    else:
        assert results[4].status is Status.FAIL


def test_two_failures_in_the_same_second_get_distinct_log_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("android_runner.runner.datetime", _Clock)
    adb = _passing_adb({("pm", "path", _PACKAGE): _ok("", code=1)})
    adb.log_texts = ["battery-log\n", "install-log\n"]
    logs = tmp_path / "logs"

    results = run_checks(
        adb,
        _config(),
        _info(battery_percent=15),
        logs,
    )

    battery_log = logs / "20261013-215301_battery_level.log"
    install_log = logs / "20261013-215301_application_installation.log"
    assert results[2].log_path == battery_log
    assert results[3].log_path == install_log
    assert battery_log.read_text(encoding="utf-8") == "battery-log\n"
    assert install_log.read_text(encoding="utf-8") == "install-log\n"
    assert results[4].status is Status.SKIP
    assert results[4].log_path is None


def test_oversized_log_is_truncated_and_keeps_the_tail(tmp_path: Path) -> None:
    adb = _passing_adb()
    adb.log_texts = [("a" * _LOG_MAX_BYTES) + "\nkept-tail\n"]

    results = run_checks(adb, _config(), _info(battery_percent=15), tmp_path / "logs")

    battery = results[2]
    assert battery.status is Status.FAIL
    assert battery.log_note == _TRUNCATED_LOG
    assert battery.log_error is None
    assert battery.log_path is not None
    saved = battery.log_path.read_text(encoding="utf-8")
    assert saved == "kept-tail\n"
    assert len(saved.encode("utf-8")) <= _LOG_MAX_BYTES


def test_logcat_failure_keeps_the_original_failure(tmp_path: Path) -> None:
    adb = _passing_adb()
    adb.logcat_error = AdbError("logcat timed out", timed_out=True)
    logs = tmp_path / "logs"

    results = run_checks(adb, _config(), _info(battery_percent=15), logs)

    battery = results[2]
    assert battery.status is Status.FAIL
    assert battery.explanation == "Battery level is 15%, which is lower than 20%."
    assert battery.log_path is None
    assert battery.log_error == "Log capture failed"
    assert list(logs.glob("*.log")) == []
    assert results[3].status is Status.PASS


def test_log_directory_that_is_a_file_does_not_abort_the_run(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.write_text("not a directory", encoding="utf-8")

    results = run_checks(_passing_adb(), _config(), _info(battery_percent=15), logs)

    assert results[2].status is Status.FAIL
    assert results[2].explanation.startswith("Battery level is 15%")
    assert results[2].log_error == "Log capture failed"
    assert results[3].status is Status.PASS


def test_every_explanation_is_one_line(tmp_path: Path) -> None:
    adb = _passing_adb({("echo", "hello"): _ok("nope")})

    results = run_checks(adb, _config(), _info(battery_percent=20), tmp_path / "logs")

    for result in results:
        assert "\n" not in result.explanation
        assert result.explanation.endswith(".")
        assert "Traceback" not in result.explanation
