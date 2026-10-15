"""Load and validate ``config.json``."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

PACKAGE_NAME_PATTERN = re.compile(
    r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$"
)

_REQUIRED_FIELDS = (
    "package_name",
    "minimum_android_version",
    "minimum_battery",
)
_ALLOWED_FIELDS = frozenset((*_REQUIRED_FIELDS, "device_id"))

_ANDROID_VERSION_MIN = 1
_ANDROID_VERSION_MAX = 99
_BATTERY_MIN = 0
_BATTERY_MAX = 100


class ConfigError(Exception):
    """Invalid configuration. The message is one sentence."""


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Settings accepted by ``load_config``."""

    package_name: str
    minimum_android_version: int
    minimum_battery: int
    device_id: str | None = None


def load_config(path: str | Path) -> AppConfig:
    """Read ``path``. Raises ``ConfigError`` when the file is missing or invalid."""
    config_path = Path(path)
    raw_text = _read_text(config_path)
    payload = _parse_json(config_path, raw_text)
    return _validate(payload)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"Configuration file not found: {path}.") from None
    except IsADirectoryError:
        raise ConfigError(f"Configuration path is a directory: {path}.") from None
    except PermissionError:
        raise ConfigError(f"Configuration file is not readable: {path}.") from None
    except UnicodeDecodeError:
        raise ConfigError(f"Configuration file is not valid UTF-8: {path}.") from None
    except OSError as exc:
        detail = exc.strerror or exc.__class__.__name__
        raise ConfigError(
            f"Configuration file could not be read: {path} ({detail})."
        ) from None


def _parse_json(path: Path, raw_text: str) -> object:
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"Configuration file {path} contains invalid JSON: {exc.msg} "
            f"(line {exc.lineno}, column {exc.colno})."
        ) from None


def _validate(payload: object) -> AppConfig:
    if not isinstance(payload, dict) or any(not isinstance(key, str) for key in payload):
        raise ConfigError("Configuration root must be a JSON object.")

    unknown = sorted(set(payload) - _ALLOWED_FIELDS)
    missing = [name for name in _REQUIRED_FIELDS if name not in payload]
    structure_error = _structure_error(unknown, missing)
    if structure_error is not None:
        raise ConfigError(structure_error)

    package_name = _require_package_name(payload["package_name"])
    minimum_android_version = _require_bounded_int(
        payload["minimum_android_version"],
        "minimum_android_version",
        _ANDROID_VERSION_MIN,
        _ANDROID_VERSION_MAX,
    )
    minimum_battery = _require_bounded_int(
        payload["minimum_battery"],
        "minimum_battery",
        _BATTERY_MIN,
        _BATTERY_MAX,
    )
    device_id = _optional_device_id(payload.get("device_id"))
    return AppConfig(
        package_name=package_name,
        minimum_android_version=minimum_android_version,
        minimum_battery=minimum_battery,
        device_id=device_id,
    )


def _structure_error(unknown: list[str], missing: list[str]) -> str | None:
    parts: list[str] = []
    if unknown:
        parts.append(f"contains {_name_list('unknown field', 'unknown fields', unknown)}")
    if missing:
        parts.append(
            f"is missing {_name_list('required field', 'required fields', missing)}"
        )
    if not parts:
        return None
    return f"Configuration {' and '.join(parts)}."


def _name_list(singular: str, plural: str, names: list[str]) -> str:
    quoted = ", ".join(f"'{name}'" for name in names)
    label = singular if len(names) == 1 else plural
    return f"{label} {quoted}"


def _require_package_name(value: object) -> str:
    if not isinstance(value, str):
        raise ConfigError(
            "Configuration field 'package_name' must be a string; "
            f"got {_json_kind(value)}."
        )
    if PACKAGE_NAME_PATTERN.fullmatch(value) is None:
        shown = f"; got {value!r}" if len(value) <= 80 else ""
        raise ConfigError(
            "Configuration field 'package_name' must be an Android package name "
            f"such as 'com.example.demo'{shown}."
        )
    return value


def _require_bounded_int(value: object, field: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(
            f"Configuration field '{field}' must be an integer from {low} "
            f"through {high}; got {_json_kind(value)}."
        )
    if not low <= value <= high:
        raise ConfigError(
            f"Configuration field '{field}' must be an integer from {low} "
            f"through {high}; got {value}."
        )
    return value


def _optional_device_id(value: object) -> str | None:
    """Serial, or ``None`` when the key is missing, null, or ``""``."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(
            "Configuration field 'device_id' must be a string; "
            f"got {_json_kind(value)}."
        )
    if value == "":
        return None
    if value != value.strip():
        raise ConfigError(
            "Configuration field 'device_id' must not have leading or trailing whitespace."
        )
    return value


def _json_kind(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, int):
        return "an integer"
    if isinstance(value, float):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "an array"
    if isinstance(value, dict):
        return "an object"
    return type(value).__name__
