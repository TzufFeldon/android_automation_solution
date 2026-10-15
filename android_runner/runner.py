"""Run the five device checks and save logcat output after a failure."""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from android_runner.adb import AdbClient, AdbError, CommandResult, DeviceInfo
from android_runner.config import AppConfig

_REACHABLE = "Device is reachable"
_VERSION = "Android version"
_BATTERY = "Battery level"
_INSTALL = "Application installation"
_LAUNCH = "Launch application"
_LOG_MAX_BYTES = 1_048_576
_TRUNCATED_LOG = "Log truncated to the most recent 1 MB."


class Status(enum.StrEnum):
    """Check result. ``SKIP`` is not a failure."""

    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"


class InstallCause(enum.StrEnum):
    """Why installation failed. Launch reads this instead of the explanation."""

    MISSING = "missing"
    UNCONFIRMED = "unconfirmed"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One check, and the log file saved when it fails."""

    name: str
    status: Status
    explanation: str
    log_path: Path | None = None
    log_error: str | None = None
    log_note: str | None = None
    install_cause: InstallCause | None = None


def run_checks(
    adb: AdbClient,
    config: AppConfig,
    info: DeviceInfo,
    logs_dir: Path,
) -> list[CheckResult]:
    """Run the five checks in order. Version and battery reuse ``info``."""
    reachable = _with_log(adb, info.serial, logs_dir, _reachable(adb, info))
    version = _with_log(adb, info.serial, logs_dir, _version(config, info))
    battery = _with_log(adb, info.serial, logs_dir, _battery(config, info))
    install = _with_log(adb, info.serial, logs_dir, _installation(adb, config, info))
    launch = _with_log(adb, info.serial, logs_dir, _launch(adb, config, info, install))
    return [reachable, version, battery, install, launch]


def _reachable(adb: AdbClient, info: DeviceInfo) -> CheckResult:
    try:
        result = adb.shell(info.serial, "echo", "hello")
    except AdbError as exc:
        explanation = (
            "Command 'echo hello' timed out."
            if exc.timed_out
            else "Command 'echo hello' did not return 'hello'."
        )
        return _fail(_REACHABLE, explanation)
    if result.returncode == 0 and result.stdout.strip() == "hello":
        return _pass(
            _REACHABLE,
            "Command 'echo hello' succeeded and returned 'hello'.",
        )
    return _fail(_REACHABLE, "Command 'echo hello' did not return 'hello'.")


def _version(config: AppConfig, info: DeviceInfo) -> CheckResult:
    minimum = config.minimum_android_version
    if info.android_major is None:
        if info.android_version:
            explanation = (
                f"Android version {info.android_version!r} could not be parsed."
            )
        else:
            explanation = "Android version could not be read."
        return _fail(_VERSION, explanation)
    if info.android_major >= minimum:
        return _pass(
            _VERSION,
            f"Major version {info.android_major} is greater than or equal "
            f"to minimum {minimum}.",
        )
    return _fail(
        _VERSION,
        f"Major version {info.android_major} is lower than minimum {minimum}.",
    )


def _battery(config: AppConfig, info: DeviceInfo) -> CheckResult:
    minimum = config.minimum_battery
    level = info.battery_percent
    if level is None:
        return _fail(_BATTERY, "Battery level could not be read.")
    if level > minimum:
        return _pass(
            _BATTERY,
            f"Battery level is {level}%, which is greater than {minimum}%.",
        )
    if level == minimum:
        return _fail(
            _BATTERY,
            f"Battery level is {level}%, which is equal to {minimum}% "
            "and must be above it.",
        )
    return _fail(
        _BATTERY,
        f"Battery level is {level}%, which is lower than {minimum}%.",
    )


def _installation(adb: AdbClient, config: AppConfig, info: DeviceInfo) -> CheckResult:
    package_name = config.package_name
    try:
        result = adb.shell(info.serial, "pm", "path", package_name)
    except AdbError as exc:
        if exc.timed_out:
            explanation = f"The installation check for {package_name} timed out."
        else:
            explanation = f"The installation check for {package_name} failed."
        return _fail(_INSTALL, explanation, install_cause=InstallCause.UNCONFIRMED)
    if result.returncode == 0 and _has_package_line(result):
        return _pass(_INSTALL, f"Package {package_name} is installed.")
    return _fail(
        _INSTALL,
        f"Package {package_name} is not installed.",
        install_cause=InstallCause.MISSING,
    )


def _launch(
    adb: AdbClient,
    config: AppConfig,
    info: DeviceInfo,
    install: CheckResult,
) -> CheckResult:
    package_name = config.package_name
    if install.status is Status.FAIL:
        if install.install_cause is InstallCause.MISSING:
            explanation = f"Skipped because {package_name} is not installed."
        else:
            explanation = (
                f"Skipped because installation of {package_name} could not "
                "be confirmed."
            )
        return _skip(_LAUNCH, explanation)
    try:
        result = adb.launch_package(info.serial, package_name)
    except AdbError as exc:
        explanation = (
            f"Launch of {package_name} timed out."
            if exc.timed_out
            else f"Launch of {package_name} failed."
        )
        return _fail(_LAUNCH, explanation)
    output = f"{result.stdout}\n{result.stderr}"
    if result.returncode == 0 and "No activities found" not in output and (
        "monkey aborted" not in output
    ):
        return _pass(_LAUNCH, f"Launched {package_name} with monkey.")
    if "No activities found" in output:
        explanation = f"No launcher activity was found for {package_name}."
    elif "monkey aborted" in output:
        explanation = f"Launch of {package_name} failed because monkey aborted."
    else:
        explanation = f"Launch of {package_name} failed."
    return _fail(_LAUNCH, explanation)


def _has_package_line(result: CommandResult) -> bool:
    return any(line.strip().startswith("package:") for line in result.stdout.splitlines())


def _with_log(
    adb: AdbClient,
    serial: str,
    logs_dir: Path,
    result: CheckResult,
) -> CheckResult:
    if result.status is not Status.FAIL:
        return result
    path: Path | None = None
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        text, note = _bounded_log(adb.logcat(serial))
        path = logs_dir / f"{datetime.now():%Y%m%d-%H%M%S}_{_slug(result.name)}.log"
        path.write_text(text, encoding="utf-8")
    except AdbError:
        return replace(result, log_error="Log capture failed")
    except OSError:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        return replace(result, log_error="Log capture failed")
    return replace(result, log_path=path, log_note=note)


def _bounded_log(text: str) -> tuple[str, str | None]:
    """Fit ``text`` in ``_LOG_MAX_BYTES``. The note is set only when it was cut."""
    raw = text.encode("utf-8")
    if len(raw) <= _LOG_MAX_BYTES:
        return text, None
    tail = raw[-_LOG_MAX_BYTES:].decode("utf-8", errors="ignore")
    newline = tail.find("\n")
    if newline != -1:
        trimmed = tail[newline + 1 :]
        if trimmed:
            tail = trimmed
    return tail, _TRUNCATED_LOG


def _slug(name: str) -> str:
    return name.lower().replace(" ", "_")


def _pass(name: str, explanation: str) -> CheckResult:
    return CheckResult(name=name, status=Status.PASS, explanation=explanation)


def _fail(
    name: str,
    explanation: str,
    *,
    install_cause: InstallCause | None = None,
) -> CheckResult:
    return CheckResult(
        name=name,
        status=Status.FAIL,
        explanation=explanation,
        install_cause=install_cause,
    )


def _skip(name: str, explanation: str) -> CheckResult:
    return CheckResult(name=name, status=Status.SKIP, explanation=explanation)
