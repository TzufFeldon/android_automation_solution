from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from android_runner.adb import AdbError, CommandResult, DeviceInfo
from android_runner.config import AppConfig
from android_runner.runner import (
    _LOG_MAX_BYTES,
    _TRUNCATED_LOG,
    CheckResult,
    InstallCause,
    Status,
    _error_note,
    _filter_package_log,
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
        log_time: str | None = "10-15 14:05:00.000",
    ) -> None:
        self.responses = responses
        self.calls: list[tuple[object, ...]] = []
        self.logcat_text = logcat_text
        self.logcat_error = logcat_error
        self.log_texts = [logcat_text]
        self.log_time = log_time
        self.log_times: list[str | None] | None = None
        self.window_texts: list[str] | None = None

    def shell(self, serial: str, *args: str) -> CommandResult:
        self.calls.append(("shell", serial, args))
        return self._take(args)

    def launch_package(self, serial: str, package_name: str) -> CommandResult:
        self.calls.append(("launch", serial, package_name))
        return self._take(("launch", package_name))

    def device_log_time(self, serial: str) -> str | None:
        self.calls.append(("device_log_time", serial))
        if self.log_times is not None:
            return self.log_times.pop(0)
        return self.log_time

    def logcat(self, serial: str, since: str | None = None) -> str:
        self.calls.append(("logcat", serial, since))
        if self.logcat_error is not None:
            raise self.logcat_error
        if since is not None and self.window_texts is not None:
            return self.window_texts.pop(0)
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
        "minimum_free_storage_mb": 100,
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


def _df(available_kib: int) -> str:
    return (
        "Filesystem 1K-blocks Used Available Use% Mounted on\n"
        f"/dev/block/data 999999 100 {available_kib} 10% /data\n"
    )


def _activities(*lines: str) -> str:
    return "".join(f"  {line}\n" for line in lines)


def _check(results: list[CheckResult], name: str) -> CheckResult:
    found = [result for result in results if result.name == name]
    assert len(found) == 1
    return found[0]


def _passing_adb(
    overrides: dict[object, CommandResult | AdbError] | None = None,
) -> FakeAdb:
    responses: dict[object, CommandResult | AdbError] = {
        ("echo", "hello"): _ok("hello\n"),
        ("df", "-k", "/data"): _ok(_df(204_800)),
        ("ip", "route", "show", "table", "all"): _ok("default via 10.0.2.2 dev eth0\n"),
        ("dumpsys", "power"): _ok("  mWakefulness=Awake\n"),
        ("pm", "path", _PACKAGE): _ok("package:/data/app/com.android.settings/base.apk\n"),
        ("launch", _PACKAGE): _ok("Events injected: 1\n"),
        ("dumpsys", "activity", "activities"): _ok(
            _activities(
                "topResumedActivity=ActivityRecord{abc u0 com.android.settings/.Settings t7}"
            )
        ),
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
        "Free storage",
        "Network",
        "Screen state",
        "Application installation",
        "Launch application",
        "Foreground activity",
    ]
    assert [result.status for result in results] == [Status.PASS] * 9
    assert all(result.log_path is None and result.log_error is None for result in results)
    assert not any(call[0] == "logcat" for call in adb.calls)
    assert not (tmp_path / "logs").exists()
    shell_args = [call[2] for call in adb.calls if call[0] == "shell"]
    assert shell_args == [
        ("echo", "hello"),
        ("df", "-k", "/data"),
        ("ip", "route", "show", "table", "all"),
        ("dumpsys", "power"),
        ("pm", "path", _PACKAGE),
        ("dumpsys", "activity", "activities"),
    ]
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
    assert [result.status for result in results[1:]] == [Status.PASS] * 8
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

    install = _check(results, "Application installation")
    launch = _check(results, "Launch application")
    foreground = _check(results, "Foreground activity")
    assert install.status is Status.FAIL
    assert install.install_cause is InstallCause.MISSING
    assert install.explanation == f"Package {_PACKAGE} is not installed."
    assert launch.status is Status.SKIP
    assert launch.install_cause is None
    assert launch.explanation == f"Skipped because {_PACKAGE} is not installed."
    assert launch.log_path is None
    assert foreground.status is Status.SKIP
    assert foreground.explanation == launch.explanation
    assert foreground.log_path is None
    assert ("launch", "emulator-5554", _PACKAGE) not in adb.calls
    shell_args = [call[2] for call in adb.calls if call[0] == "shell"]
    assert ("dumpsys", "activity", "activities") not in shell_args
    log_path = logs / "20261013-215301_application_installation.log"
    assert install.log_path == log_path
    assert install.log_note is None
    assert log_path.read_text(encoding="utf-8") == ""


def test_package_line_with_leading_space_passes(tmp_path: Path) -> None:
    adb = _passing_adb(
        {("pm", "path", _PACKAGE): _ok("warning\n  package:/data/app/base.apk\n")}
    )

    results = run_checks(adb, _config(), _info(), tmp_path / "logs")

    assert _check(results, "Application installation").status is Status.PASS


def test_install_adb_failure_does_not_claim_the_package_is_missing(
    tmp_path: Path,
) -> None:
    adb = _passing_adb(
        {("pm", "path", _PACKAGE): AdbError("adb is not available")}
    )

    results = run_checks(adb, _config(), _info(), tmp_path / "logs")

    install = _check(results, "Application installation")
    launch = _check(results, "Launch application")
    foreground = _check(results, "Foreground activity")
    assert install.status is Status.FAIL
    assert install.install_cause is InstallCause.UNCONFIRMED
    assert install.explanation == f"The installation check for {_PACKAGE} failed."
    assert "not installed" not in install.explanation
    assert launch.status is Status.SKIP
    assert "could not be confirmed" in launch.explanation
    assert "not installed" not in launch.explanation
    assert foreground.status is Status.SKIP
    assert "could not be confirmed" in foreground.explanation
    assert ("launch", "emulator-5554", _PACKAGE) not in adb.calls
    shell_args = [call[2] for call in adb.calls if call[0] == "shell"]
    assert ("dumpsys", "activity", "activities") not in shell_args


def test_install_timeout_skips_launch_and_does_not_claim_the_package_is_missing(
    tmp_path: Path,
) -> None:
    adb = _passing_adb(
        {("pm", "path", _PACKAGE): AdbError("timed out", timed_out=True)}
    )

    results = run_checks(adb, _config(), _info(), tmp_path / "logs")

    install = _check(results, "Application installation")
    launch = _check(results, "Launch application")
    foreground = _check(results, "Foreground activity")
    assert install.status is Status.FAIL
    assert install.install_cause is InstallCause.UNCONFIRMED
    assert "timed out" in install.explanation
    assert launch.status is Status.SKIP
    assert "could not be confirmed" in launch.explanation
    assert foreground.status is Status.SKIP
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

    launch = _check(results, "Launch application")
    assert snippet in launch.explanation
    if code == 0 and snippet == "Launched":
        assert launch.status is Status.PASS
        assert launch.log_path is None
    else:
        assert launch.status is Status.FAIL


def test_two_failures_in_the_same_second_get_distinct_log_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("android_runner.runner.datetime", _Clock)
    adb = _passing_adb({("pm", "path", _PACKAGE): _ok("", code=1)})
    adb.log_texts = ["battery-log\n", f"{_PACKAGE} install-log\n"]
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
    assert _check(results, "Application installation").log_path == install_log
    assert battery_log.read_text(encoding="utf-8") == "battery-log\n"
    assert install_log.read_text(encoding="utf-8") == f"{_PACKAGE} install-log\n"
    launch = _check(results, "Launch application")
    assert launch.status is Status.SKIP
    assert launch.log_path is None


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
    assert battery.log_error == "Log capture failed because logcat could not be read."
    assert list(logs.glob("*.log")) == []
    assert results[3].status is Status.PASS


def test_log_directory_that_is_a_file_does_not_abort_the_run(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.write_text("not a directory", encoding="utf-8")

    results = run_checks(_passing_adb(), _config(), _info(battery_percent=15), logs)

    assert results[2].status is Status.FAIL
    assert results[2].explanation.startswith("Battery level is 15%")
    assert results[2].log_error == (
        "Log capture failed because the log file could not be written."
    )
    assert results[3].status is Status.PASS


def test_every_explanation_is_one_line(tmp_path: Path) -> None:
    adb = _passing_adb({("echo", "hello"): _ok("nope")})

    results = run_checks(adb, _config(), _info(battery_percent=20), tmp_path / "logs")

    for result in results:
        assert "\n" not in result.explanation
        assert result.explanation.endswith(".")
        assert "Traceback" not in result.explanation


def test_device_clock_is_read_before_each_check(tmp_path: Path) -> None:
    adb = _passing_adb()

    run_checks(adb, _config(), _info(), tmp_path / "logs")

    assert [call[0] for call in adb.calls] == [
        "device_log_time",
        "shell",
        "device_log_time",
        "device_log_time",
        "device_log_time",
        "shell",
        "device_log_time",
        "shell",
        "device_log_time",
        "shell",
        "device_log_time",
        "shell",
        "device_log_time",
        "launch",
        "device_log_time",
        "shell",
    ]


def test_failure_uses_the_clock_read_before_that_check(tmp_path: Path) -> None:
    adb = _passing_adb()
    adb.log_times = [f"10-15 14:05:0{index}.000" for index in range(1, 10)]

    results = run_checks(adb, _config(), _info(battery_percent=15), tmp_path / "logs")

    assert [call for call in adb.calls if call[0] == "logcat"] == [
        ("logcat", "emulator-5554", "10-15 14:05:03.000")
    ]
    assert results[2].status is Status.FAIL
    assert results[2].log_note is None
    assert results[2].log_path is not None
    assert results[2].log_path.read_text(encoding="utf-8") == "log line\n"


def test_unreadable_device_clock_does_not_save_a_log(tmp_path: Path) -> None:
    adb = _passing_adb()
    adb.log_time = None

    results = run_checks(adb, _config(), _info(battery_percent=15), tmp_path / "logs")

    assert not any(call[0] == "logcat" for call in adb.calls)
    battery = results[2]
    assert battery.status is Status.FAIL
    assert battery.explanation == "Battery level is 15%, which is lower than 20%."
    assert battery.log_path is None
    assert battery.log_note is None
    assert battery.log_error == (
        "Log capture failed because the device clock could not be read."
    )


@pytest.mark.parametrize("window", ["", "\n", "   \n"])
def test_empty_time_window_is_saved_as_returned(tmp_path: Path, window: str) -> None:
    adb = _passing_adb()
    adb.window_texts = [window]
    adb.log_texts = ["recent\n"]

    results = run_checks(adb, _config(), _info(battery_percent=15), tmp_path / "logs")

    assert [call for call in adb.calls if call[0] == "logcat"] == [
        ("logcat", "emulator-5554", "10-15 14:05:00.000")
    ]
    battery = results[2]
    assert battery.status is Status.FAIL
    assert battery.log_note is None
    assert battery.log_error is None
    assert battery.log_path is not None
    assert battery.log_path.read_text(encoding="utf-8") == window


def test_battery_log_keeps_lines_outside_the_package(tmp_path: Path) -> None:
    log = f"unrelated\n{_PACKAGE}\n"
    adb = _passing_adb()
    adb.log_texts = [log]

    results = run_checks(adb, _config(), _info(battery_percent=15), tmp_path / "logs")

    assert results[2].log_path is not None
    assert results[2].log_path.read_text(encoding="utf-8") == log


def test_package_filter_keeps_crash_blocks_until_a_blank_line() -> None:
    text = "\n".join(
        [
            "noise before",
            "10-15 14:05:01.300  3  3 E AndroidRuntime: FATAL EXCEPTION: main",
            "10-15 14:05:01.301  3  3 E AndroidRuntime: Process: com.other.app",
            "\tat com.foo.Bar.run(Bar.java:4)",
            "",
            "noise after",
            f"started {_PACKAGE}",
        ]
    )

    assert _filter_package_log(text, _PACKAGE) == "\n".join(
        [
            "10-15 14:05:01.300  3  3 E AndroidRuntime: FATAL EXCEPTION: main",
            "10-15 14:05:01.301  3  3 E AndroidRuntime: Process: com.other.app",
            "\tat com.foo.Bar.run(Bar.java:4)",
            f"started {_PACKAGE}",
        ]
    ) + "\n"


def test_launch_log_filters_to_the_package_and_counts_kept_errors(
    tmp_path: Path,
) -> None:
    log = "\n".join(
        [
            "10-15 14:05:01.100  1  1 E Other: should be dropped",
            f"10-15 14:05:01.200  2  2 E App: failed {_PACKAGE}",
            "10-15 14:05:01.300  3  3 E AndroidRuntime: FATAL EXCEPTION: main",
            "10-15 14:05:01.301  3  3 E AndroidRuntime: \tat com.foo.Bar.run(Bar.java:4)",
            "",
            "10-15 14:05:01.400  4  4 E Other: after the crash",
        ]
    ) + "\n"
    adb = _passing_adb({("launch", _PACKAGE): _ok("", "monkey aborted", 1)})
    adb.log_texts = [log]

    results = run_checks(adb, _config(), _info(), tmp_path / "logs")

    launch = _check(results, "Launch application")
    assert launch.status is Status.FAIL
    assert "monkey aborted" in launch.explanation
    assert launch.log_path is not None
    saved = launch.log_path.read_text(encoding="utf-8")
    assert "should be dropped" not in saved
    assert "after the crash" not in saved
    assert f"failed {_PACKAGE}" in saved
    assert "FATAL EXCEPTION: main" in saved
    assert "Bar.run(Bar.java:4)" in saved
    assert launch.log_note == (
        f"3 error lines in this check. First: App: failed {_PACKAGE}."
    )


def test_unreadable_clock_leaves_an_app_check_failure_unchanged(
    tmp_path: Path,
) -> None:
    adb = _passing_adb({("pm", "path", _PACKAGE): _ok("", code=1)})
    adb.log_time = None

    results = run_checks(adb, _config(), _info(), tmp_path / "logs")

    install = _check(results, "Application installation")
    assert install.status is Status.FAIL
    assert install.explanation == f"Package {_PACKAGE} is not installed."
    assert install.log_path is None
    assert install.log_note is None
    assert install.log_error == (
        "Log capture failed because the device clock could not be read."
    )


def test_error_lines_do_not_change_the_check_result(tmp_path: Path) -> None:
    adb = _passing_adb()
    adb.log_texts = [
        "\n".join(
            [
                "10-15 14:05:01.200  10  10 E AndroidRuntime: FATAL EXCEPTION: main",
                "10-15 14:05:01.300  10  10 F libc: Fatal signal 11",
            ]
        )
        + "\n"
    ]

    results = run_checks(adb, _config(), _info(battery_percent=15), tmp_path / "logs")

    battery = results[2]
    assert battery.status is Status.FAIL
    assert battery.explanation == "Battery level is 15%, which is lower than 20%."
    assert battery.log_note == (
        "2 error lines in this check. First: AndroidRuntime: FATAL EXCEPTION: main."
    )


def test_error_note_counts_only_threadtime_error_and_fatal_lines() -> None:
    text = "\n".join(
        [
            "10-15 14:05:01.100  10  10 I ActivityManager: start",
            "10-15 14:05:01.200  10  10 E AndroidRuntime: FATAL EXCEPTION: main",
            "10-15 14:05:01.300  1234  5678 E chatty  : expire 1 line",
            "10-15 14:05:01.400  10  10 F libc: Fatal signal 11",
            "E not threadtime",
            "10-15 14:05:01.500  10  10 W Tag: warning",
        ]
    )

    assert _error_note(text) == (
        "3 error lines in this check. First: AndroidRuntime: FATAL EXCEPTION: main."
    )
    assert _error_note("10-15 14:05:01.200  10  10 E Tag: boom\n") == (
        "1 error line in this check. First: Tag: boom."
    )
    assert _error_note("nothing\n") is None


def test_storage_compares_truncated_mebibytes(tmp_path: Path) -> None:
    cases = [
        (204_800, 100, Status.PASS, "200 MB, which is greater than 100 MB"),
        (102_400, 100, Status.FAIL, "100 MB, which is equal to 100 MB"),
        (51_200, 100, Status.FAIL, "50 MB, which is lower than 100 MB"),
        (1023, 0, Status.FAIL, "0 MB, which is equal to 0 MB"),
        (1024, 0, Status.PASS, "1 MB, which is greater than 0 MB"),
    ]
    for available, minimum, status, snippet in cases:
        adb = _passing_adb({("df", "-k", "/data"): _ok(_df(available))})
        results = run_checks(
            adb,
            _config(minimum_free_storage_mb=minimum),
            _info(),
            tmp_path / "logs",
        )
        storage = _check(results, "Free storage")
        assert storage.status is status
        assert snippet in storage.explanation
        assert _check(results, "Launch application").status is Status.PASS


@pytest.mark.parametrize(
    ("stdout", "code", "snippet"),
    [
        ("Filesystem\nnot a table\n", 0, "could not be read"),
        ("/dev/data 3.7G 1.0G 2.7G 27% /data\n", 0, "could not be read"),
        ("", 1, "could not be read"),
    ],
)
def test_unreadable_storage_fails(
    tmp_path: Path,
    stdout: str,
    code: int,
    snippet: str,
) -> None:
    adb = _passing_adb({("df", "-k", "/data"): _ok(stdout, code=code)})

    results = run_checks(adb, _config(), _info(), tmp_path / "logs")

    storage = _check(results, "Free storage")
    assert storage.status is Status.FAIL
    assert snippet in storage.explanation


def test_storage_timeout_and_adb_error_have_their_own_sentences(tmp_path: Path) -> None:
    timed_out = _passing_adb(
        {("df", "-k", "/data"): AdbError("timed out", timed_out=True)}
    )
    failed = _passing_adb({("df", "-k", "/data"): AdbError("adb is not available")})

    timeout_result = _check(
        run_checks(timed_out, _config(), _info(), tmp_path / "logs"),
        "Free storage",
    )
    failed_result = _check(
        run_checks(failed, _config(), _info(), tmp_path / "logs"),
        "Free storage",
    )

    assert timeout_result.explanation == "The storage check timed out."
    assert failed_result.explanation == "The storage check failed."


def test_storage_uses_the_first_capacity_column(tmp_path: Path) -> None:
    text = (
        "Filesystem 1K-blocks Used Available Use% Mounted on\n"
        "/dev/small 100 1 1024 50% /data\n"
        "/dev/large 100 1 204800 10% /data\n"
    )
    adb = _passing_adb({("df", "-k", "/data"): _ok(text)})

    results = run_checks(adb, _config(), _info(), tmp_path / "logs")

    storage = _check(results, "Free storage")
    assert storage.status is Status.FAIL
    assert "1 MB" in storage.explanation


@pytest.mark.parametrize(
    ("stdout", "status", "snippet"),
    [
        ("default via 10.0.2.2 dev eth0\n", Status.PASS, "default network route is present"),
        ("  default via fe80::1 dev wlan0\n", Status.PASS, "default network route is present"),
        (
            "default dev dummy0 table 1002 proto static scope link\n"
            "default via 10.0.2.2 dev eth0 table 1015 proto static\n",
            Status.PASS,
            "default network route is present",
        ),
        (
            "10.0.2.0/24 dev eth0 proto kernel scope link src 10.0.2.15\n"
            "default dev dummy0 table 1002 proto static scope link\n",
            Status.FAIL,
            "cannot reach other networks",
        ),
        (
            "10.0.2.0/24 dev eth0 proto kernel scope link src 10.0.2.15\n",
            Status.FAIL,
            "cannot reach other networks",
        ),
        ("", Status.FAIL, "cannot reach other networks"),
        ("Cannot open netlink\n", Status.FAIL, "The network check failed."),
    ],
)
def test_network_route(
    tmp_path: Path,
    stdout: str,
    status: Status,
    snippet: str,
) -> None:
    code = 1 if stdout.startswith("Cannot") else 0
    adb = _passing_adb({("ip", "route", "show", "table", "all"): _ok(stdout, code=code)})

    results = run_checks(adb, _config(), _info(), tmp_path / "logs")

    network = _check(results, "Network")
    assert network.status is status
    assert snippet in network.explanation


def test_network_timeout_and_adb_error_have_their_own_sentences(tmp_path: Path) -> None:
    timed_out = _passing_adb(
        {("ip", "route", "show", "table", "all"): AdbError("timed out", timed_out=True)}
    )
    failed = _passing_adb(
        {("ip", "route", "show", "table", "all"): AdbError("adb is not available")}
    )

    timeout_result = _check(
        run_checks(timed_out, _config(), _info(), tmp_path / "logs"),
        "Network",
    )
    failed_result = _check(
        run_checks(failed, _config(), _info(), tmp_path / "logs"),
        "Network",
    )

    assert timeout_result.explanation == "The network check timed out."
    assert failed_result.explanation == "The network check failed."
    assert "route" not in timeout_result.explanation


@pytest.mark.parametrize(
    ("stdout", "status", "snippet"),
    [
        ("  mWakefulness=Awake\n", Status.PASS, "Screen state is Awake."),
        ("mWakefulness=1\n", Status.PASS, "Screen state is Awake."),
        ("  mWakefulness=Asleep\n", Status.FAIL, "Screen state is Asleep."),
        ("mWakefulness=Dozing\n", Status.FAIL, "Screen state is Dozing."),
        ("mWakefulness=Dreaming\n", Status.FAIL, "Screen state is Dreaming."),
        ("mWakefulness=0\n", Status.FAIL, "Screen state is Asleep."),
        ("mScreenOn=true\n", Status.PASS, "Screen state is Awake."),
        ("mScreenOn=false\n", Status.FAIL, "Screen state is Asleep."),
        (
            "mWakefulness=Unknown\nmScreenOn=true\n",
            Status.FAIL,
            "Screen state could not be read.",
        ),
        ("", Status.FAIL, "Screen state could not be read."),
        ("permission denied\n", Status.FAIL, "Screen state could not be read."),
        ("mWakefulness=2\n", Status.FAIL, "Screen state is Dreaming."),
        ("mWakefulness=3\n", Status.FAIL, "Screen state is Dozing."),
    ],
)
def test_screen_state(tmp_path: Path, stdout: str, status: Status, snippet: str) -> None:
    code = 1 if stdout == "permission denied\n" else 0
    adb = _passing_adb({("dumpsys", "power"): _ok(stdout, code=code)})

    results = run_checks(adb, _config(), _info(), tmp_path / "logs")

    screen = _check(results, "Screen state")
    assert screen.status is status
    assert screen.explanation == snippet


def test_screen_timeout_and_adb_error_have_their_own_sentences(tmp_path: Path) -> None:
    timed_out = _passing_adb(
        {("dumpsys", "power"): AdbError("timed out", timed_out=True)}
    )
    failed = _passing_adb({("dumpsys", "power"): AdbError("adb is not available")})

    timeout_result = _check(
        run_checks(timed_out, _config(), _info(), tmp_path / "logs"),
        "Screen state",
    )
    failed_result = _check(
        run_checks(failed, _config(), _info(), tmp_path / "logs"),
        "Screen state",
    )

    assert timeout_result.explanation == "The screen check timed out."
    assert failed_result.explanation == "The screen check failed."


def test_foreground_prefers_top_resumed_activity(tmp_path: Path) -> None:
    text = _activities(
        "mResumedActivity: ActivityRecord{abc u0 com.android.launcher3/.Launcher t2}",
        "topResumedActivity=ActivityRecord{def u0 com.android.settings/.Settings t7}",
    )
    adb = _passing_adb({("dumpsys", "activity", "activities"): _ok(text)})

    results = run_checks(adb, _config(), _info(), tmp_path / "logs")

    foreground = _check(results, "Foreground activity")
    assert foreground.status is Status.PASS
    assert foreground.explanation == "Foreground activity is com.android.settings/.Settings."


def test_foreground_uses_resumed_activity_when_top_is_absent(tmp_path: Path) -> None:
    text = _activities(
        "mResumedActivity: ActivityRecord{abc u0 com.android.settings/com.android.settings.Settings t7}"
    )
    adb = _passing_adb({("dumpsys", "activity", "activities"): _ok(text)})

    results = run_checks(adb, _config(), _info(), tmp_path / "logs")

    foreground = _check(results, "Foreground activity")
    assert foreground.status is Status.PASS
    assert (
        foreground.explanation
        == "Foreground activity is com.android.settings/com.android.settings.Settings."
    )


def test_foreground_names_a_different_package(tmp_path: Path) -> None:
    text = _activities(
        "topResumedActivity=ActivityRecord{abc u0 com.android.launcher3/.Launcher t2}"
    )
    adb = _passing_adb({("dumpsys", "activity", "activities"): _ok(text)})

    results = run_checks(adb, _config(), _info(), tmp_path / "logs")

    foreground = _check(results, "Foreground activity")
    assert foreground.status is Status.FAIL
    assert foreground.explanation == (
        "Foreground activity is com.android.launcher3/.Launcher, "
        f"not {_PACKAGE}."
    )


def test_foreground_runs_after_a_failed_launch(tmp_path: Path) -> None:
    adb = _passing_adb({("launch", _PACKAGE): _ok("", "monkey aborted", 1)})

    results = run_checks(adb, _config(), _info(), tmp_path / "logs")

    assert _check(results, "Launch application").status is Status.FAIL
    assert _check(results, "Foreground activity").status is Status.PASS
    shell_args = [call[2] for call in adb.calls if call[0] == "shell"]
    assert ("dumpsys", "activity", "activities") in shell_args


@pytest.mark.parametrize(
    ("stdout", "code", "snippet"),
    [
        ("no resumed activity\n", 0, "could not be read"),
        ("", 1, "could not be read"),
    ],
)
def test_unreadable_foreground_fails(
    tmp_path: Path,
    stdout: str,
    code: int,
    snippet: str,
) -> None:
    adb = _passing_adb({("dumpsys", "activity", "activities"): _ok(stdout, code=code)})

    results = run_checks(adb, _config(), _info(), tmp_path / "logs")

    foreground = _check(results, "Foreground activity")
    assert foreground.status is Status.FAIL
    assert snippet in foreground.explanation


def test_foreground_timeout_and_adb_error_have_their_own_sentences(tmp_path: Path) -> None:
    key = ("dumpsys", "activity", "activities")
    timed_out = _passing_adb({key: AdbError("timed out", timed_out=True)})
    failed = _passing_adb({key: AdbError("adb is not available")})

    timeout_result = _check(
        run_checks(timed_out, _config(), _info(), tmp_path / "logs"),
        "Foreground activity",
    )
    failed_result = _check(
        run_checks(failed, _config(), _info(), tmp_path / "logs"),
        "Foreground activity",
    )

    assert timeout_result.explanation == (
        f"The foreground check for {_PACKAGE} timed out."
    )
    assert failed_result.explanation == f"The foreground check for {_PACKAGE} failed."
