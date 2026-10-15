# Android Python Automation

Checks one Android device through ADB and writes `report.txt` and `report.json`. A failed check also saves recent logcat lines under `logs/`. Python 3.12 or newer, standard library only.

## Prerequisites and device setup

- Python 3.12+. On macOS use `python3`. On Windows, add `python.exe` to `PATH` and use `python`.
- [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools) on `PATH` (`adb version`).
  - macOS: `brew install --cask android-platform-tools`
  - Windows: add the unzipped folder to `Path`. If the phone stays `unauthorized`, install the maker's USB driver or the [Google USB Driver](https://developer.android.com/studio/run/win-usb).
- `adb devices` must list the phone or emulator as `device`. Enable USB debugging and accept the prompt on the device. The tool does not accept that prompt. `unauthorized` and `offline` are not ready.

Run from the project root. Paths are relative to the current directory. The tool does not search for `adb` outside `PATH`.

## Configuration

Edit `config.json`.

```json
{
  "package_name": "com.android.settings",
  "minimum_android_version": 10,
  "minimum_battery": 20,
  "minimum_free_storage_mb": 100
}
```

| Field | Meaning |
| --- | --- |
| `package_name` | Package to check, launch, and match in the foreground. A dotted name such as `com.android.settings`: at least two segments. Each segment starts with a letter and may continue with letters, digits, or underscores. |
| `minimum_android_version` | Integer 1–99. The major version must be at least this value. `8.1.0` counts as 8. |
| `minimum_battery` | Integer 0–100. The battery percent must be greater than this value. |
| `minimum_free_storage_mb` | Optional integer 0–1048576. Omit it or set `null` to skip the storage check. Free mebibytes on `/data` must be greater than this value. The report prints the number as MB. |

`package_name`, `minimum_android_version`, and `minimum_battery` are required. A file with only those three fields runs one pass and skips the storage check. The shipped file also sets `minimum_free_storage_mb`, so `python3 main.py` checks storage once and exits. Numeric fields must be integers. Floats, numeric strings, booleans, and unknown keys are rejected. Missing required fields and unknown keys are named in the error. The file is validated before any ADB call.

Two keys are accepted when you add them. `device_id` is an optional serial. Omit it, or set `null` or `""`, to use the only ready device, or to choose when several are ready. Leading or trailing spaces are rejected. `interval_seconds` is an optional wait between passes, from 1 to 86400. Omit it or set `null` for one pass.

## Run

```bash
python3 main.py   # macOS
python main.py    # Windows
```

Each run reads `config.json`. The first pass prints the chosen serial. After the checks it writes `report.txt` and `report.json`, then prints the time, the overall result, and those two paths. A failed check saves a log under `logs/`.

Several ready devices prompt once on a terminal. Enter a row number or a serial. A row number selects that row. Closing the prompt without a choice exits 2. Without a terminal the run exits 2, names the serials, and says to run in a terminal or set `device_id`. Later interval passes keep that serial and do not prompt again. Each completed pass overwrites both reports.

Ctrl+C during a pass or during the wait keeps the exit code of the last completed report. Ctrl+C before any report exits 2. A later pass that cannot select the device or write the report exits 2 and leaves the previous files in place.

## Checks

A failed check does not stop the rest. The tool does not install the package, turn the screen on, unlock the device, join a network, or free storage. Storage is the only check with a setting: omit `minimum_free_storage_mb` or set it to `null` and that check is `SKIP`. Network and screen always run. Launch and foreground are `SKIP` only when installation failed.

1. Device answers `echo hello`.
2. Android major version is at least `minimum_android_version`.
3. Battery percent is greater than `minimum_battery`.
4. Free space on `/data` is greater than `minimum_free_storage_mb`. `SKIP` when that field is omitted or `null`. The number is available KiB from `df -k`, divided by 1024 and rounded down. The report labels it MB.
5. A default route exists, and that route is not `dummy0`.
6. The screen is `Awake`. `Asleep`, `Dozing`, `Dreaming`, and an unreadable state fail. The tool does not turn the screen on.
7. The package is installed (`pm path`).
8. `monkey` can launch it. Success is the command result, not a visual check. `SKIP` when installation failed.
9. The resumed activity belongs to that package. `SKIP` when installation failed.

A missing package skips launch and foreground as not installed. An installation check that timed out or could not be confirmed skips them as unconfirmed.

A failed check writes `logs/YYYYMMDD-HHMMSS_<check_name>.log`. The name uses the computer clock. The contents are logcat from the device clock read just before that check, cut to the most recent 1 MB when larger. The report notes the cut. Installation, launch, and foreground logs keep lines that mention the package, plus crash blocks. The other failure logs keep the logcat slice as read. `PASS` and `SKIP` do not write a log. If the clock, logcat, or the write fails, the check stays `FAIL` and the report names that step. A saved log can also note how many error lines it contains, and the text of the first one.

## Reports

`report.txt` lists the device, each check, totals, and an overall `PASS` or `FAIL`. A threshold check prints the measured value, then `Minimum required` on the next line. The device block is the serial, the full Android version, model, manufacturer, and battery percent. Unread fields print as `unknown`. The version check prints the major used for the comparison, so `8.1.0` is `8.1.0` in the device block and `8` on the check.

`report.json` has the same result. The device object is `id`, `android_version`, `android_major`, `model`, `manufacturer`, and `battery_percent`. Unread values are JSON `null`. Each check has `name`, `status`, `explanation`, `log_path`, `log_note`, and `log_error`. `log_path` is set when a failure log was saved.

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

| Module | Responsibility |
| --- | --- |
| `main.py` | Own the exit code and the optional interval loop. |
| `android_runner.config` | Read and validate `config.json` into `AppConfig`. |
| `android_runner.adb` | Run ADB, select one serial, and collect device facts. |
| `android_runner.runner` | Run the checks and attach a failure log. |
| `android_runner.report` | Render and write `report.txt` and the sibling JSON report. |

Design choices:

- Configuration is validated before the first ADB call. A bad file exits 2 with one sentence and no traceback.
- One process checks one device. The serial chosen on the first pass is kept for later passes.
- Each ADB call waits at most 20 seconds. Version, model, manufacturer, and battery are read once and reused. A timeout or an empty reading leaves that field unread and fails only the check that needs it. Any other ADB error while selecting the device or reading those facts stops the run before the checks.
- Launch success is the `monkey` result. The foreground check reads the resumed activity afterward.
- `interval_seconds` repeats the same pass and overwrites both reports.

## Failure behavior

| Exit | When | Report |
| --- | --- | --- |
| 0 | No check failed. `SKIP` is not a failure. | Written |
| 1 | At least one check failed. | Written |
| 2 | Bad config, missing `adb`, the device cannot be selected, reading device facts fails for a reason other than a timeout, or a report cannot be written. | Not written for that pass |

Exit 2 prints one sentence on stderr and does not print a traceback.

| Condition | What the tool prints |
| --- | --- |
| `adb` is not on `PATH` | Install Android SDK Platform-Tools and verify with `adb version`. |
| No device | No Android device or emulator is connected. |
| `unauthorized` or `offline` | The serial, the state, and how to correct it. |
| Several ready devices, no terminal | The serials, then to run in a terminal or set `device_id`. |
| Invalid configuration | The configuration error, in one sentence. No traceback. |
| A check command fails or times out | That check is `FAIL` in the report. The remaining checks still run. |
| The device cannot be selected, or reading device facts hits an ADB error other than a timeout | One sentence on stderr. The run stops. No report for that pass. |
| Logcat cannot be saved | The original `FAIL` is kept, the report names the failed log step, and the report is still written. |

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
- Summarize crash blocks in the failure log (exception and process) instead of only counting error lines.
- Run the same pytest job on Windows as well as Ubuntu.
