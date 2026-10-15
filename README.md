# Android Python Automation

Command-line checks for one Android device. The tool reads a JSON configuration file, runs five checks through ADB, writes a text report, and saves `adb logcat -d` when a check fails.

The runtime uses the Python standard library only. Python 3.12 or newer is required.

## Prerequisites and device setup

You need three things before a run can succeed: Python, the `adb` binary, and one device or emulator that `adb devices` lists as `device`.

**Python.** Install Python 3.12 or newer from [python.org](https://www.python.org/downloads/). On macOS the command is `python3`. On Windows, select "Add python.exe to PATH" during setup, and the command is `python`.

**Platform-Tools.** Install [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools) and put `adb` on `PATH`. Open a new terminal and confirm with `adb version`. The program looks up `adb` with `shutil.which` and does not search the Android SDK directory itself.

- macOS: `brew install --cask android-platform-tools`, or unzip the platform-tools download and add that directory to `PATH` in the shell profile.
- Windows: unzip the platform-tools download and add that folder to the user `Path` variable. If the phone does not appear, or stays `unauthorized` after the USB debugging prompt, install the phone maker's USB driver or the [Google USB Driver](https://developer.android.com/studio/run/win-usb).

**Device.** Enable Developer options and USB debugging on the phone, connect it, and accept the "Allow USB debugging?" prompt for this computer. An emulator needs no USB driver. `adb devices` must show the serial with state `device`. `unauthorized` and `offline` are not ready. The tool does not start the emulator, toggle debugging, or click the authorization dialog.

Run from the project root so the default paths `config.json`, `report.txt`, and `logs/` resolve next to `main.py`. Those paths are relative to the current working directory.

## How to edit configuration and run the tool

Edit `config.json` in the project root. The shipped file checks the Settings app on Android 10 or newer with a battery level above 20 percent:

```json
{
  "package_name": "com.android.settings",
  "minimum_android_version": 10,
  "minimum_battery": 20
}
```

| Field | Meaning |
| --- | --- |
| `package_name` | Android package name, such as `com.android.settings`. Each segment starts with a letter and then contains letters, digits, or underscores. |
| `minimum_android_version` | Integer from 1 through 99. The device's major version must be greater than or equal to this value. `8.1.0` counts as 8. |
| `minimum_battery` | Integer from 0 through 100. The battery level must be **greater than** this value. A level equal to the threshold fails, including `100` against a threshold of `100`. |
| `device_id` | Optional serial from `adb devices`. Omit the key, or set it to `null` or `""`, when only one device is in state `device`. Leading or trailing spaces are rejected. |

A float such as `10.0`, JSON `true` or `false`, and any key other than the four above are rejected. Missing required fields are named in the error.

Then run one pass:

```bash
# macOS
python3 main.py

# Windows
python main.py
```

Optional paths, all relative to the current directory unless you pass an absolute path:

```bash
python3 main.py --config config.json --report report.txt --logs logs
```

On success the program prints one line, `Overall: PASS` or `Overall: FAIL`, and the report path. `report.txt` lists the device, each check with a one-sentence explanation, the log path for each failure, and the totals. `SKIP` is counted and is not a failure. Overall is `PASS` when nothing failed.

The five checks, in order:

1. **Device is reachable.** `adb shell echo hello` exits 0 and prints `hello`.
2. **Android version.** The major version already read from `ro.build.version.release` meets `minimum_android_version`.
3. **Battery level.** The percent already read from `dumpsys battery` is above `minimum_battery`.
4. **Application installation.** `adb shell pm path <package>` exits 0 and prints a line that starts with `package:`.
5. **Launch application.** `monkey -p <package> -c android.intent.category.LAUNCHER 1` exits 0, and the output does not contain `No activities found` or `monkey aborted`. The tool does not look at the screen.

Version and battery reuse the facts collected before the checks. They do not call ADB again. If installation fails, launch is `SKIP` and `monkey` is not run.

## How to run the unit tests

Tests use pytest and do not need a device or an `adb` binary. They replace `subprocess.run`.

```bash
# macOS
python3 -m pip install pytest
python3 -m pytest

# Windows
python -m pip install pytest
python -m pytest
```

`pyproject.toml` puts the repository root on `sys.path` and collects `tests/`. The suite covers configuration validation, device selection, ADB parsing, a passing run, a failed check, log capture, report layout and totals, and exit codes 0, 1, and 2.

GitHub Actions runs the same `pytest` command on Ubuntu with Python 3.12 for every push and pull request. That job does not install ADB.

## Project structure and design choices

The repository root is the project directory.

```text
main.py                 Entry point, argument parsing, and exit codes
config.json             Values to edit before a run
android_runner/
    config.py           Load and validate configuration
    adb.py              ADB commands, device selection, device info
    runner.py           Five checks and failure logs
    report.py           report.txt
tests/                  pytest, with subprocess mocked
.github/workflows/ci.yml
logs/                   Failure logs created by a real run
```

Important choices:

- **Configuration is checked before ADB.** `load_config` finishes, including file, JSON, type, and range checks, before `AdbClient` is used. A bad config never starts a device command.
- **ADB is an argument list.** Every command is `["adb", ...]`. `shell=True` is not used, so package names and serials are not interpolated into a shell string. Each call waits at most 20 seconds, discards stdin, and decodes output as UTF-8 with replacement characters.
- **One device, no prompt.** With `device_id` unset, the only row in state `device` is selected. Several ready devices stop the run and print the serials. The named serial is used only when that exact serial is connected and ready. Another ready device is not a fallback.
- **Facts are read once.** `read_device_info` collects the release, model, manufacturer, and battery text. A timed-out property becomes missing (`None`) and the other properties are still read. The version and battery checks use those values.
- **A failed check does not stop the pass.** The five checks always run. Launch is the exception in behavior: it is skipped when installation did not pass, so a missing package is not also reported as a failed launch.
- **A failed log capture does not change the check.** After `FAIL`, the tool runs `adb logcat -d` and writes `logs/YYYYMMDD-HHMMSS_<check_name>.log`. If logcat or the write fails, the check stays `FAIL` and the report adds `Log capture failed`. `PASS` and `SKIP` do not write a log.
- **Launch is a command result, not a UI check.** Success means `monkey` accepted a launcher intent. The tool does not unlock the device, wait for an activity, or screenshot the result.

## Expected failure behavior and assumptions

There are two kinds of failure. Setup problems stop the run before any check. Check problems are recorded and the rest of the pass still runs.

| Exit code | When | `report.txt` |
| --- | --- | --- |
| 0 | Every check is `PASS` or `SKIP`. `SKIP` is not a failure. | Written |
| 1 | At least one check is `FAIL`. | Written |
| 2 | Configuration, missing `adb`, or device selection failed. | Not written |

**Setup (exit 2).** `ConfigError` and `AdbError` print one sentence to stderr and do not print a traceback. Typical cases:

- The config file is missing, unreadable, not UTF-8, or not valid JSON. The message includes the path. Invalid JSON also includes the parser message, line, and column.
- The JSON root is not an object, a required field is missing, or an unknown field is present. Every offending name is listed.
- `package_name` is not a package name. `minimum_android_version` or `minimum_battery` is a boolean, a float, or outside its range.
- `adb` is not on `PATH`.
- No device is connected. Devices are connected but none is `device`. The message names the serials and states it actually saw, and the hint matches those states: accept the USB debugging prompt for `unauthorized`, reconnect for `offline`.
- `device_id` is set and that serial is missing or not in state `device`.
- More than one device is ready and `device_id` is unset:

```text
More than one Android device is ready (SERIAL, SERIAL); set device_id in config.json to one of these serials.
```

Set `device_id` to one of those serials and run again.

An unexpected exception, such as a bug that is not `ConfigError` or `AdbError`, is not caught. It propagates with a traceback. A failure while writing `report.txt` is in that category: the checks already ran, and the process does not convert that I/O error into exit code 1 or 2.

**Checks (exit 1).** Each failed check stores one sentence. The report is still written, and later checks still run.

| Check | Fails when |
| --- | --- |
| Device is reachable | `echo hello` times out, exits non-zero, or prints something other than `hello`. |
| Android version | The release string is missing, does not start with a digit (`Next`, `v14`), or its major version is below the minimum. |
| Battery level | `dumpsys battery` has no usable level, or the percent is less than or equal to `minimum_battery`. The equal case says the level must be above the threshold. |
| Application installation | `pm path` times out, the command fails, or stdout has no `package:` line. A command failure does not say the package is missing. |
| Launch application | `monkey` times out or fails, or the output contains `No activities found` or `monkey aborted`. |

If installation is `FAIL` because the package is not installed, launch is `SKIP` with `Skipped because <package> is not installed.` If installation failed for another reason, launch is `SKIP` with `Skipped because installation of <package> could not be confirmed.`

**Assumptions.**

- The operator runs the tool locally, with `adb` already able to see the device. Wireless debugging works only to the extent that `adb devices` already lists the phone as `device`.
- Authorization has already happened. The tool will not retry an `unauthorized` device.
- The battery percent comes from `dumpsys battery`. A missing `scale`, or `scale: 100`, uses `level` as the percent. Any other positive scale uses `round(level * 100 / scale)`. `scale` of 0 or less, or a `level` line that is not an integer, makes the battery unreadable and fails the battery check.
- The Android version is the leading integer of `ro.build.version.release`. Preview strings that do not start with a digit fail the version check.
- The target package is already installed. This tool does not install, uninstall, or grant permissions.
- `monkey` exists on the device image, and a package that should pass the launch check has an activity in `android.intent.category.LAUNCHER`. Packages without a launcher activity fail launch even when they are installed.
- The saved log is the current logcat buffer (`logcat -d`), not a slice limited to that check or package. The buffer is not cleared first.
- Log file names use the local time and the check name. Two failures in the same second still get different files because the check names differ.
- An existing `report.txt` is overwritten. Parent directories of `--report` and `--logs` are created when a report or a failure log is written. If `--logs` points at a file, log capture fails, the check stays `FAIL`, and the other checks continue.
- `device_id` must match the serial exactly. A single ready device is not substituted when the named serial is absent.

## Improvements with more time

- Re-run the checks on an interval, and keep the previous report until the new pass finishes writing.
- Limit each failure log to the failing package and the time around that check, and record whether logcat itself timed out as its own line of context.
- Confirm launch by reading the resumed activity after `monkey`, so a command that exits 0 while the screen stays locked is a failure.
- Add checks for free storage, network connectivity, whether the screen is on, and whether the target package is in the foreground.
- Write a JSON report next to `report.txt` so another program can consume the same pass without parsing the text layout.
- Retry a single timed-out `adb` call once before failing the check. Device authorization and "more than one device" would stay hard stops.
- Treat a failure to write `report.txt` as exit code 2 with one sentence on stderr, consistent with the other setup errors.

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
