from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from android_runner.adb import AdbError, CommandResult, DeviceInfo
from main import main

_PACKAGE = "com.android.settings"


class ScriptedClient:
    def __init__(self, info: DeviceInfo, fail_install: bool = False) -> None:
        self.info = info
        self.fail_install = fail_install
        self.device_id: str | None = None
        self.calls: list[tuple[object, ...]] = []

    def select_device(self, device_id: str | None) -> str:
        self.device_id = device_id
        return self.info.serial

    def read_device_info(self, serial: str) -> DeviceInfo:
        assert serial == self.info.serial
        return self.info

    def shell(self, serial: str, *args: str) -> CommandResult:
        self.calls.append(("shell", serial, args))
        if args[:2] == ("pm", "path") and self.fail_install:
            return CommandResult(1, "", "not found")
        if args == ("echo", "hello"):
            return CommandResult(0, "hello\n", "")
        if args[:2] == ("pm", "path"):
            return CommandResult(0, f"package:{_PACKAGE}\n", "")
        raise AssertionError(args)

    def launch_package(self, serial: str, package_name: str) -> CommandResult:
        self.calls.append(("launch", serial, package_name))
        return CommandResult(0, "Events injected: 1\n", "")

    def logcat(self, serial: str) -> str:
        self.calls.append(("logcat", serial))
        return "captured\n"


def _write_config(directory: Path, **overrides: object) -> Path:
    payload: dict[str, object] = {
        "package_name": _PACKAGE,
        "minimum_android_version": 10,
        "minimum_battery": 20,
    }
    payload.update(overrides)
    path = directory / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _info(battery: int = 86) -> DeviceInfo:
    return DeviceInfo(
        serial="emulator-5554",
        android_version="14",
        android_major=14,
        model="sdk_gphone64_arm64",
        manufacturer="Google",
        battery_percent=battery,
    )


def _argv(config: Path, root: Path) -> list[str]:
    return [
        "--config",
        str(config),
        "--report",
        str(root / "report.txt"),
        "--logs",
        str(root / "logs"),
    ]


def test_broken_config_exits_2_without_a_traceback_or_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbid_adb() -> None:
        raise AssertionError("ADB started before configuration was accepted")

    monkeypatch.setattr("main.AdbClient", forbid_adb)
    config = tmp_path / "config.json"
    config.write_text("{", encoding="utf-8")
    report = tmp_path / "report.txt"

    code = main(_argv(config, tmp_path))

    captured = capsys.readouterr()
    assert code == 2
    assert "invalid JSON" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
    assert not report.exists()
    assert not (tmp_path / "logs").exists()


def test_missing_field_names_the_field_on_stderr(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"minimum_android_version": 10, "minimum_battery": 20}),
        encoding="utf-8",
    )

    code = main(_argv(config, tmp_path))

    captured = capsys.readouterr()
    assert code == 2
    assert "package_name" in captured.err
    assert "Traceback" not in captured.err
    assert not (tmp_path / "report.txt").exists()


def test_adb_setup_error_exits_2_without_a_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingAdb:
        def select_device(self, _device_id: str | None) -> str:
            raise AdbError("No Android device or emulator is connected.")

    monkeypatch.setattr("main.AdbClient", MissingAdb)
    config = _write_config(tmp_path)

    code = main(_argv(config, tmp_path))

    captured = capsys.readouterr()
    assert code == 2
    assert "No Android device or emulator is connected." in captured.err
    assert "Traceback" not in captured.err
    assert not (tmp_path / "report.txt").exists()


def test_several_ready_devices_exit_2_without_a_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_which(name: str) -> str:
        assert name == "adb"
        return "/usr/bin/adb"

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args == ["adb", "devices"]
        stdout = (
            "List of devices attached\n"
            "emulator-5554\tdevice\n"
            "emulator-5556\tdevice\n"
        )
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr("android_runner.adb.shutil.which", fake_which)
    monkeypatch.setattr("android_runner.adb.subprocess.run", fake_run)
    monkeypatch.setattr("android_runner.adb.sys.stdin.isatty", lambda: False)
    config = _write_config(tmp_path)

    code = main(_argv(config, tmp_path))

    captured = capsys.readouterr()
    assert code == 2
    assert captured.err == (
        "More than one Android device is ready (emulator-5554, emulator-5556); "
        "set device_id in config.json to one of these serials.\n"
    )
    assert "Traceback" not in captured.err
    assert not (tmp_path / "report.txt").exists()


def test_passing_run_exits_0_and_writes_the_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = ScriptedClient(_info())
    monkeypatch.setattr("main.AdbClient", lambda: client)
    config = _write_config(tmp_path, device_id="emulator-5554")

    code = main(_argv(config, tmp_path))

    report = (tmp_path / "report.txt").read_text(encoding="utf-8")
    assert code == 0
    assert client.device_id == "emulator-5554"
    assert "Overall: PASS" in report
    assert "Device ID: emulator-5554" in report
    assert "Passed: 5" in report
    assert "Failed: 0" in report
    assert not (tmp_path / "logs").exists()


def test_failing_check_exits_1_and_writes_the_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = ScriptedClient(_info(battery=15))
    monkeypatch.setattr("main.AdbClient", lambda: client)
    config = _write_config(tmp_path)

    code = main(_argv(config, tmp_path))

    report = (tmp_path / "report.txt").read_text(encoding="utf-8")
    assert code == 1
    assert "Battery level is 15%, which is lower than 20%." in report
    assert "Overall: FAIL" in report
    assert list((tmp_path / "logs").glob("*battery_level.log"))


def test_report_directory_exits_2_without_a_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = ScriptedClient(_info())
    monkeypatch.setattr("main.AdbClient", lambda: client)
    config = _write_config(tmp_path)
    report = tmp_path / "report.txt"
    report.mkdir()

    code = main(
        [
            "--config",
            str(config),
            "--report",
            str(report),
            "--logs",
            str(tmp_path / "logs"),
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.err == f"Report path is a directory: {report}.\n"
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_terminal_choice_is_the_device_checked_in_that_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chosen = "emulator-5556"
    serials: list[str] = []

    def fake_which(name: str) -> str:
        assert name == "adb"
        return "/usr/bin/adb"

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if args == ["adb", "devices"]:
            stdout = (
                "List of devices attached\n"
                "emulator-5554\tdevice\n"
                "emulator-5556\tdevice\n"
            )
            return subprocess.CompletedProcess(args, 0, stdout, "")
        serial = args[2]
        serials.append(serial)
        if args[4:6] == ["getprop", "ro.build.version.release"]:
            return subprocess.CompletedProcess(args, 0, "14\n", "")
        if args[4:6] == ["getprop", "ro.product.model"]:
            return subprocess.CompletedProcess(args, 0, "sdk\n", "")
        if args[4:6] == ["getprop", "ro.product.manufacturer"]:
            return subprocess.CompletedProcess(args, 0, "Google\n", "")
        if args[4:6] == ["dumpsys", "battery"]:
            return subprocess.CompletedProcess(args, 0, "  level: 86\n  scale: 100\n", "")
        if args[4:6] == ["echo", "hello"]:
            return subprocess.CompletedProcess(args, 0, "hello\n", "")
        if args[4:6] == ["pm", "path"]:
            return subprocess.CompletedProcess(args, 0, f"package:{_PACKAGE}\n", "")
        if args[4] == "monkey":
            return subprocess.CompletedProcess(args, 0, "Events injected: 1\n", "")
        raise AssertionError(args)

    class _Stdin:
        def isatty(self) -> bool:
            return True

        def readline(self) -> str:
            return "2\n"

    monkeypatch.setattr("android_runner.adb.shutil.which", fake_which)
    monkeypatch.setattr("android_runner.adb.subprocess.run", fake_run)
    monkeypatch.setattr("android_runner.adb.sys.stdin", _Stdin())
    config = _write_config(tmp_path)

    code = main(_argv(config, tmp_path))

    captured = capsys.readouterr()
    report = (tmp_path / "report.txt").read_text(encoding="utf-8")
    assert code == 0
    assert serials
    assert set(serials) == {chosen}
    assert "Device ID: emulator-5556" in report
    assert "emulator-5554" not in report
    assert "Choose a device [1-2]: " in captured.err
    assert "Overall: PASS" in captured.out


def test_interval_repeats_until_stopped(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = ScriptedClient(_info())
    monkeypatch.setattr("main.AdbClient", lambda: client)
    sleeps: list[float] = []

    def stop_on_second_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("main.time.sleep", stop_on_second_sleep)
    config = _write_config(tmp_path, interval_seconds=30)

    code = main(_argv(config, tmp_path))

    captured = capsys.readouterr()
    report = (tmp_path / "report.txt").read_text(encoding="utf-8")
    assert code == 0
    assert sleeps == [30, 30]
    assert captured.out.count("Overall: PASS") == 2
    assert captured.err == "Stopped.\n"
    assert "Traceback" not in captured.err
    assert "Overall: PASS" in report


def test_interval_keeps_the_last_completed_exit_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DroppingBattery(ScriptedClient):
        def __init__(self) -> None:
            super().__init__(_info())
            self.reads = 0

        def read_device_info(self, serial: str) -> DeviceInfo:
            assert serial == self.info.serial
            self.reads += 1
            if self.reads == 2:
                return _info(battery=15)
            return self.info

    client = DroppingBattery()
    monkeypatch.setattr("main.AdbClient", lambda: client)

    def stop_after_the_failed_pass(seconds: float) -> None:
        if seconds != 15:
            raise AssertionError(seconds)
        if client.reads >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("main.time.sleep", stop_after_the_failed_pass)
    config = _write_config(tmp_path, interval_seconds=15)

    code = main(_argv(config, tmp_path))

    captured = capsys.readouterr()
    report = (tmp_path / "report.txt").read_text(encoding="utf-8")
    assert code == 1
    assert "Overall: PASS" in captured.out
    assert "Overall: FAIL" in captured.out
    assert captured.err == "Stopped.\n"
    assert "Battery level is 15%, which is lower than 20%." in report


def test_interval_reuses_the_serial_chosen_on_the_first_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Choosing(ScriptedClient):
        def __init__(self) -> None:
            super().__init__(_info())
            self.ids: list[str | None] = []

        def select_device(self, device_id: str | None) -> str:
            self.ids.append(device_id)
            if device_id is None:
                return "emulator-5556"
            return device_id

        def read_device_info(self, serial: str) -> DeviceInfo:
            info = _info()
            return DeviceInfo(
                serial=serial,
                android_version=info.android_version,
                android_major=info.android_major,
                model=info.model,
                manufacturer=info.manufacturer,
                battery_percent=info.battery_percent,
            )

    client = Choosing()
    monkeypatch.setattr("main.AdbClient", lambda: client)
    passes = 0

    def stop_after_two(_seconds: float) -> None:
        nonlocal passes
        passes += 1
        if passes == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("main.time.sleep", stop_after_two)
    config = _write_config(tmp_path, interval_seconds=30)

    code = main(_argv(config, tmp_path))

    assert code == 0
    assert client.ids == [None, "emulator-5556"]


def test_interrupt_before_the_first_report_exits_2(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Interrupted:
        def select_device(self, _device_id: str | None) -> str:
            raise KeyboardInterrupt

    monkeypatch.setattr("main.AdbClient", Interrupted)
    config = _write_config(tmp_path, interval_seconds=30)

    code = main(_argv(config, tmp_path))

    captured = capsys.readouterr()
    assert code == 2
    assert captured.err == "Stopped before a report was written.\n"
    assert "Traceback" not in captured.err
    assert not (tmp_path / "report.txt").exists()


def test_interval_stops_when_a_later_pass_cannot_start(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Disconnects(ScriptedClient):
        def __init__(self) -> None:
            super().__init__(_info())
            self.selections = 0

        def select_device(self, device_id: str | None) -> str:
            self.selections += 1
            self.device_id = device_id
            if self.selections == 2:
                raise AdbError("No Android device or emulator is connected.")
            return self.info.serial

    client = Disconnects()
    monkeypatch.setattr("main.AdbClient", lambda: client)

    def record_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("main.time.sleep", record_sleep)
    config = _write_config(tmp_path, interval_seconds=30)

    code = main(_argv(config, tmp_path))

    captured = capsys.readouterr()
    assert code == 2
    assert "Overall: PASS" in captured.out
    assert "No Android device or emulator is connected." in captured.err
    assert "Stopped." not in captured.err
    assert (tmp_path / "report.txt").exists()


def test_one_pass_does_not_sleep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = ScriptedClient(_info())
    monkeypatch.setattr("main.AdbClient", lambda: client)

    def forbid_sleep(_seconds: float) -> None:
        raise AssertionError("one-pass run slept")

    monkeypatch.setattr("main.time.sleep", forbid_sleep)
    config = _write_config(tmp_path)

    code = main(_argv(config, tmp_path))

    assert code == 0
    assert client.calls.count(("shell", "emulator-5554", ("echo", "hello"))) == 1


def test_unexpected_error_propagates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Broken:
        def select_device(self, _device_id: str | None) -> str:
            raise RuntimeError("bug in device selection")

    monkeypatch.setattr("main.AdbClient", Broken)
    config = _write_config(tmp_path)

    with pytest.raises(RuntimeError, match="bug in device selection"):
        main(_argv(config, tmp_path))
