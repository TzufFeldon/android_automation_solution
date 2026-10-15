from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from android_runner.adb import AdbError, CommandResult, DeviceInfo
from main import main

_PACKAGE = "com.android.settings"


class ScriptedClient:
    def __init__(self, info: DeviceInfo) -> None:
        self.info = info
        self.device_id: str | None = None

    def select_device(self, device_id: str | None) -> str:
        self.device_id = device_id
        return self.info.serial

    def read_device_info(self, serial: str) -> DeviceInfo:
        assert serial == self.info.serial
        return self.info

    def shell(self, _serial: str, *args: str) -> CommandResult:
        if args == ("echo", "hello"):
            return CommandResult(0, "hello\n", "")
        if args == ("df", "-k", "/data"):
            return CommandResult(
                0,
                "Filesystem 1K-blocks Used Available Use% Mounted on\n"
                "/dev/block/data 999999 100 204800 10% /data\n",
                "",
            )
        if args == ("ip", "route", "show", "table", "all"):
            return CommandResult(0, "default via 10.0.2.2 dev eth0\n", "")
        if args == ("dumpsys", "power"):
            return CommandResult(0, "  mWakefulness=Awake\n", "")
        if args[:2] == ("pm", "path"):
            return CommandResult(0, f"package:{_PACKAGE}\n", "")
        if args == ("dumpsys", "activity", "activities"):
            return CommandResult(
                0,
                "  topResumedActivity=ActivityRecord{abc u0 "
                "com.android.settings/.Settings t7}\n",
                "",
            )
        raise AssertionError(args)

    def launch_package(self, _serial: str, _package_name: str) -> CommandResult:
        return CommandResult(0, "Events injected: 1\n", "")

    def device_log_time(self, _serial: str) -> str | None:
        return "10-15 14:05:00.000"

    def logcat(self, _serial: str, _since: str) -> str:
        return "captured\n"


def _write_config(directory: Path, **overrides: object) -> Path:
    payload: dict[str, object] = {
        "package_name": _PACKAGE,
        "minimum_android_version": 10,
        "minimum_battery": 20,
        "minimum_free_storage_mb": 100,
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


@pytest.fixture(autouse=True)
def _run_from_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


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

    code = main()

    captured = capsys.readouterr()
    assert code == 2
    assert "invalid JSON" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
    assert not report.exists()
    assert not (tmp_path / "logs").exists()


def test_adb_setup_error_exits_2_without_a_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingAdb:
        def select_device(self, _device_id: str | None) -> str:
            raise AdbError("No Android device or emulator is connected.")

    monkeypatch.setattr("main.AdbClient", MissingAdb)
    _write_config(tmp_path)

    code = main()

    captured = capsys.readouterr()
    assert code == 2
    assert "No Android device or emulator is connected." in captured.err
    assert "Traceback" not in captured.err
    assert not (tmp_path / "report.txt").exists()


_RUN_LINE = re.compile(
    r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} Overall: (?:PASS|FAIL); "
)


def test_passing_run_exits_0_and_writes_the_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = ScriptedClient(_info())
    monkeypatch.setattr("main.AdbClient", lambda: client)

    def forbid_sleep(_seconds: float) -> None:
        raise AssertionError("one-pass run slept")

    monkeypatch.setattr("main.time.sleep", forbid_sleep)
    _write_config(tmp_path, device_id="emulator-5554")

    code = main()

    report = (tmp_path / "report.txt").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    captured = capsys.readouterr()
    assert code == 0
    assert client.device_id == "emulator-5554"
    assert captured.out.startswith("Connected device: emulator-5554\n")
    assert _RUN_LINE.search(captured.out)
    assert report.startswith("Android Automation Report: emulator-5554\n")
    assert "Overall: PASS" in report
    assert "Device ID: emulator-5554" in report
    assert "Passed: 9" in report
    assert "Failed: 0" in report
    assert payload["overall"] == "PASS"
    assert payload["totals"]["passed"] == 9
    assert not (tmp_path / "logs").exists()


def test_failing_check_exits_1_and_writes_the_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = ScriptedClient(_info(battery=15))
    monkeypatch.setattr("main.AdbClient", lambda: client)
    _write_config(tmp_path)

    code = main()

    report = (tmp_path / "report.txt").read_text(encoding="utf-8")
    assert code == 1
    assert "    Battery: 15%\n    Minimum required: 20%" in report
    assert "Overall: FAIL" in report
    assert list((tmp_path / "logs").glob("*battery_level.log"))


def test_report_directory_exits_2_without_a_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = ScriptedClient(_info())
    monkeypatch.setattr("main.AdbClient", lambda: client)
    _write_config(tmp_path)
    report = tmp_path / "report.txt"
    report.mkdir()

    code = main()

    captured = capsys.readouterr()
    assert code == 2
    assert captured.err == "Report path is a directory: report.txt.\n"
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
        if len(args) >= 6 and args[4] == "date":
            return subprocess.CompletedProcess(args, 0, "10-15 14:05:00\n", "")
        if args[4:6] == ["echo", "hello"]:
            return subprocess.CompletedProcess(args, 0, "hello\n", "")
        if args[4:7] == ["df", "-k", "/data"]:
            stdout = (
                "Filesystem 1K-blocks Used Available Use% Mounted on\n"
                "/dev/block/data 999999 100 204800 10% /data\n"
            )
            return subprocess.CompletedProcess(args, 0, stdout, "")
        if args[4:9] == ["ip", "route", "show", "table", "all"]:
            return subprocess.CompletedProcess(args, 0, "default via 10.0.2.2 dev eth0\n", "")
        if args[4:6] == ["dumpsys", "power"]:
            return subprocess.CompletedProcess(args, 0, "  mWakefulness=Awake\n", "")
        if args[4:6] == ["pm", "path"]:
            return subprocess.CompletedProcess(args, 0, f"package:{_PACKAGE}\n", "")
        if args[4:7] == ["dumpsys", "activity", "activities"]:
            stdout = (
                "  topResumedActivity=ActivityRecord{abc u0 "
                "com.android.settings/.Settings t7}\n"
            )
            return subprocess.CompletedProcess(args, 0, stdout, "")
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
    _write_config(tmp_path)

    code = main()

    captured = capsys.readouterr()
    report = (tmp_path / "report.txt").read_text(encoding="utf-8")
    assert code == 0
    assert serials
    assert set(serials) == {chosen}
    assert "Device ID: emulator-5556" in report
    assert "emulator-5554" not in report
    assert "Choose a device [1-2]: " in captured.err
    assert "Connected device: emulator-5556\n" in captured.out
    assert _RUN_LINE.search(captured.out)
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
    _write_config(tmp_path, interval_seconds=30)

    code = main()

    captured = capsys.readouterr()
    report = (tmp_path / "report.txt").read_text(encoding="utf-8")
    assert code == 0
    assert sleeps == [30, 30]
    assert captured.out.count("Connected device: emulator-5554\n") == 1
    assert len(_RUN_LINE.findall(captured.out)) == 2
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
    _write_config(tmp_path, interval_seconds=15)

    code = main()

    captured = capsys.readouterr()
    report = (tmp_path / "report.txt").read_text(encoding="utf-8")
    assert code == 1
    assert "Overall: PASS" in captured.out
    assert "Overall: FAIL" in captured.out
    assert captured.err == "Stopped.\n"
    assert "    Battery: 15%\n    Minimum required: 20%" in report


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
    _write_config(tmp_path, interval_seconds=30)

    code = main()

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
    _write_config(tmp_path, interval_seconds=30)

    code = main()

    captured = capsys.readouterr()
    assert code == 2
    assert captured.err == "Stopped before a report was written.\n"
    assert "Traceback" not in captured.err
    assert not (tmp_path / "report.txt").exists()
    assert not (tmp_path / "report.json").exists()


def test_interval_stops_when_a_later_pass_cannot_start(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Disconnects(ScriptedClient):
        def __init__(self) -> None:
            super().__init__(_info())
            self.selections = 0

        def select_device(self, _device_id: str | None) -> str:
            self.selections += 1
            if self.selections == 2:
                raise AdbError("No Android device or emulator is connected.")
            return self.info.serial

    client = Disconnects()
    monkeypatch.setattr("main.AdbClient", lambda: client)

    def record_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("main.time.sleep", record_sleep)
    _write_config(tmp_path, interval_seconds=30)

    code = main()

    captured = capsys.readouterr()
    assert code == 2
    assert "Overall: PASS" in captured.out
    assert "No Android device or emulator is connected." in captured.err
    assert "Stopped." not in captured.err
    assert (tmp_path / "report.txt").exists()


def test_unexpected_error_propagates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Broken:
        def select_device(self, _device_id: str | None) -> str:
            raise RuntimeError("bug in device selection")

    monkeypatch.setattr("main.AdbClient", Broken)
    _write_config(tmp_path)

    with pytest.raises(RuntimeError, match="bug in device selection"):
        main()
