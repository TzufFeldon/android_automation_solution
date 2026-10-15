from __future__ import annotations

import subprocess
from collections.abc import Mapping

import pytest

from android_runner import adb as adb_module
from android_runner.adb import AdbClient, AdbError, DeviceRecord

_SERIAL = "emulator-5554"
_OTHER = "emulator-5556"


def _client(
    monkeypatch: pytest.MonkeyPatch,
    responses: Mapping[tuple[str, ...], object],
) -> tuple[AdbClient, list[list[str]]]:
    calls: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        assert name == "adb"
        return "/usr/bin/adb"

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs.get("shell") is not True
        assert kwargs["timeout"] == 20
        assert kwargs["check"] is False
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        calls.append(list(args))
        key = tuple(args)
        if key not in responses:
            raise AssertionError(f"unexpected command {args}")
        outcome = responses[key]
        if isinstance(outcome, BaseException):
            raise outcome
        code, stdout, stderr = outcome  # type: ignore[misc]
        return subprocess.CompletedProcess(args, code, stdout, stderr)

    monkeypatch.setattr(adb_module.shutil, "which", fake_which)
    monkeypatch.setattr(adb_module.subprocess, "run", fake_run)
    return AdbClient(), calls


def _terminal(monkeypatch: pytest.MonkeyPatch, lines: list[str]) -> list[str]:
    pending = list(lines)

    class _Stdin:
        def isatty(self) -> bool:
            return True

        def readline(self) -> str:
            if not pending:
                return ""
            return pending.pop(0)

    monkeypatch.setattr(adb_module.sys, "stdin", _Stdin())
    return pending


def _devices(*rows: str) -> dict[tuple[str, ...], object]:
    body = "List of devices attached\n" + "\n".join(rows) + "\n"
    return {("adb", "devices"): (0, body, "")}


def _info(
    version: object = (0, "14\n", ""),
    model: object = (0, "Pixel 7\n", ""),
    manufacturer: object = (0, "Google\n", ""),
    battery: object = (0, "  level: 86\n  scale: 100\n", ""),
) -> dict[tuple[str, ...], object]:
    return {
        ("adb", "-s", _SERIAL, "shell", "getprop", "ro.build.version.release"): version,
        ("adb", "-s", _SERIAL, "shell", "getprop", "ro.product.model"): model,
        ("adb", "-s", _SERIAL, "shell", "getprop", "ro.product.manufacturer"): manufacturer,
        ("adb", "-s", _SERIAL, "shell", "dumpsys", "battery"): battery,
    }


def test_device_list_ignores_header_blanks_daemon_lines_and_extra_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = (
        "* daemon not running; starting now at tcp:5037 *\n"
        "* daemon started successfully *\n"
        "List of devices attached\n"
        "\n"
        f"{_SERIAL}\tdevice product:sdk model:emu\n"
        "orphan-serial\n"
        f"{_OTHER}\toffline\n"
    )
    client, _calls = _client(monkeypatch, {("adb", "devices"): (0, stdout, "")})

    assert client.list_devices() == [
        DeviceRecord(_SERIAL, "device"),
        DeviceRecord(_OTHER, "offline"),
    ]


def test_single_ready_device_is_selected_when_another_is_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, calls = _client(
        monkeypatch,
        _devices(f"{_SERIAL}\tunauthorized", f"{_OTHER}\tdevice"),
    )

    assert client.select_device(None) == _OTHER
    assert calls == [["adb", "devices"]]


def test_no_device_lines_report_that_nothing_is_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _calls = _client(
        monkeypatch,
        {("adb", "devices"): (0, "List of devices attached\n* daemon started *\n", "")},
    )

    with pytest.raises(AdbError, match="No Android device or emulator is connected"):
        client.select_device(None)


def test_unauthorized_device_names_the_serial_and_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _calls = _client(monkeypatch, _devices(f"{_SERIAL}\tunauthorized"))

    with pytest.raises(AdbError, match=rf"{_SERIAL} \(unauthorized\)") as caught:
        client.select_device("")

    message = str(caught.value)
    assert "accept the USB debugging prompt" in message
    assert "offline" not in message
    assert "Traceback" not in message
    assert "\n" not in message


def test_offline_device_names_the_serial_and_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _calls = _client(monkeypatch, _devices(f"{_SERIAL}\toffline"))

    with pytest.raises(AdbError, match=rf"{_SERIAL} \(offline\)") as caught:
        client.select_device(None)

    message = str(caught.value)
    assert "reconnect a device that is offline" in message
    assert "USB debugging" not in message


def test_unauthorized_and_offline_corrections_are_joined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _calls = _client(
        monkeypatch,
        _devices(f"{_SERIAL}\tunauthorized", f"{_OTHER}\toffline"),
    )

    with pytest.raises(AdbError, match="none is ready") as caught:
        client.select_device(None)

    assert str(caught.value) == (
        "Android devices are connected but none is ready: "
        f"{_SERIAL} (unauthorized), {_OTHER} (offline); "
        "accept the USB debugging prompt on the device, or "
        "reconnect a device that is offline."
    )


def test_other_device_state_uses_the_generic_reconnect_sentence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _calls = _client(monkeypatch, _devices(f"{_SERIAL}\trecovery"))

    with pytest.raises(AdbError, match="none is ready") as caught:
        client.select_device(None)

    assert str(caught.value) == (
        "Android devices are connected but none is ready: "
        f"{_SERIAL} (recovery); reconnect the device and confirm that "
        "adb devices shows state device."
    )


def test_several_ready_devices_require_a_terminal_when_stdin_is_not_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _calls = _client(
        monkeypatch,
        _devices(f"{_SERIAL}\tdevice", f"{_OTHER}\tdevice", "ABC\tunauthorized"),
    )
    monkeypatch.setattr(adb_module.sys.stdin, "isatty", lambda: False)

    with pytest.raises(AdbError, match="run in a terminal") as caught:
        client.select_device(None)

    message = str(caught.value)
    assert message == (
        "More than one Android device is ready "
        f"({_SERIAL}, {_OTHER}); run in a terminal to choose one, "
        "or set device_id in config.json."
    )


def test_several_ready_devices_prompt_on_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client, _calls = _client(
        monkeypatch,
        _devices(f"{_SERIAL}\tdevice", f"{_OTHER}\tdevice", "ABC\tunauthorized"),
    )
    _terminal(monkeypatch, ["2\n"])

    assert client.select_device(None) == _OTHER

    captured = capsys.readouterr()
    assert captured.err == (
        "Several Android devices are ready:\n"
        f"  1. {_SERIAL}\n"
        f"  2. {_OTHER}\n"
        "Choose a device [1-2]: "
    )
    assert "ABC" not in captured.err


def test_prompt_accepts_a_serial_after_an_invalid_choice(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client, _calls = _client(
        monkeypatch,
        _devices(f"{_SERIAL}\tdevice", f"{_OTHER}\tdevice"),
    )
    _terminal(monkeypatch, ["9\n", " \n", f"{_SERIAL}\n"])

    assert client.select_device(None) == _SERIAL

    captured = capsys.readouterr()
    assert captured.err.count("Enter a number from 1-2, or a serial from the list.") == 2


def test_prompt_treats_a_number_as_an_index_when_it_is_also_a_serial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _calls = _client(
        monkeypatch,
        _devices(f"{_SERIAL}\tdevice", "1\tdevice"),
    )
    _terminal(monkeypatch, ["1\n"])

    assert client.select_device(None) == _SERIAL


def test_prompt_without_a_choice_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _calls = _client(
        monkeypatch,
        _devices(f"{_SERIAL}\tdevice", f"{_OTHER}\tdevice"),
    )
    _terminal(monkeypatch, [""])

    with pytest.raises(AdbError, match="no choice was entered") as caught:
        client.select_device(None)

    assert "Traceback" not in str(caught.value)
    assert "\n" not in str(caught.value)


def test_each_prompt_is_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, calls = _client(
        monkeypatch,
        _devices(f"{_SERIAL}\tdevice", f"{_OTHER}\tdevice"),
    )
    answers = _terminal(monkeypatch, ["1\n", "2\n"])

    assert client.select_device(None) == _SERIAL
    assert client.select_device(None) == _OTHER

    assert answers == []
    assert calls == [["adb", "devices"], ["adb", "devices"]]


def test_device_id_selects_its_ready_device_among_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _calls = _client(
        monkeypatch,
        _devices(f"{_SERIAL}\tdevice", f"{_OTHER}\tdevice"),
    )

    def forbid_read() -> str:
        raise AssertionError("device_id prompted")

    class _Stdin:
        def isatty(self) -> bool:
            return True

        def readline(self) -> str:
            return forbid_read()

    monkeypatch.setattr(adb_module.sys, "stdin", _Stdin())

    assert client.select_device(_OTHER) == _OTHER


def test_device_id_does_not_fall_back_to_a_different_ready_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _calls = _client(
        monkeypatch,
        _devices(f"{_SERIAL}\tdevice", f"{_OTHER}\toffline"),
    )

    with pytest.raises(AdbError, match=f"{_OTHER} is connected with state offline") as caught:
        client.select_device(_OTHER)

    assert "reconnect a device that is offline" in str(caught.value)
    assert "USB debugging" not in str(caught.value)


def test_missing_device_id_lists_the_connected_devices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _calls = _client(monkeypatch, _devices(f"{_SERIAL}\tdevice"))

    with pytest.raises(AdbError, match="was not found") as caught:
        client.select_device("missing-serial")

    assert f"{_SERIAL} (device)" in str(caught.value)


def test_missing_device_id_with_no_devices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _calls = _client(monkeypatch, {("adb", "devices"): (0, "", "")})

    with pytest.raises(AdbError, match="no Android device or emulator is connected"):
        client.select_device(_SERIAL)


def test_nonzero_adb_devices_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _calls = _client(monkeypatch, {("adb", "devices"): (1, "", "")})

    with pytest.raises(AdbError, match="exit code 1"):
        client.list_devices()


def test_device_info_parses_version_model_and_battery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, calls = _client(monkeypatch, _info(version=(0, "8.1.0\n", "")))

    info = client.read_device_info(_SERIAL)

    assert info.serial == _SERIAL
    assert info.android_version == "8.1.0"
    assert info.android_major == 8
    assert info.model == "Pixel 7"
    assert info.manufacturer == "Google"
    assert info.battery_percent == 86
    assert calls[0] == [
        "adb",
        "-s",
        _SERIAL,
        "shell",
        "getprop",
        "ro.build.version.release",
    ]


@pytest.mark.parametrize(
    ("version", "stored", "major"),
    [
        ("14", "14", 14),
        ("8.1.0", "8.1.0", 8),
        ("Next", "Next", None),
        ("v14", "v14", None),
    ],
)
def test_version_strings(
    monkeypatch: pytest.MonkeyPatch,
    version: str,
    stored: str,
    major: int | None,
) -> None:
    client, _calls = _client(monkeypatch, _info(version=(0, f"{version}\n", "")))

    info = client.read_device_info(_SERIAL)

    assert info.android_version == stored
    assert info.android_major == major


def test_blank_or_failed_fields_are_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    client, calls = _client(
        monkeypatch,
        _info(
            version=(1, "", "failed"),
            model=(0, "   \n", ""),
            manufacturer=(0, "", ""),
            battery=(0, "scale: 100\n", ""),
        ),
    )

    info = client.read_device_info(_SERIAL)

    assert info.android_version is None
    assert info.android_major is None
    assert info.model is None
    assert info.manufacturer is None
    assert info.battery_percent is None
    assert len(calls) == 4


@pytest.mark.parametrize(
    ("dumpsys", "percent"),
    [
        ("level: 40\n", 40),
        ("level: 0\nscale: 100\n", 0),
        ("level: 100\n", 100),
        ("level: 2\nscale: 3\n", 67),
        ("level: 1\nscale: 8\n", 12),
        ("level: 50\nscale: 50\n", 100),
        ("level: 0\nscale: 50\n", 0),
        ("level: 50\nscale: 0\n", None),
        ("level: -1\n", None),
        ("level: 101\n", None),
        ("level: 250\nscale: 50\n", None),
        ("level: -5\nscale: 50\n", None),
        ("scale: 100\n", None),
        ("level: 86%\n", None),
        ("  level: 15\r\n  scale: 100\r\n", 15),
    ],
)
def test_battery_parsing(
    monkeypatch: pytest.MonkeyPatch,
    dumpsys: str,
    percent: int | None,
) -> None:
    client, _calls = _client(monkeypatch, _info(battery=(0, dumpsys, "")))

    assert client.read_device_info(_SERIAL).battery_percent == percent


def test_one_timed_out_property_does_not_drop_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeout = subprocess.TimeoutExpired(["adb"], 20)
    client, calls = _client(monkeypatch, _info(version=timeout))

    info = client.read_device_info(_SERIAL)

    assert info.android_version is None
    assert info.model == "Pixel 7"
    assert info.battery_percent == 86
    assert len(calls) == 4


def test_non_timeout_adb_error_while_reading_device_info_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, calls = _client(
        monkeypatch,
        _info(battery=AdbError("adb is not available")),
    )

    with pytest.raises(AdbError, match="adb is not available") as caught:
        client.read_device_info(_SERIAL)

    assert caught.value.timed_out is False
    assert len(calls) == 4


def test_shell_returns_a_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    command = ("adb", "-s", _SERIAL, "shell", "pm", "path", "com.android.settings")
    client, _calls = _client(monkeypatch, {command: (1, "", "not found")})

    result = client.shell(_SERIAL, "pm", "path", "com.android.settings")

    assert result.returncode == 1
    assert result.stderr == "not found"


def test_launch_package_uses_monkey_without_a_shell_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = (
        "adb",
        "-s",
        _SERIAL,
        "shell",
        "monkey",
        "-p",
        "com.android.settings",
        "-c",
        "android.intent.category.LAUNCHER",
        "1",
    )
    client, calls = _client(monkeypatch, {command: (0, "Events injected: 1\n", "")})

    result = client.launch_package(_SERIAL, "com.android.settings")

    assert result.returncode == 0
    assert calls == [list(command)]


def test_device_log_time_appends_millis(monkeypatch: pytest.MonkeyPatch) -> None:
    command = ("adb", "-s", _SERIAL, "shell", "date", "+%m-%dT%H:%M:%S")
    client, calls = _client(monkeypatch, {command: (0, "10-15T14:05:00\n", "")})

    assert client.device_log_time(_SERIAL) == "10-15 14:05:00.000"
    assert calls == [list(command)]


@pytest.mark.parametrize(
    "outcome",
    [
        (1, "", "bad date"),
        (0, "\n", ""),
        (0, "Thu Oct 15 14:05:00 IST 2026\n", ""),
        subprocess.TimeoutExpired(["adb"], 20),
    ],
)
def test_device_log_time_returns_none_when_the_clock_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
    outcome: object,
) -> None:
    command = ("adb", "-s", _SERIAL, "shell", "date", "+%m-%dT%H:%M:%S")
    client, _calls = _client(monkeypatch, {command: outcome})

    assert client.device_log_time(_SERIAL) is None


def test_logcat_returns_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    since = "10-15 14:05:00.000"
    command = ("adb", "-s", _SERIAL, "logcat", "-d", "-t", since)
    client, calls = _client(monkeypatch, {command: (0, "log line\n", "")})

    assert client.logcat(_SERIAL, since) == "log line\n"
    assert calls == [list(command)]
    assert "-c" not in calls[0]
    assert "--pid" not in calls[0]
    assert "2000" not in calls[0]


def test_logcat_nonzero_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    since = "10-15 14:05:00.000"
    command = ("adb", "-s", _SERIAL, "logcat", "-d", "-t", since)
    client, _calls = _client(monkeypatch, {command: (2, "", "busy")})

    with pytest.raises(AdbError, match="exit code 2: busy") as caught:
        client.logcat(_SERIAL, since)

    assert caught.value.timed_out is False


def test_timeout_raises_adb_error(monkeypatch: pytest.MonkeyPatch) -> None:
    timeout = subprocess.TimeoutExpired(["adb", "devices"], 20)
    client, _calls = _client(monkeypatch, {("adb", "devices"): timeout})

    with pytest.raises(AdbError, match="timed out after 20 seconds") as caught:
        client.list_devices()

    assert caught.value.timed_out is True
    assert "\n" not in str(caught.value)


def test_missing_adb_does_not_start_a_process(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_which(name: str) -> None:
        assert name == "adb"
        return None

    def fake_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("adb was started")

    monkeypatch.setattr(adb_module.shutil, "which", fake_which)
    monkeypatch.setattr(adb_module.subprocess, "run", fake_run)

    with pytest.raises(AdbError, match="Platform-Tools") as caught:
        AdbClient().list_devices()

    message = str(caught.value)
    assert "PATH" in message
    assert "adb version" in message


def test_file_not_found_uses_the_same_missing_adb_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _calls = _client(
        monkeypatch,
        {("adb", "devices"): FileNotFoundError("adb")},
    )

    with pytest.raises(AdbError, match="Platform-Tools"):
        client.list_devices()
