"""ADB commands and device selection."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

TIMEOUT_SECONDS = 20

_ADB_MISSING = (
    "Android SDK Platform-Tools is not available; install it, put "
    "platform-tools on PATH, and verify with adb version."
)
_LEVEL_LINE = re.compile(r"^level:\s*(-?\d+)$")
_SCALE_LINE = re.compile(r"^scale:\s*(-?\d+)$")
_MAJOR_VERSION = re.compile(r"(\d+)")
_DEVICE_TIME = re.compile(r"^\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


class AdbError(Exception):
    """ADB failure. ``timed_out`` is set when the command hits the time limit."""

    def __init__(self, message: str, *, timed_out: bool = False) -> None:
        super().__init__(message)
        self.timed_out = timed_out


@dataclass(frozen=True, slots=True)
class DeviceRecord:
    """One ``adb devices`` row."""

    serial: str
    state: str


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Collected device facts. Unread fields are ``None``."""

    serial: str
    android_version: str | None
    android_major: int | None
    model: str | None
    manufacturer: str | None
    battery_percent: int | None


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Stdout, stderr, and exit code of one ADB command."""

    returncode: int
    stdout: str
    stderr: str


class AdbClient:
    """Runs ADB through ``subprocess``."""

    def list_devices(self) -> list[DeviceRecord]:
        """Return every parsed device row."""
        args = ["devices"]
        result = self._run(args)
        if result.returncode != 0:
            raise AdbError(_failure_message(args, result))
        return _parse_devices(result.stdout)

    def select_device(self, device_id: str | None) -> str:
        """Return the serial to check.

        ``None`` or ``""`` selects the only ready device. Several ready devices
        prompt on a terminal. Without a terminal they raise ``AdbError``.
        The choice is not stored.
        """
        devices = self.list_devices()
        if device_id:
            return _select_named(device_id, devices)
        ready = [record for record in devices if record.state == "device"]
        if len(ready) > 1 and sys.stdin.isatty():
            return _prompt_ready(ready)
        return _select_only_ready(devices)

    def read_device_info(self, serial: str) -> DeviceInfo:
        """Read version, model, manufacturer, and battery. A failed field stays ``None``."""
        version = self._property(serial, "ro.build.version.release")
        model = self._property(serial, "ro.product.model")
        manufacturer = self._property(serial, "ro.product.manufacturer")
        battery_text = self._command_text(serial, "dumpsys", "battery")
        return DeviceInfo(
            serial=serial,
            android_version=version,
            android_major=_major_version(version),
            model=model,
            manufacturer=manufacturer,
            battery_percent=_battery_percent(battery_text),
        )

    def shell(self, serial: str, *args: str) -> CommandResult:
        """Run ``adb -s serial shell``. A non-zero exit code is returned, not raised."""
        return self._run(["-s", serial, "shell", *args])

    def launch_package(self, serial: str, package_name: str) -> CommandResult:
        """Start ``package_name`` with ``monkey`` and the launcher category."""
        return self.shell(
            serial,
            "monkey",
            "-p",
            package_name,
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
        )

    def device_log_time(self, serial: str) -> str | None:
        """Device-local ``MM-DD HH:MM:SS.000`` for ``logcat -t``.

        The format uses ``T`` instead of a space. Toybox ``date`` allows one
        argument, and ``adb shell`` splits a format that contains a space.
        ``None`` when ``date`` fails, times out, or prints something else.
        """
        try:
            result = self.shell(serial, "date", "+%m-%dT%H:%M:%S")
        except AdbError:
            return None
        if result.returncode != 0:
            return None
        stamp = result.stdout.strip().replace("T", " ", 1)
        if _DEVICE_TIME.fullmatch(stamp) is None:
            return None
        return f"{stamp}.000"

    def logcat(self, serial: str, since: str) -> str:
        """Return ``logcat -d -t since``. The buffer is not cleared.

        ``since`` is ``MM-DD HH:MM:SS.mmm``. Raises ``AdbError`` on failure.
        """
        args = ["-s", serial, "logcat", "-d", "-t", since]
        result = self._run(args)
        if result.returncode != 0:
            raise AdbError(_failure_message(args, result))
        return result.stdout

    def _property(self, serial: str, name: str) -> str | None:
        return self._command_text(serial, "getprop", name)

    def _command_text(self, serial: str, *args: str) -> str | None:
        try:
            result = self.shell(serial, *args)
        except AdbError as exc:
            if exc.timed_out:
                return None
            raise
        if result.returncode != 0:
            return None
        text = result.stdout.strip()
        return text or None

    def _run(self, args: list[str]) -> CommandResult:
        if shutil.which("adb") is None:
            raise AdbError(_ADB_MISSING)
        command = ["adb", *args]
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError:
            raise AdbError(_ADB_MISSING) from None
        except subprocess.TimeoutExpired:
            shown = " ".join(command)
            raise AdbError(
                f"ADB command timed out after {TIMEOUT_SECONDS} seconds: {shown}.",
                timed_out=True,
            ) from None
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )


def _failure_message(args: list[str], result: CommandResult) -> str:
    shown = " ".join(["adb", *args])
    detail = " ".join(result.stderr.split()).strip().rstrip(".")
    base = f"ADB command '{shown}' failed with exit code {result.returncode}"
    if detail:
        return f"{base}: {detail}."
    return f"{base}."


def _parse_devices(stdout: str) -> list[DeviceRecord]:
    records: list[DeviceRecord] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line or line == "List of devices attached" or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        records.append(DeviceRecord(serial=parts[0], state=parts[1]))
    return records


def _select_named(device_id: str, devices: list[DeviceRecord]) -> str:
    match = next((record for record in devices if record.serial == device_id), None)
    if match is None:
        if not devices:
            raise AdbError(
                f"Android device {device_id} was not found; no Android device "
                "or emulator is connected."
            )
        listing = _describe(devices)
        raise AdbError(
            f"Android device {device_id} was not found; connected devices: {listing}."
        )
    if match.state != "device":
        raise AdbError(
            f"Android device {device_id} is connected with state {match.state}; "
            f"{_correction({match.state})}."
        )
    return match.serial


def _select_only_ready(devices: list[DeviceRecord]) -> str:
    ready = [record for record in devices if record.state == "device"]
    if not devices:
        raise AdbError("No Android device or emulator is connected.")
    if not ready:
        listing = _describe(devices)
        states = {record.state for record in devices}
        raise AdbError(
            "Android devices are connected but none is ready: "
            f"{listing}; {_correction(states)}."
        )
    if len(ready) > 1:
        serials = ", ".join(record.serial for record in ready)
        raise AdbError(
            f"More than one Android device is ready ({serials}); set "
            "device_id in config.json to one of these serials."
        )
    return ready[0].serial


def _prompt_ready(ready: list[DeviceRecord]) -> str:
    """Ask for a ready serial. A number wins when it is also a valid index."""
    print("Several Android devices are ready:", file=sys.stderr)
    for index, record in enumerate(ready, start=1):
        print(f"  {index}. {record.serial}", file=sys.stderr)
    span = f"1-{len(ready)}"
    while True:
        print(f"Choose a device [{span}]: ", file=sys.stderr, end="", flush=True)
        answer = sys.stdin.readline()
        if answer == "":
            raise AdbError(
                "Several Android devices are ready and no choice was entered."
            )
        selected = _match_choice(ready, answer.strip())
        if selected is not None:
            return selected
        print(
            f"Enter a number from {span}, or a serial from the list.",
            file=sys.stderr,
        )


def _match_choice(ready: list[DeviceRecord], choice: str) -> str | None:
    if choice.isdigit():
        index = int(choice)
        if 1 <= index <= len(ready):
            return ready[index - 1].serial
        return None
    for record in ready:
        if record.serial == choice:
            return record.serial
    return None


def _describe(devices: list[DeviceRecord]) -> str:
    return ", ".join(f"{record.serial} ({record.state})" for record in devices)


def _correction(states: set[str]) -> str:
    """How to fix the states that are actually connected."""
    hints: list[str] = []
    if "unauthorized" in states:
        hints.append("accept the USB debugging prompt on the device")
    if "offline" in states:
        hints.append("reconnect a device that is offline")
    if states - {"unauthorized", "offline"} or not hints:
        hints.append(
            "reconnect the device and confirm that adb devices shows state device"
        )
    if len(hints) == 1:
        return hints[0]
    return ", ".join(hints[:-1]) + ", or " + hints[-1]


def _major_version(version: str | None) -> int | None:
    """Leading integer of a release string. ``8.1.0`` is 8."""
    if version is None:
        return None
    match = _MAJOR_VERSION.match(version.strip())
    if match is None:
        return None
    return int(match.group(1))


def _battery_percent(text: str | None) -> int | None:
    """Percent from ``dumpsys battery``.

    A missing or 100 ``scale`` uses ``level``. Any other positive scale uses
    ``round(level * 100 / scale)``. A result outside 0 through 100 is ``None``.
    """
    if text is None:
        return None
    level: int | None = None
    scale: int | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if level is None:
            level_match = _LEVEL_LINE.match(line)
            if level_match is not None:
                level = int(level_match.group(1))
                continue
        if scale is None:
            scale_match = _SCALE_LINE.match(line)
            if scale_match is not None:
                scale = int(scale_match.group(1))
    if level is None:
        return None
    if scale is None or scale == 100:
        return _percent_in_range(level)
    if scale <= 0:
        return None
    return _percent_in_range(round(level * 100 / scale))


def _percent_in_range(value: int) -> int | None:
    if 0 <= value <= 100:
        return value
    return None
