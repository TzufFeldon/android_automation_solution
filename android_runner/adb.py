"""Run ADB and turn its output into device records.

Commands are argument lists with a 20-second timeout. ``shell=True`` is never
used. This module raises :class:`AdbError` and does not exit the process.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass

TIMEOUT_SECONDS = 20

_ADB_MISSING = (
    "Android SDK Platform-Tools is not available; install it, put "
    "platform-tools on PATH, and verify with adb version."
)
_LEVEL_LINE = re.compile(r"^level:\s*(-?\d+)$")
_SCALE_LINE = re.compile(r"^scale:\s*(-?\d+)$")
_MAJOR_VERSION = re.compile(r"(\d+)")


class AdbError(Exception):
    """ADB is missing, timed out, or could not select a device.

    ``timed_out`` is true when the failure is the 20-second command limit.
    ``str(error)`` is one sentence.
    """

    def __init__(self, message: str, *, timed_out: bool = False) -> None:
        super().__init__(message)
        self.timed_out = timed_out


@dataclass(frozen=True, slots=True)
class DeviceRecord:
    """One row from ``adb devices``."""

    serial: str
    state: str


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Facts collected after a device has been selected.

    Missing strings and an unreadable battery are ``None``. The report shows
    ``unknown`` for a missing model or manufacturer. A missing version or
    battery fails the matching check.
    """

    serial: str
    android_version: str | None
    android_major: int | None
    model: str | None
    manufacturer: str | None
    battery_percent: int | None


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Output of one ADB command, including a non-zero exit code."""

    returncode: int
    stdout: str
    stderr: str


class AdbClient:
    """Build ADB commands and call ``subprocess.run`` in one place."""

    def list_devices(self) -> list[DeviceRecord]:
        """Return every parsed device row, including devices that are not ready."""
        args = ["devices"]
        result = self._run(args)
        if result.returncode != 0:
            raise AdbError(_failure_message(args, result))
        return _parse_devices(result.stdout)

    def select_device(self, device_id: str | None) -> str:
        """Return the serial to check.

        An empty ``device_id`` means the same thing as ``None``: select the
        only ready device. Selection never reads from stdin.
        """
        devices = self.list_devices()
        if device_id:
            return _select_named(device_id, devices)
        return _select_only_ready(devices)

    def read_device_info(self, serial: str) -> DeviceInfo:
        """Read version, model, manufacturer, and battery for ``serial``.

        A field that times out, exits non-zero, or comes back blank is stored
        as missing. The other fields are still read.
        """
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
        """Run ``adb -s serial shell`` and keep a non-zero exit code.

        Raises:
            AdbError: ``adb`` is missing or the command times out.
        """
        return self._run(["-s", serial, "shell", *args])

    def launch_package(self, serial: str, package_name: str) -> CommandResult:
        """Start ``package_name`` through ``monkey`` and the launcher category."""
        return self.shell(
            serial,
            "monkey",
            "-p",
            package_name,
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
        )

    def logcat(self, serial: str) -> str:
        """Return the current logcat buffer.

        Raises:
            AdbError: The command is missing, times out, or exits non-zero.
        """
        args = ["-s", serial, "logcat", "-d"]
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


def _describe(devices: list[DeviceRecord]) -> str:
    return ", ".join(f"{record.serial} ({record.state})" for record in devices)


def _correction(states: set[str]) -> str:
    """Say how to make the listed states usable.

    Only states that are actually present are mentioned, so an offline device
    is not told to accept a USB prompt and an unauthorized device is not told
    to reconnect an offline one.
    """
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
    """Return the leading integer of a release string such as ``8.1.0``."""
    if version is None:
        return None
    match = _MAJOR_VERSION.match(version.strip())
    if match is None:
        return None
    return int(match.group(1))


def _battery_percent(text: str | None) -> int | None:
    """Read ``level`` and ``scale`` from ``dumpsys battery``.

    A missing scale, or a scale of 100, means the percentage is ``level``.
    Any other positive scale uses Python ``round`` (half to even). A missing
    level or a scale of 0 or less is unreadable.
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
        return level
    if scale <= 0:
        return None
    return round(level * 100 / scale)
