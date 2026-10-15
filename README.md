# Android Python Automation

Command-line checks for one Android device. The tool reads `config.json`, runs nine checks through ADB, writes `report.txt` and `report.json`, and saves the most recent logcat lines when a check fails.

Python 3.12 or newer. The program uses the standard library only.

## Prerequisites and device setup

- Python 3.12 or newer from [python.org](https://www.python.org/downloads/). On macOS the command is `python3`. On Windows, select "Add python.exe to PATH" and use `python`.
- [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools) on `PATH`. Confirm with `adb version`.
  - macOS: `brew install --cask android-platform-tools`, or add the unzipped platform-tools directory to `PATH`.
  - Windows: add the unzipped platform-tools folder to `Path`. If the phone stays `unauthorized`, install the maker's USB driver or the [Google USB Driver](https://developer.android.com/studio/run/win-usb).
- A device or emulator that `adb devices` lists as `device`. When several are ready, a terminal asks which one to check. Enable USB debugging and accept the prompt on the phone. An emulator needs no USB driver. `unauthorized` and `offline` are not ready.

Run from the project root. `config.json`, `report.txt`, `report.json`, and `logs/` are relative to the current directory.

## Configuration and how to run

Edit `config.json`:

```json
{
  "package_name": "com.android.settings",
  "minimum_android_version": 10,
  "minimum_battery": 20,
  "minimum_free_storage_mb": 100,
  "interval_seconds": 30
}
```

| Field | Meaning |
| --- | --- |
| `package_name` | Package name, such as `com.android.settings`. |
| `minimum_android_version` | Integer from 1 through 99. `8.1.0` counts as 8. |
| `minimum_battery` | Integer from 0 through 100. The level must be greater than this value. Equal fails, including 100 against 100. |
| `minimum_free_storage_mb` | Integer from 0 through 1048576. Free mebibytes on `/data` must be greater than this value. Equal fails. |
| `device_id` | Optional serial. Omit it, or set `null` or `""`, to use the only ready device, or to choose from a numbered list when a terminal has several. Leading or trailing spaces are rejected. |
| `interval_seconds` | Optional wait between passes, an integer from 1 through 86400. Omit it or set `null` for a single pass. Ctrl+C stops after the last completed pass and keeps that exit code. Later passes keep the serial chosen on the first pass. |

Floats, booleans, and unknown keys are rejected. Missing fields are named in the error.

```bash
# macOS
python3 main.py

# Windows
python main.py
```

Optional paths: `--config`, `--report`, and `--logs` (defaults are `config.json`, `report.txt`, and `logs`). Each completed pass also writes a JSON report beside the text file. The JSON path uses a `.json` suffix, so `report.txt` is accompanied by `report.json`. A `--report` path that already ends in `.json` stays the text file, and the JSON copy is named `<name>.json.json`.

The nine checks run in this order:

1. **Device is reachable.** `echo hello` exits 0 and prints `hello`.
2. **Android version.** The major version from `ro.build.version.release` is at least the minimum.
3. **Battery level.** The percent from `dumpsys battery` is above the minimum.
4. **Free storage.** Available space from `df -k /data` is above `minimum_free_storage_mb`. The value is whole mebibytes, truncated toward zero.
5. **Network.** `ip route show table all` prints a line whose first field is `default` and that does not contain `dummy0`. A missing route means the device cannot reach other networks.
6. **Screen state.** `dumpsys power` reports wakefulness `Awake`. When that line is absent, `mScreenOn=true` passes. The tool does not unlock the screen or turn it on.
7. **Application installation.** `pm path <package>` prints a line that starts with `package:`.
8. **Launch application.** `monkey` with category `android.intent.category.LAUNCHER` exits 0, and the output does not contain `No activities found` or `monkey aborted`.
9. **Foreground activity.** The first `topResumedActivity` belongs to the configured package. When that marker is absent, the first `mResumedActivity` is used.

Version and battery reuse values already collected. If installation fails, launch and the foreground check are `SKIP`. `monkey` is not run, and the resumed activity is not read.

## Unit tests

```bash
# macOS
python3 -m pip install pytest
python3 -m pytest

# Windows
python -m pip install pytest
python -m pytest
```

The tests mock `subprocess.run`. They cover configuration, device selection, the terminal prompt, a repeated run, a passing run, a failed check, log capture, the text report, the JSON report, and exit codes 0, 1, and 2. No device or `adb` binary is required.

GitHub Actions runs `pytest` from `uv.lock` on Ubuntu with Python 3.12, on pushes to `main` and on pull requests. That job does not install ADB.

## Project structure and design choices

```text
main.py                 Entry point and exit codes
config.json             Values to edit
android_runner/
    config.py           Load and validate configuration
    adb.py              ADB commands, device selection, device info
    runner.py           Nine checks and failure logs
    report.py           report.txt and report.json
tests/                  pytest, with subprocess mocked
.github/workflows/ci.yml
logs/                   Failure logs from a run
```

- Configuration is validated before any ADB call. With `interval_seconds`, each pass overwrites `report.txt` and `report.json`. A pass that cannot start or cannot write either file stops the loop with exit 2.
- ADB commands are argument lists. `shell=True` is not used. Each call times out after 20 seconds.
- Several ready devices prompt on a terminal once per process. Later passes of an interval run keep that serial. `device_id` skips the prompt. Without a terminal the run exits 2 and names the serials.
- A failed check does not stop the others. A failed log capture leaves the check as `FAIL` and says whether the device clock, logcat, or the log file was the problem.
- Launch success is the `monkey` result. A later check reads the resumed activity. Screen state is read from `dumpsys power` and is not changed.

## Failure behavior and assumptions

| Exit code | When | `report.txt` and `report.json` |
| --- | --- | --- |
| 0 | No check failed. `SKIP` is not a failure. | Written |
| 1 | At least one check failed. | Written |
| 2 | Bad configuration, missing `adb`, no selectable device, or either report file cannot be written. | Not written for that pass |

Exit 2 prints one sentence to stderr, without a traceback. The message names only states that are connected: accept the USB debugging prompt for `unauthorized`, reconnect for `offline`. On a terminal, several ready devices print a numbered list and wait for a choice. Without a terminal they print:

```text
More than one Android device is ready (SERIAL, SERIAL); set device_id in config.json to one of these serials.
```

A failed check writes `logs/YYYYMMDD-HHMMSS_<check_name>.log`. Before the check, the tool reads the device clock with `adb shell date +%m-%dT%H:%M:%S`, replaces `T` with a space, and appends `.000`. The `T` keeps the format as one argument, because the device `date` command rejects a format that contains a space. The log is `adb logcat -d -t MM-DD HH:MM:SS.mmm` from that time. The buffer is not cleared. An empty window is saved as returned. If the clock cannot be read, no log file is written and the report says the device clock could not be read. A logcat failure says logcat could not be read. A file failure says the log file could not be written. Installation, launch, and foreground logs keep lines that contain the package name, and a `FATAL EXCEPTION` or `AndroidRuntime` block through the next blank line. Other checks keep the whole window. Threadtime lines at level `E` or `F` add `N error lines in this check. First: ...` and do not change `PASS` or `FAIL`. A log still larger than 1 MB is cut to the most recent 1 MB, and the report adds `Log truncated to the most recent 1 MB.` `PASS` and `SKIP` do not write a log.

Assumptions:

- `adb devices` already shows the phone as `device`. The tool does not authorize the computer, install the package, or grant permissions.
- A missing battery `scale`, or `scale: 100`, uses `level` as the percent. Any other positive scale uses `round(level * 100 / scale)`. A result outside 0 through 100 is unreadable.
- The saved log starts at the device clock reading taken before that check, and is cut to 1 MB when it is still too large. Installation, launch, and foreground logs also keep the package and crash blocks.
- Free storage is the `Available` column of `df -k /data`, in 1024-byte blocks, divided by 1024 and truncated. A missing column is unreadable.
- Network passes when a routing line starts with `default` and does not contain `dummy0`. The tool does not open a connection.
- Screen state passes for wakefulness `Awake`, or for `mScreenOn=true` when no wakefulness line is present. `Asleep`, `Dozing`, and `Dreaming` fail. Numeric wakefulness `0` through `3` uses the same names.
- `device_id` must match the serial exactly. An existing `report.txt` and `report.json` are overwritten.

## Improvements with more time

- Confirm launch from the resumed activity, not only from `monkey`.

## Sample report

```text
Android Automation Report: emulator-5554
========================================
Run: 2026-10-15 14:33:07

Device
------
Device ID: emulator-5554
Android version: 14
Model: sdk_gphone64_arm64
Manufacturer: Google
Battery: 15%

Checks
------
Device is reachable: PASS
    Command 'echo hello' succeeded and returned 'hello'.
Android version: PASS
    Major version 14 is greater than or equal to minimum 10.
Battery level: FAIL
    Battery level is 15%, which is lower than 20%.
    Log: logs/20261013-215301_battery_level.log
Free storage: PASS
    Free storage is 200 MB, which is greater than 100 MB.
Network: PASS
    A default network route is present.
Screen state: PASS
    Screen state is Awake.
Application installation: FAIL
    Package com.android.settings is not installed.
    Log: logs/20261013-215302_application_installation.log
Launch application: SKIP
    Skipped because com.android.settings is not installed.
Foreground activity: SKIP
    Skipped because com.android.settings is not installed.

Totals
------
Passed: 5
Failed: 2
Skipped: 2
All: 9

Overall: FAIL
```

`report.json` carries the same pass. Unread device fields are JSON `null`. An empty string stays `""`. `log_path` is a POSIX path when a failure log was saved, and `null` otherwise. The `checks` array has one object per check. The sample below shows the battery failure.

```json
{
  "ran_at": "2026-10-15 14:33:07",
  "device": {
    "id": "emulator-5554",
    "android_version": "14",
    "android_major": 14,
    "model": "sdk_gphone64_arm64",
    "manufacturer": "Google",
    "battery_percent": 15
  },
  "checks": [
    {
      "name": "Battery level",
      "status": "FAIL",
      "explanation": "Battery level is 15%, which is lower than 20%.",
      "log_path": "logs/20261013-215301_battery_level.log",
      "log_note": null,
      "log_error": null
    }
  ],
  "totals": {
    "passed": 5,
    "failed": 2,
    "skipped": 2,
    "all": 9
  },
  "overall": "FAIL"
}
```
