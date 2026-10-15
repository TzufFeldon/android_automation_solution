# Android Python Automation

Command-line checks for one Android device. The tool reads `config.json`, runs five checks through ADB, writes `report.txt`, and saves the most recent logcat lines when a check fails.

Python 3.12 or newer. The program uses the standard library only.

## Prerequisites and device setup

- Python 3.12 or newer from [python.org](https://www.python.org/downloads/). On macOS the command is `python3`. On Windows, select "Add python.exe to PATH" and use `python`.
- [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools) on `PATH`. Confirm with `adb version`.
  - macOS: `brew install --cask android-platform-tools`, or add the unzipped platform-tools directory to `PATH`.
  - Windows: add the unzipped platform-tools folder to `Path`. If the phone stays `unauthorized`, install the maker's USB driver or the [Google USB Driver](https://developer.android.com/studio/run/win-usb).
- One device or emulator that `adb devices` lists as `device`. Enable USB debugging and accept the prompt on the phone. An emulator needs no USB driver. `unauthorized` and `offline` are not ready.

Run from the project root. `config.json`, `report.txt`, and `logs/` are relative to the current directory.

## Configuration and how to run

Edit `config.json`:

```json
{
  "package_name": "com.android.settings",
  "minimum_android_version": 10,
  "minimum_battery": 20
}
```

| Field | Meaning |
| --- | --- |
| `package_name` | Package name, such as `com.android.settings`. |
| `minimum_android_version` | Integer from 1 through 99. `8.1.0` counts as 8. |
| `minimum_battery` | Integer from 0 through 100. The level must be greater than this value. Equal fails, including 100 against 100. |
| `device_id` | Optional serial. Omit it, or set `null` or `""`, when only one device is ready. Leading or trailing spaces are rejected. |
| `interval_seconds` | Optional wait between passes, an integer from 1 through 86400. Omit it or set `null` for a single pass. Ctrl+C stops after the last completed pass and keeps that exit code. |

Floats, booleans, and unknown keys are rejected. Missing fields are named in the error.

```bash
# macOS
python3 main.py

# Windows
python main.py
```

Optional paths: `--config`, `--report`, and `--logs` (defaults are `config.json`, `report.txt`, and `logs`).

The five checks run in this order:

1. **Device is reachable.** `echo hello` exits 0 and prints `hello`.
2. **Android version.** The major version from `ro.build.version.release` is at least the minimum.
3. **Battery level.** The percent from `dumpsys battery` is above the minimum.
4. **Application installation.** `pm path <package>` prints a line that starts with `package:`.
5. **Launch application.** `monkey` with category `android.intent.category.LAUNCHER` exits 0, and the output does not contain `No activities found` or `monkey aborted`. The screen is not inspected.

Version and battery reuse values already collected. If installation fails, launch is `SKIP` and `monkey` is not run.

## Unit tests

```bash
# macOS
python3 -m pip install pytest
python3 -m pytest

# Windows
python -m pip install pytest
python -m pytest
```

The tests mock `subprocess.run`. They cover configuration, device selection, a passing run, a failed check, log capture, the report, and exit codes 0, 1, and 2. No device or `adb` binary is required.

GitHub Actions runs `pytest` from `uv.lock` on Ubuntu with Python 3.12, on pushes to `main` and on pull requests. That job does not install ADB.

## Project structure and design choices

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
logs/                   Failure logs from a run
```

- Configuration is validated before any ADB call. With `interval_seconds`, each pass selects the device again and overwrites `report.txt`. A pass that cannot start or cannot write the report stops the loop with exit 2.
- ADB commands are argument lists. `shell=True` is not used. Each call times out after 20 seconds.
- Device selection does not prompt. Several ready devices require `device_id`.
- A failed check does not stop the others. A failed log capture leaves the check as `FAIL` and adds `Log capture failed` to the report.
- Launch success is the `monkey` result. The tool does not unlock the screen or check the foreground app.

## Failure behavior and assumptions

| Exit code | When | `report.txt` |
| --- | --- | --- |
| 0 | No check failed. `SKIP` is not a failure. | Written |
| 1 | At least one check failed. | Written |
| 2 | Bad configuration, missing `adb`, no selectable device, or the report cannot be written. | Not written |

Exit 2 prints one sentence to stderr, without a traceback. The message names only states that are connected: accept the USB debugging prompt for `unauthorized`, reconnect for `offline`. Several ready devices print:

```text
More than one Android device is ready (SERIAL, SERIAL); set device_id in config.json to one of these serials.
```

A failed check writes `logs/YYYYMMDD-HHMMSS_<check_name>.log` from `adb logcat -d -t 2000`, the most recent 2000 lines. A log still larger than 1 MB is cut to the most recent 1 MB, and the report adds `Log truncated to the most recent 1 MB.` `PASS` and `SKIP` do not write a log.

Assumptions:

- `adb devices` already shows the phone as `device`. The tool does not authorize the computer, install the package, or grant permissions.
- A missing battery `scale`, or `scale: 100`, uses `level` as the percent. Any other positive scale uses `round(level * 100 / scale)`. A result outside 0 through 100 is unreadable.
- The saved log is the most recent 2000 logcat lines, cut to 1 MB when that is still too large. It is not limited to that check or package.
- `device_id` must match the serial exactly. An existing `report.txt` is overwritten.

## Improvements with more time

- Limit each failure log to the package and the time of that check.
- Confirm launch from the resumed activity, not only from `monkey`.
- Add checks for storage, network, screen state, and the foreground app.
- Write a JSON report next to `report.txt`.

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
    Battery level is 15%, which is lower than 20%.
    Log: logs/20261013-215301_battery_level.log
Application installation: FAIL
    Package com.android.settings is not installed.
    Log: logs/20261013-215302_application_installation.log
Launch application: SKIP
    Skipped because com.android.settings is not installed.

Totals
------
Passed: 2
Failed: 2
Skipped: 1
All: 5

Overall: FAIL
```
