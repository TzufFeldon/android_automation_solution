# Android Python Automation

Command-line checks for one Android device. The tool reads `config.json`, runs five checks through ADB, writes `report.txt`, and saves `adb logcat -d` when a check fails.

Python 3.12 or newer. The program uses the standard library only.

## Prerequisites

- Python 3.12 or newer.
- [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools) on `PATH`. Check with `adb version`.
- A device or emulator with USB debugging enabled and this computer authorized. `adb devices` shows the state `device`.

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
| `package_name` | Package name, for example `com.example.demo`. |
| `minimum_android_version` | Integer from 1 through 99. `8.1.0` counts as major version 8. |
| `minimum_battery` | Integer from 0 through 100. The level must be strictly above this value. `100` always fails. |
| `device_id` | Optional serial. Omit it, or set `null` or `""`, when only one device is ready. |

A float such as `10.0` and JSON `true` are rejected. `device_id` with leading or trailing spaces is rejected.

When more than one device is in state `device` and `device_id` is unset, the program exits with code 2:

```text
More than one Android device is ready (SERIAL, SERIAL); set device_id in config.json to one of these serials.
```

Set `device_id` to one of those serials and run again. There is no prompt.

## Run

From the project root:

```bash
python main.py
```

| Exit code | Meaning | `report.txt` |
| --- | --- | --- |
| 0 | No check failed. `SKIP` is not a failure. | Written |
| 1 | At least one check failed. | Written |
| 2 | Bad configuration, missing `adb`, or no selectable device. | Not written |

Setup failures print one sentence to stderr, without a traceback. An unauthorized device is told to accept the USB debugging prompt. An offline device is told to reconnect. The message names only states that are connected.

A failed check saves `logs/YYYYMMDD-HHMMSS_<check_name>.txt`. If logcat fails, the check stays `FAIL` and the report adds `Log capture failed`.

Launch runs `monkey` with category `android.intent.category.LAUNCHER`. It does not inspect the screen. If the package is not installed, installation is `FAIL`, launch is `SKIP`, and `monkey` is not run.

## Tests

```bash
python -m pip install pytest
pytest
```

The tests mock `subprocess.run`. They cover configuration, device selection, a passing run, a failed check, log capture, report totals, and exit codes 0, 1, and 2. No device or `adb` binary is required.

## Project structure

The repository root is the project directory.

```text
main.py                 Entry point and exit codes
config.json             Values to edit
android_runner/
    config.py           Load and validate configuration
    adb.py              ADB commands, device selection, device info
    runner.py           Five checks and failure logs
    report.py           report.txt
tests/                  pytest, with subprocess mocked
.github/workflows/ci.yml
logs/                   Failure logs; contents are not committed
```

- ADB commands are argument lists. `shell=True` is not used.
- Configuration is validated before any ADB call.
- Device selection does not prompt.
- The battery level must be strictly above `minimum_battery`.
- Version and battery checks reuse values already collected.
- A failed log capture leaves the check as `FAIL`.

## Continuous integration

GitHub Actions runs `pytest` on Ubuntu with Python 3.12 for every push and pull request. The job does not install ADB.

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

- Run the checks on an interval.
- Filter or analyze logcat output.
- Write a JSON or HTML report in addition to `report.txt`.
- Add checks for storage, network, foreground activity, and screen state.
