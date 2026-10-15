# Android Python Automation

Command-line checks for one Android device. The tool reads `config.json`, selects a device through ADB, runs five checks, saves `adb logcat -d` when a check fails, and writes `report.txt`.

Python 3.12 or newer is required. The program uses only the standard library. `pytest` is the only development dependency, and the tests do not need a device or an `adb` binary.

## Prerequisites

- Python 3.12 or newer.
- [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools), with `platform-tools` on `PATH`. Confirm with `adb version`.
- A device or emulator with USB debugging enabled, authorized for this computer (`adb devices` shows the state `device`).

## Configuration

Edit `config.json` before running:

```json
{
  "package_name": "com.example.demo",
  "minimum_android_version": 10,
  "minimum_battery": 20
}
```

| Field | Meaning |
| --- | --- |
| `package_name` | Android package name, such as `com.example.demo`. |
| `minimum_android_version` | Integer major version from 1 through 99. This is the minimum, not the full release string. |
| `minimum_battery` | Integer percentage from 0 through 100. The device must be strictly above this value. A threshold of 100 always fails the battery check. |
| `device_id` | Optional serial. Omit it, or set `null` or `""`, to select the only ready device. A value with leading or trailing whitespace is rejected. |

The version on the device may look like `14` or `8.1.0`. The check uses the leading integer, so `8.1.0` counts as major version 8. A float such as `10.0` is not a valid `minimum_android_version`. JSON `true` is rejected as well, because a boolean is not an integer version.

When more than one device is in the `device` state, set `device_id` to one of the serials printed in the error. There is no interactive prompt.

## Run

From the project root:

```bash
python main.py
python main.py --config config.json --report report.txt --logs logs
```

| Exit code | Meaning | `report.txt` |
| --- | --- | --- |
| 0 | Every check passed or was skipped. `SKIP` is not a failure. | Written |
| 1 | At least one check failed. | Written |
| 2 | Configuration is missing or invalid, `adb` is not on `PATH`, no device is connected, a device is unauthorized or offline, or a device cannot be selected. | Not written |

Setup failures print one sentence to stderr and do not include a traceback. An unexpected programming error still shows a traceback.

Each failed check tries to save `logs/YYYYMMDD-HHMMSS_<check_name>.txt`. If logcat times out or fails, the check stays `FAIL` and the report adds `Log capture failed`. Passing and skipped checks do not create a log file.

Launch is a command check. The tool runs `monkey` with category `android.intent.category.LAUNCHER`. It does not look at the screen. If the package is not installed, launch is `SKIP` and `monkey` is not run.

## Tests

```bash
python -m pip install pytest
pytest
```

The tests mock `subprocess.run`. They cover invalid configuration, device selection, a fully passing run, a failed check, log capture, report totals, and exit codes 0, 1, and 2.

## Project structure

```text
main.py                 Entry point and exit codes
config.json             Values to edit
android_runner/
    config.py           Load and validate configuration before ADB
    adb.py              ADB commands, device selection, device info
    runner.py           Five checks and failure logs
    report.py           report.txt
tests/                  pytest, with subprocess mocked
.github/workflows/ci.yml
logs/                   Created when a check fails; contents are not committed
```

Decisions that affect the results:

- No runtime dependencies. ADB is an argument list passed to `subprocess`, never `shell=True`.
- Configuration is validated before any ADB call.
- Device selection is not interactive.
- The battery check passes only when the level is strictly above `minimum_battery`.
- Version and battery checks reuse the values already collected. They do not call ADB again.
- Log capture is best-effort and does not change a `FAIL` into another status.

## Continuous integration

GitHub Actions runs on every push and pull request. The job uses Ubuntu, installs Python 3.12 and `pytest`, and runs `pytest`. It does not install ADB and it does not use a device.

## Sample report

```text
Android Automation Report
=========================

Device
------
Device ID: emulator-5554
Android version: 14
Model: sdk_gphone64_arm64
Manufacturer: Google
Battery: 86%

Checks
------
Device is reachable: PASS
    Command 'echo hello' succeeded and returned 'hello'.
Android version: PASS
    Major version 14 is greater than or equal to minimum 10.
Battery level: FAIL
    Battery level is 15%, which is not strictly above 20%.
    Log: logs/20261013-215301_battery_level.txt
Application installation: FAIL
    Package com.example.demo is not installed.
    Log: logs/20261013-215302_application_installation.txt
Launch application: SKIP
    Skipped because com.example.demo is not installed.

Totals
------
Passed: 2
Failed: 2
Skipped: 1
All: 5

Overall: FAIL
```

## Future improvements

These assignment extensions are not implemented:

- Run the checks on an interval.
- Filter or analyze logcat content.
- Write a JSON or HTML report in addition to `report.txt`.
- Add checks for storage, network, foreground activity, and screen state.
