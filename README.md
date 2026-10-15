# Android Python Automation

Checks one Android device through ADB and writes `report.txt` and `report.json`. A failed check also saves recent logcat lines under `logs/`. Python 3.12 or newer, standard library only.

## Prerequisites and device setup

- Python 3.12+. On macOS use `python3`. On Windows, add `python.exe` to `PATH` and use `python`.
- [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools) on `PATH` (`adb version`).
  - macOS: `brew install --cask android-platform-tools`
  - Windows: add the unzipped folder to `Path`. If the phone stays `unauthorized`, install the maker's USB driver or the [Google USB Driver](https://developer.android.com/studio/run/win-usb).
- `adb devices` must list the phone or emulator as `device`. Enable USB debugging and accept the prompt. `unauthorized` and `offline` are not ready.

Run from the project root. Paths are relative to the current directory. The tool does not search for `adb` outside `PATH`.

## Configuration

Edit `config.json`.

```json
{
  "package_name": "com.android.settings",
  "minimum_android_version": 10,
  "minimum_battery": 20,
  "minimum_free_storage_mb": 100,
  "interval_seconds": 30
}
```


| Field                     | Meaning                                                                                                                         |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `package_name`            | Package to check, launch, and find in the foreground.                                                                           |
| `minimum_android_version` | Integer 1–99. `8.1.0` counts as 8.                                                                                              |
| `minimum_battery`         | Integer 0–100. The level must be greater than this value.                                                                       |
| `minimum_free_storage_mb` | Integer 0–1048576. Free MiB on `/data` must be greater than this value.                                                         |
| `device_id`               | Optional serial. Omit it, or set `null` or `""`, to use the only ready device, or to choose from a list when several are ready. |
| `interval_seconds`        | Optional seconds between passes, 1–86400. Omit it or set `null` for one pass. Ctrl+C stops after the current pass.              |


`package_name`, `minimum_android_version`, `minimum_battery`, and `minimum_free_storage_mb` are required. Floats, booleans, and unknown keys are rejected. Missing fields are named in the error. The file is validated before any ADB call.

## Run

```bash
python3 main.py   # macOS
python main.py    # Windows
```

Each run reads `config.json`, writes `report.txt` and `report.json`, and saves failure logs under `logs/`.

Several ready devices prompt once on a terminal. Without a terminal the run exits 2, names the serials, and says to run in a terminal or set `device_id`. Later interval passes keep the serial from the first pass and overwrite both reports. Ctrl+C during a pass or during the wait stops after the current pass and keeps the exit code of the last completed report. Ctrl+C before any report exits 2.

## Checks

A failure does not stop the rest. If installation fails, launch and the foreground check are `SKIP`. The tool does not install the package, turn the screen on, unlock the device, join a network, or free storage.

1. Device answers `echo hello`.
2. Android major version meets the minimum.
3. Battery percent is above the minimum.
4. Free space on `/data` is above the minimum.
5. A default route exists and is not `dummy0`.
6. The screen is awake. The tool does not turn it on.
7. The package is installed (`pm path`).
8. `monkey` can launch it. Success is the command result, not a visual check.
9. The resumed activity belongs to that package.

Checks 1–3 and 7–8 are the required set. Checks 4–6 and 9 are extra checks from the optional extensions (storage, network, screen state, foreground activity).

A failed check writes `logs/YYYYMMDD-HHMMSS_<check_name>.log`. The file is logcat from the device clock read just before that check, cut to the most recent 1 MB when larger. Installation, launch, and foreground logs keep lines that mention the package, plus crash blocks. `PASS` and `SKIP` do not write a log. If the clock, logcat, or the write fails, the check stays `FAIL` and the report says which step failed.

## Reports

`report.txt` lists the device, each check with short label lines, totals, and an overall `PASS` or `FAIL`. A threshold check prints the measured value, then `Minimum required` on the next line. Device fields are the serial, Android version, model, manufacturer, and battery percent. Unread text fields print as `unknown`.

`report.json` has the same result: device fields, a `checks` array, `totals`, and `overall`. Unread device fields are JSON `null`. `log_path` is set when a failure log was saved.

`Overall` is `PASS` when no check is `FAIL`. `SKIP` is not a failure.

## Project structure and design choices

```text
android_automation_solution/
|-- main.py
|-- config.json
|-- android_runner/
|   |-- adb.py
|   |-- config.py
|   |-- runner.py
|   |-- report.py
|-- tests/
|-- logs/
|-- README.md
```


| Module                  | Responsibility                                                                                |
| ----------------------- | --------------------------------------------------------------------------------------------- |
| `main.py`               | Own the exit code and the optional interval loop.                                              |
| `android_runner.config` | Read and validate `config.json` into `AppConfig`.                                             |
| `android_runner.adb`    | Run ADB, select one serial, and collect device facts.                                         |
| `android_runner.runner` | Run the checks and attach a failure log.                                                      |
| `android_runner.report` | Render and write `report.txt` and the sibling JSON report.                                    |


Design choices:

- Configuration is validated before the first ADB call. A bad file exits 2 with one sentence and no traceback.
- One process checks one device. `device_id` selects that serial. Without it, the only ready device is used. Several ready devices prompt on a terminal, or exit 2 when stdin is not a terminal.
- ADB is invoked as an argument list. `shell=True` is not used. Each call waits at most 20 seconds.
- Version and battery are read once with the other device facts and reused by those checks. A field that cannot be read stays empty and fails its own check. It does not abort the run.
- Launch success is the `monkey` result. The foreground check is separate and reads the resumed activity afterward.
- `interval_seconds` repeats the same pass and overwrites both reports. The serial chosen on the first pass is kept, so the prompt is not repeated.
- JSON is written beside the text report so a person and a script can read the same run. There is no HTML report.



## Failure behavior and assumptions


| Exit | When                                                                            | Report                    |
| ---- | ------------------------------------------------------------------------------- | ------------------------- |
| 0    | No check failed. `SKIP` is not a failure.                                       | Written                   |
| 1    | At least one check failed.                                                      | Written                   |
| 2    | Bad config, missing `adb`, no selectable device, or a report cannot be written. | Not written for that pass |


Exit 2 prints one sentence on stderr and does not print a traceback.


| Condition                         | What the tool prints                                                                                                        |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `adb` is not on `PATH`            | Install Android SDK Platform-Tools and verify with `adb version`.                                                           |
| No device                         | No Android device or emulator is connected.                                                                                 |
| `unauthorized` or `offline`       | The serial, the state, and how to correct it.                                                                               |
| Several ready devices, no terminal | The serials, then to run in a terminal or set `device_id`.                                                                  |
| Invalid configuration             | The missing or invalid field. No traceback.                                                                                 |
| An ADB command fails or times out | A short command error. Device selection stops the run. A check command fails that check and the remaining checks still run. |
| Logcat cannot be saved            | The original `FAIL` is kept, the report names the failed log step, and the report is still written.                         |


Assumptions:

- The computer is already authorized for USB debugging. The tool does not accept the device prompt for you.
- State `device` is the only usable state. `unauthorized` and `offline` are reported and not checked.
- The configured package is already installed when launch should be attempted. A missing package is `FAIL` for installation and `SKIP` for launch and foreground.
- The screen, network, and free space are only observed. The tool does not change them.
- A default route on `dummy0` does not count as connectivity.
- Paths are relative to the working directory.
- A continuous run overwrites `report.txt` and `report.json`. Earlier passes are not kept.
- Tests mock `subprocess.run`. They do not need a device or an `adb` binary.



## Tests

```bash
python3 -m pip install pytest && python3 -m pytest   # macOS
python -m pip install pytest && python -m pytest     # Windows
```

Tests mock `subprocess.run`. No device or `adb` binary is required. They cover valid and invalid configuration, a ready device and an unauthorized device, a full passing run, a failing check, log capture, and report totals. GitHub Actions runs `pytest` on Ubuntu with Python 3.12.

## Future Improvements

- Keep each interval pass instead of overwriting the same two files.
- Retry one timed-out ADB command before failing the check.
- After `monkey`, wait briefly for the package to become the resumed activity instead of reading it once immediately.
- Make storage, network, screen, and foreground optional in config, so a file with only the three base fields still runs.
- Summarize crash blocks in the failure log (exception and process) instead of only counting error lines.
- Run the same pytest job on Windows as well as Ubuntu.

