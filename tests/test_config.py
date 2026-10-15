from __future__ import annotations

import json
from pathlib import Path

import pytest

from android_runner.config import AppConfig, ConfigError, load_config

_VALID = {
    "package_name": "com.android.settings",
    "minimum_android_version": 10,
    "minimum_battery": 20,
    "minimum_free_storage_mb": 100,
}


def _write(directory: Path, payload: object) -> Path:
    path = directory / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _assert_error(path: Path, match: str) -> str:
    with pytest.raises(ConfigError, match=match) as caught:
        load_config(path)
    message = str(caught.value)
    assert "Traceback" not in message
    assert "\n" not in message
    assert message.endswith(".")
    return message


def test_valid_config(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, _VALID))

    assert config == AppConfig(
        package_name="com.android.settings",
        minimum_android_version=10,
        minimum_battery=20,
        minimum_free_storage_mb=100,
        device_id=None,
    )


def test_present_device_id(tmp_path: Path) -> None:
    payload = {**_VALID, "device_id": "emulator-5554"}

    config = load_config(_write(tmp_path, payload))

    assert config.device_id == "emulator-5554"


def test_empty_device_id_means_no_selection(tmp_path: Path) -> None:
    payload = {**_VALID, "device_id": ""}

    config = load_config(_write(tmp_path, payload))

    assert config.device_id is None


def test_null_device_id_means_no_selection(tmp_path: Path) -> None:
    payload = {**_VALID, "device_id": None}

    config = load_config(_write(tmp_path, payload))

    assert config.device_id is None


def test_absent_interval_means_one_pass(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, _VALID))

    assert config.interval_seconds is None


def test_null_interval_means_one_pass(tmp_path: Path) -> None:
    payload = {**_VALID, "interval_seconds": None}

    config = load_config(_write(tmp_path, payload))

    assert config.interval_seconds is None


def test_interval_bounds_are_accepted(tmp_path: Path) -> None:
    for seconds in (1, 86_400):
        payload = {**_VALID, "interval_seconds": seconds}
        config = load_config(_write(tmp_path, payload))
        assert config.interval_seconds == seconds


def test_battery_bounds_are_accepted(tmp_path: Path) -> None:
    for level in (0, 100):
        payload = {**_VALID, "minimum_battery": level}
        config = load_config(_write(tmp_path, payload))
        assert config.minimum_battery == level


def test_storage_bounds_are_accepted(tmp_path: Path) -> None:
    for mebibytes in (0, 1_048_576):
        payload = {**_VALID, "minimum_free_storage_mb": mebibytes}
        config = load_config(_write(tmp_path, payload))
        assert config.minimum_free_storage_mb == mebibytes


def test_android_version_bounds_are_accepted(tmp_path: Path) -> None:
    for version in (1, 99):
        payload = {**_VALID, "minimum_android_version": version}
        config = load_config(_write(tmp_path, payload))
        assert config.minimum_android_version == version


def test_missing_file_names_the_path(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"

    message = _assert_error(path, "not found")

    assert str(path) in message


def test_invalid_json_names_path_message_line_and_column(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{\n  "package_name": \n}\n', encoding="utf-8")

    message = _assert_error(path, r"invalid JSON: .+ \(line \d+, column \d+\)")

    assert str(path) in message
    assert "line 3" in message


def test_non_object_root_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("[]", encoding="utf-8")

    _assert_error(path, "JSON object")


def test_missing_required_field_is_named(tmp_path: Path) -> None:
    payload = {
        "minimum_android_version": 10,
        "minimum_battery": 20,
        "minimum_free_storage_mb": 100,
    }

    _assert_error(_write(tmp_path, payload), "required field 'package_name'")


def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    payload = {**_VALID, "min_battery": 15}

    _assert_error(_write(tmp_path, payload), "unknown field 'min_battery'")


def test_package_name_must_be_a_string(tmp_path: Path) -> None:
    payload = {**_VALID, "package_name": 12}

    _assert_error(_write(tmp_path, payload), "package_name")


def test_invalid_package_name_is_rejected(tmp_path: Path) -> None:
    payload = {**_VALID, "package_name": "not a package"}

    _assert_error(_write(tmp_path, payload), "Android package name")


def test_package_name_longer_than_80_characters_omits_the_value(tmp_path: Path) -> None:
    included = "x" * 80
    omitted = "x" * 81

    shown = _assert_error(
        _write(tmp_path, {**_VALID, "package_name": included}),
        "Android package name",
    )
    hidden = _assert_error(
        _write(tmp_path, {**_VALID, "package_name": omitted}),
        "Android package name",
    )

    assert f"got {included!r}" in shown
    assert "got" not in hidden
    assert omitted not in hidden


def test_boolean_is_rejected_for_android_version(tmp_path: Path) -> None:
    payload = {**_VALID, "minimum_android_version": True}

    message = _assert_error(_write(tmp_path, payload), "minimum_android_version")

    assert "boolean" in message


def test_boolean_is_rejected_for_battery(tmp_path: Path) -> None:
    payload = {**_VALID, "minimum_battery": False}

    message = _assert_error(_write(tmp_path, payload), "minimum_battery")

    assert "boolean" in message


def test_float_is_rejected(tmp_path: Path) -> None:
    payload = {**_VALID, "minimum_android_version": 10.0}

    message = _assert_error(_write(tmp_path, payload), "minimum_android_version")

    assert "number" in message


def test_android_version_out_of_range(tmp_path: Path) -> None:
    payload = {**_VALID, "minimum_android_version": 0}

    _assert_error(_write(tmp_path, payload), "from 1 through 99")


def test_battery_out_of_range(tmp_path: Path) -> None:
    payload = {**_VALID, "minimum_battery": 101}

    _assert_error(_write(tmp_path, payload), "from 0 through 100")


def test_device_id_whitespace_is_rejected(tmp_path: Path) -> None:
    payload = {**_VALID, "device_id": " emulator-5554"}

    _assert_error(_write(tmp_path, payload), "leading or trailing whitespace")


def test_directory_path_is_rejected(tmp_path: Path) -> None:
    _assert_error(tmp_path, "directory")


def test_invalid_utf8_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_bytes(b"\xff\xfe{")

    _assert_error(path, "UTF-8")


def test_shipped_config_loads() -> None:
    config = load_config(Path(__file__).resolve().parents[1] / "config.json")

    assert config.package_name == "com.android.settings"
    assert config.minimum_android_version == 10
    assert config.minimum_battery == 20
    assert config.minimum_free_storage_mb == 100
    assert config.device_id is None
    assert config.interval_seconds == 30


def test_load_config_accepts_a_string_path(tmp_path: Path) -> None:
    config = load_config(str(_write(tmp_path, _VALID)))

    assert config.package_name == "com.android.settings"


def test_package_name_may_contain_underscores_and_digits(tmp_path: Path) -> None:
    payload = {**_VALID, "package_name": "com.example2.my_app"}

    config = load_config(_write(tmp_path, payload))

    assert config.package_name == "com.example2.my_app"


def test_device_id_may_contain_internal_spaces(tmp_path: Path) -> None:
    payload = {**_VALID, "device_id": "emu lator"}

    config = load_config(_write(tmp_path, payload))

    assert config.device_id == "emu lator"


@pytest.mark.parametrize("raw", ["null", "10", '"config"'])
def test_non_object_json_roots_are_rejected(tmp_path: Path, raw: str) -> None:
    path = tmp_path / "config.json"
    path.write_text(raw, encoding="utf-8")

    _assert_error(path, "JSON object")


def test_utf8_bom_is_rejected_as_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_bytes(b"\xef\xbb\xbf{}")

    _assert_error(path, "invalid JSON")


def test_several_missing_fields_are_all_named(tmp_path: Path) -> None:
    message = _assert_error(_write(tmp_path, {}), "required fields")

    assert "package_name" in message
    assert "minimum_android_version" in message
    assert "minimum_battery" in message
    assert "minimum_free_storage_mb" in message


def test_several_unknown_fields_are_all_named(tmp_path: Path) -> None:
    payload = {**_VALID, "zzz": 1, "aaa": 2}

    message = _assert_error(_write(tmp_path, payload), "unknown fields")

    assert "'aaa'" in message
    assert "'zzz'" in message


def test_unknown_and_missing_fields_are_reported_together(tmp_path: Path) -> None:
    payload = {"package_name": "com.android.settings", "zzz": 1, "aaa": 2}

    message = _assert_error(_write(tmp_path, payload), "unknown fields")

    assert message == (
        "Configuration contains unknown fields 'aaa', 'zzz' and is missing "
        "required fields 'minimum_android_version', 'minimum_battery', "
        "'minimum_free_storage_mb'."
    )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("minimum_android_version", "10", "integer"),
        ("minimum_android_version", 10.0, "number"),
        ("minimum_android_version", 100, "1 through 99"),
        ("minimum_android_version", -1, "1 through 99"),
        ("minimum_android_version", None, "integer"),
        ("minimum_battery", 20.0, "integer"),
        ("minimum_battery", -1, "0 through 100"),
        ("minimum_battery", True, "boolean"),
        ("minimum_free_storage_mb", 100.0, "integer"),
        ("minimum_free_storage_mb", -1, "0 through 1048576"),
        ("minimum_free_storage_mb", 1_048_577, "0 through 1048576"),
        ("minimum_free_storage_mb", True, "boolean"),
        ("package_name", None, "string"),
        ("package_name", "example", "Android package name"),
        ("package_name", "com.", "Android package name"),
        ("package_name", ".com.example", "Android package name"),
        ("package_name", "com..example", "Android package name"),
        ("package_name", "com.android.settings ", "Android package name"),
        ("package_name", "1com.example", "Android package name"),
        ("device_id", "serial ", "whitespace"),
        ("device_id", "\nemulator", "whitespace"),
        ("device_id", 5554, "string"),
        ("device_id", True, "boolean"),
        ("interval_seconds", 0, "1 through 86400"),
        ("interval_seconds", 86_401, "1 through 86400"),
        ("interval_seconds", 30.0, "integer"),
        ("interval_seconds", True, "boolean"),
        ("interval_seconds", "30", "integer"),
    ],
)
def test_invalid_field_values_are_rejected(
    tmp_path: Path,
    field: str,
    value: object,
    match: str,
) -> None:
    payload = {**_VALID, field: value}

    message = _assert_error(_write(tmp_path, payload), match)

    assert field in message


def test_unreadable_file_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _VALID)
    path.chmod(0)
    try:
        try:
            load_config(path)
        except ConfigError as exc:
            assert "not readable" in str(exc)
            assert "Traceback" not in str(exc)
        else:
            pytest.skip("this platform still allows the owner to read a mode 000 file")
    finally:
        path.chmod(0o644)
