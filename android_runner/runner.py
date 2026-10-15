"""Run the device checks and save logcat output after a failure."""

from __future__ import annotations

import enum
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from android_runner.adb import AdbClient, AdbError, CommandResult, DeviceInfo
from android_runner.config import AppConfig

_REACHABLE = "Device is reachable"
_VERSION = "Android version"
_BATTERY = "Battery level"
_STORAGE = "Free storage"
_NETWORK = "Network"
_SCREEN = "Screen state"
_INSTALL = "Application installation"
_LAUNCH = "Launch application"
_FOREGROUND = "Foreground activity"
_WAKEFULNESS = re.compile(r"^mWakefulness=(\S+)$")
_SCREEN_ON = re.compile(r"^mScreenOn=(true|false)$")
_COMPONENT = re.compile(
    r"([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+/[A-Za-z0-9_.$]+)"
)
_WAKE_NAMES = {
    "Awake": "Awake",
    "Asleep": "Asleep",
    "Dozing": "Dozing",
    "Dreaming": "Dreaming",
    "0": "Asleep",
    "1": "Awake",
    "2": "Dreaming",
    "3": "Dozing",
}
_LOG_MAX_BYTES = 1_048_576
_TRUNCATED_LOG = "Log truncated to the most recent 1 MB."
_LOG_CLOCK = "Log capture failed because the device clock could not be read."
_LOG_READ = "Log capture failed because logcat could not be read."
_LOG_WRITE = "Log capture failed because the log file could not be written."
_THREADTIME_ERROR = re.compile(
    r"^\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}\s+\d+\s+\d+\s+[EF]\s+(\S+)\s*:\s?(.*)$"
)


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
    """Run the checks in order. Version and battery reuse ``info``."""
    reachable = _run_one(adb, info.serial, logs_dir, None, lambda: _reachable(adb, info))
    version = _run_one(adb, info.serial, logs_dir, None, lambda: _version(config, info))
    battery = _run_one(adb, info.serial, logs_dir, None, lambda: _battery(config, info))
    storage = _run_one(adb, info.serial, logs_dir, None, lambda: _storage(adb, config, info))
    network = _run_one(adb, info.serial, logs_dir, None, lambda: _network(adb, info))
    screen = _run_one(adb, info.serial, logs_dir, None, lambda: _screen(adb, info))
    install = _run_one(
        adb,
        info.serial,
        logs_dir,
        config.package_name,
        lambda: _installation(adb, config, info),
    )
    launch = _run_one(
        adb,
        info.serial,
        logs_dir,
        config.package_name,
        lambda: _launch(adb, config, info, install),
    )
    foreground = _run_one(
        adb,
        info.serial,
        logs_dir,
        config.package_name,
        lambda: _foreground(adb, config, info, install),
    )
    return [
        reachable,
        version,
        battery,
        storage,
        network,
        screen,
        install,
        launch,
        foreground,
    ]


def _run_one(
    adb: AdbClient,
    serial: str,
    logs_dir: Path,
    package_name: str | None,
    check: Callable[[], CheckResult],
) -> CheckResult:
    """Read the device clock, run one check, then save a failure log."""
    since = adb.device_log_time(serial)
    return _with_log(adb, serial, logs_dir, check(), since, package_name)


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


def _storage(adb: AdbClient, config: AppConfig, info: DeviceInfo) -> CheckResult:
    minimum = config.minimum_free_storage_mb
    try:
        result = adb.shell(info.serial, "df", "-k", "/data")
    except AdbError as exc:
        explanation = (
            "The storage check timed out."
            if exc.timed_out
            else "The storage check failed."
        )
        return _fail(_STORAGE, explanation)
    free_mb = None if result.returncode != 0 else _free_storage_mb(result.stdout)
    if free_mb is None:
        return _fail(_STORAGE, "Free storage could not be read.")
    if free_mb > minimum:
        return _pass(
            _STORAGE,
            f"Free storage is {free_mb} MB, which is greater than {minimum} MB.",
        )
    if free_mb == minimum:
        return _fail(
            _STORAGE,
            f"Free storage is {free_mb} MB, which is equal to {minimum} MB "
            "and must be above it.",
        )
    return _fail(
        _STORAGE,
        f"Free storage is {free_mb} MB, which is lower than {minimum} MB.",
    )


def _network(adb: AdbClient, info: DeviceInfo) -> CheckResult:
    try:
        result = adb.shell(info.serial, "ip", "route", "show", "table", "all")
    except AdbError as exc:
        explanation = (
            "The network check timed out."
            if exc.timed_out
            else "The network check failed."
        )
        return _fail(_NETWORK, explanation)
    if result.returncode != 0:
        return _fail(_NETWORK, "The network check failed.")
    if _has_default_route(result.stdout):
        return _pass(_NETWORK, "A default network route is present.")
    return _fail(
        _NETWORK,
        "No default network route is present, so the device cannot reach other networks.",
    )


def _screen(adb: AdbClient, info: DeviceInfo) -> CheckResult:
    try:
        result = adb.shell(info.serial, "dumpsys", "power")
    except AdbError as exc:
        explanation = (
            "The screen check timed out."
            if exc.timed_out
            else "The screen check failed."
        )
        return _fail(_SCREEN, explanation)
    state = None if result.returncode != 0 else _screen_state(result.stdout)
    if state is None:
        return _fail(_SCREEN, "Screen state could not be read.")
    if state == "Awake":
        return _pass(_SCREEN, "Screen state is Awake.")
    return _fail(_SCREEN, f"Screen state is {state}.")


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


def _install_skip(
    name: str,
    package_name: str,
    install: CheckResult,
) -> CheckResult | None:
    """Skip ``name`` when installation failed. ``None`` when it passed."""
    if install.status is not Status.FAIL:
        return None
    if install.install_cause is InstallCause.MISSING:
        explanation = f"Skipped because {package_name} is not installed."
    else:
        explanation = (
            f"Skipped because installation of {package_name} could not be confirmed."
        )
    return _skip(name, explanation)


def _launch(
    adb: AdbClient,
    config: AppConfig,
    info: DeviceInfo,
    install: CheckResult,
) -> CheckResult:
    package_name = config.package_name
    skipped = _install_skip(_LAUNCH, package_name, install)
    if skipped is not None:
        return skipped
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


def _foreground(
    adb: AdbClient,
    config: AppConfig,
    info: DeviceInfo,
    install: CheckResult,
) -> CheckResult:
    package_name = config.package_name
    skipped = _install_skip(_FOREGROUND, package_name, install)
    if skipped is not None:
        return skipped
    try:
        result = adb.shell(info.serial, "dumpsys", "activity", "activities")
    except AdbError as exc:
        explanation = (
            f"The foreground check for {package_name} timed out."
            if exc.timed_out
            else f"The foreground check for {package_name} failed."
        )
        return _fail(_FOREGROUND, explanation)
    component = None if result.returncode != 0 else _foreground_component(result.stdout)
    if component is None:
        return _fail(_FOREGROUND, "Foreground activity could not be read.")
    if component.split("/", 1)[0] == package_name:
        return _pass(_FOREGROUND, f"Foreground activity is {component}.")
    return _fail(
        _FOREGROUND,
        f"Foreground activity is {component}, not {package_name}.",
    )


def _free_storage_mb(text: str) -> int | None:
    """Available mebibytes from ``df -k``. Truncates 1024 KiB blocks toward zero."""
    for raw_line in text.splitlines():
        tokens = raw_line.split()
        for index, token in enumerate(tokens):
            if not token.endswith("%") or index == 0:
                continue
            available = tokens[index - 1]
            if available.isdigit():
                return int(available) // 1024
    return None


def _has_default_route(text: str) -> bool:
    """True when a usable ``default`` route exists.

    Android keeps that route in a per-network table, so ``ip route`` alone
    misses it. ``default dev dummy0`` is the unreachable placeholder and
    does not count.
    """
    for raw_line in text.splitlines():
        tokens = raw_line.split()
        if tokens and tokens[0] == "default" and "dummy0" not in tokens:
            return True
    return False


def _screen_state(text: str) -> str | None:
    """``Awake``, ``Asleep``, ``Dozing``, or ``Dreaming``. ``None`` when unread."""
    wakefulness: str | None = None
    screen_on: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if wakefulness is None:
            match = _WAKEFULNESS.match(line)
            if match is not None:
                wakefulness = match.group(1)
                continue
        if screen_on is None:
            match = _SCREEN_ON.match(line)
            if match is not None:
                screen_on = match.group(1)
    if wakefulness is not None:
        return _WAKE_NAMES.get(wakefulness)
    if screen_on == "true":
        return "Awake"
    if screen_on == "false":
        return "Asleep"
    return None


def _foreground_component(text: str) -> str | None:
    """First ``topResumedActivity``, else the first ``mResumedActivity``."""
    for marker in ("topResumedActivity=", "mResumedActivity:"):
        for raw_line in text.splitlines():
            if marker not in raw_line:
                continue
            match = _COMPONENT.search(raw_line)
            if match is not None:
                return match.group(1)
    return None


def _has_package_line(result: CommandResult) -> bool:
    return any(line.strip().startswith("package:") for line in result.stdout.splitlines())


def _with_log(
    adb: AdbClient,
    serial: str,
    logs_dir: Path,
    result: CheckResult,
    since: str | None,
    package_name: str | None,
) -> CheckResult:
    if result.status is not Status.FAIL:
        return result
    if since is None:
        return replace(result, log_error=_LOG_CLOCK)
    path: Path | None = None
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        text = adb.logcat(serial, since)
        if package_name is not None:
            text = _filter_package_log(text, package_name)
        text, truncated = _bounded_log(text)
        path = logs_dir / f"{datetime.now():%Y%m%d-%H%M%S}_{_slug(result.name)}.log"
        path.write_text(text, encoding="utf-8")
    except AdbError:
        return replace(result, log_error=_LOG_READ)
    except OSError:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        return replace(result, log_error=_LOG_WRITE)
    return replace(
        result,
        log_path=path,
        log_note=_join_notes(truncated, _error_note(text)),
    )


def _filter_package_log(text: str, package_name: str) -> str:
    """Keep package lines and crash blocks through the next empty line."""
    lines = text.splitlines()
    kept: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if "FATAL EXCEPTION" in line or "AndroidRuntime" in line:
            kept.append(line)
            index += 1
            while index < len(lines) and lines[index].strip() != "":
                kept.append(lines[index])
                index += 1
            if index < len(lines) and lines[index].strip() == "":
                index += 1
            continue
        if package_name in line:
            kept.append(line)
        index += 1
    if not kept:
        return ""
    return "\n".join(kept) + "\n"


def _error_note(text: str) -> str | None:
    """Count threadtime ``E`` and ``F`` lines. ``None`` when there are none."""
    found: list[str] = []
    for line in text.splitlines():
        match = _THREADTIME_ERROR.match(line.strip())
        if match is None:
            continue
        tag = match.group(1)
        message = match.group(2).strip()
        found.append(f"{tag}: {message}" if message else tag)
    if not found:
        return None
    noun = "line" if len(found) == 1 else "lines"
    return f"{len(found)} error {noun} in this check. First: {found[0]}."


def _join_notes(*notes: str | None) -> str | None:
    present = [note for note in notes if note]
    if not present:
        return None
    return " ".join(present)


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
