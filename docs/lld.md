# Low-Level Design

Module behavior behind the [high-level design](hld.md). Names below match the code.

## Types

`AppConfig` (`config.py`) is frozen. Fields: `package_name: str`, `minimum_android_version: int`, `minimum_battery: int`, `device_id: str | None`, `interval_seconds: int | None`.

`DeviceRecord` is one `adb devices` row: `serial`, `state`.

`DeviceInfo` holds facts for the selected serial. `android_version`, `android_major`, `model`, `manufacturer`, and `battery_percent` are `None` when that read failed or timed out. `serial` is always set.

`CommandResult` is `returncode`, `stdout`, and `stderr` of one ADB invocation. A non-zero shell exit is returned to the caller. It is not an `AdbError`.

`CheckResult` is one check: `name`, `status` (`PASS`, `FAIL`, or `SKIP`), `explanation`, and optional `log_path`, `log_error`, `log_note`, and `install_cause`. `install_cause` is set only on a failed installation check.

`ConfigError`, `AdbError`, and `ReportError` each carry one sentence. `AdbError.timed_out` is true only when `subprocess` hits the time limit.

## `main.main`

`_parse_args` accepts `--config` (default `config.json`), `--report` (default `report.txt`), and `--logs` (default `logs`).

```text
load_config(args.config)          ConfigError → stderr, return 2
loop:
  select_device(...)              AdbError    → stderr, return 2
  read_device_info(...)
  run_checks(...)
  write_report(...)               ReportError → stderr, return 2
  print overall line
  code = 1 if any Status.FAIL else 0
  interval_seconds is None        return code
  time.sleep(interval_seconds)    KeyboardInterrupt → stop
```

`SKIP` does not count as failed. The process entry point is `raise SystemExit(main())`.

`interval_seconds` absent or JSON `null` is one pass. A set value repeats from device selection. Sleep starts only after a pass that returned `0` or `1`. The report path is overwritten on each completed pass.

Ctrl+C during a pass or during sleep calls `_stop`. When a pass has already returned `0` or `1`, stderr is `Stopped.` and that code is returned. When none has, stderr is `Stopped before a report was written.` and the code is `2`. A `KeyboardInterrupt` does not print a traceback.

## Configuration (`config.load_config`)

Read UTF-8, parse JSON, then validate. File errors become `ConfigError` and drop the original traceback (`from None`).

| Condition | Message shape |
| --- | --- |
| Missing file | `Configuration file not found: {path}.` |
| Path is a directory | `Configuration path is a directory: {path}.` |
| Not readable | `Configuration file is not readable: {path}.` |
| Not UTF-8 | `Configuration file is not valid UTF-8: {path}.` |
| Other `OSError` | `Configuration file could not be read: {path} ({strerror}).` |
| Bad JSON | Includes `json.JSONDecodeError.msg`, line, and column. |
| Root is not an object | `Configuration root must be a JSON object.` |

Unknown and missing keys are reported together, in one sentence. Unknown names are sorted. Missing names stay in required order: `package_name`, `minimum_android_version`, `minimum_battery`.

`package_name` must be a string matching `^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$`. A value longer than 80 characters omits the received text from the message.

`minimum_android_version` is an integer from 1 through 99. `minimum_battery` is an integer from 0 through 100. `bool` is rejected before the integer check, because `bool` is a subclass of `int` in Python. Floats are rejected.

`device_id` absent, JSON `null`, or `""` becomes `None`. Any other non-string is rejected. A string that is not equal to its stripped form is rejected. The stored value is otherwise unchanged, including internal spaces.

`interval_seconds` absent or JSON `null` becomes `None`. Any other value uses the same integer rules as the required numbers: `bool` and floats are rejected, and the accepted range is 1 through 86400.

## ADB client (`adb.AdbClient`)

`_run` checks `shutil.which("adb")`, then runs `["adb", *args]` with:

- `stdin=subprocess.DEVNULL`
- `capture_output=True`, `text=True`, `encoding="utf-8"`, `errors="replace"`
- `timeout=TIMEOUT_SECONDS` (20)
- `check=False`

`FileNotFoundError` and a missing `adb` on `PATH` both raise `AdbError` with the platform-tools sentence. `TimeoutExpired` raises `AdbError` with `timed_out=True`. Stdout and stderr `None` become `""`.

`shell` is `adb -s {serial} shell {args...}` and returns `CommandResult` even when the exit code is non-zero.

`_command_text` returns stripped stdout, or `None` when the exit code is non-zero, the output is empty, or the call timed out. Any other `AdbError` propagates. `read_device_info` uses this for three properties and for `dumpsys battery`, so a timeout on one field leaves that field `None` and the run continues.

### Device list

`list_devices` runs `adb devices` and requires exit code 0. `_parse_devices` skips blank lines, the header `List of devices attached`, and lines starting with `*`. Each remaining line needs at least two whitespace-separated tokens. Token 0 is the serial. Token 1 is the state. Later tokens are ignored.

### Selection

`select_device(None)` and `select_device("")` call `_select_only_ready`:

| Connected devices | Result |
| --- | --- |
| None | `No Android device or emulator is connected.` |
| Rows exist, none in state `device` | Lists `serial (state)` and a correction for the states present. |
| Exactly one `device` | That serial. |
| More than one `device` | Names the ready serials and tells the caller to set `device_id`. |

`unauthorized` adds “accept the USB debugging prompt on the device”. `offline` adds “reconnect a device that is offline”. Any other state, or a set with no specific hint, adds the generic reconnect sentence. Two or more hints are joined with commas and a final `or`.

`select_device(serial)` calls `_select_named`. The serial must match exactly.

| Match | Result |
| --- | --- |
| No rows at all | Not found, and nothing is connected. |
| Rows exist, serial absent | Not found, plus `connected devices: serial (state), ...`. |
| Serial present, state is not `device` | Names the state and the same correction text. |
| State is `device` | That serial. |

### Version and battery parsing

`_major_version` returns the leading integer of the stripped release string. `8.1.0` is `8`. No leading digits, or a missing version, is `None`.

`_battery_percent` scans `dumpsys battery` for the first `level: <int>` and the first `scale: <int>`.

| Input | Percent |
| --- | --- |
| No `level` line, or dumpsys text is `None` | `None` |
| `scale` missing or `scale` is 100 | `level` when it is from 0 through 100, otherwise `None` |
| `scale` ≤ 0 | `None` |
| Any other positive `scale` | `round(level * 100 / scale)` when that result is from 0 through 100, otherwise `None` |

## Checks (`runner.run_checks`)

Order is fixed: reachable, version, battery, installation, launch. Each result passes through `_with_log` before the next check starts.

### Device is reachable

`adb shell echo hello`. `PASS` only when `returncode == 0` and stripped stdout is exactly `hello`. Timeout explanation: `Command 'echo hello' timed out.` Any other `AdbError`, non-zero exit, or different stdout: `Command 'echo hello' did not return 'hello'.`

### Android version

Uses `DeviceInfo` only. No extra ADB call.

| `android_major` | Status | Explanation |
| --- | --- | --- |
| `None`, version string present | `FAIL` | Version could not be parsed, value shown with `!r`. |
| `None`, version missing | `FAIL` | `Android version could not be read.` |
| `>= minimum` | `PASS` | Major is greater than or equal to the minimum. |
| `< minimum` | `FAIL` | Major is lower than the minimum. |

### Battery level

Uses `DeviceInfo.battery_percent`. Comparison is strict.

| Level | Status |
| --- | --- |
| `None` | `FAIL`, could not be read |
| `> minimum` | `PASS` |
| `== minimum` | `FAIL`, equal and must be above it |
| `< minimum` | `FAIL`, lower than the minimum |

### Application installation

`adb shell pm path {package_name}`. `PASS` when exit code is 0 and some stdout line, after `strip`, starts with `package:`. Otherwise `FAIL` and `{package} is not installed`, with `install_cause` `InstallCause.MISSING`, unless the call timed out (`installation check ... timed out`) or raised a non-timeout `AdbError` (`installation check ... failed`). Those two failures set `install_cause` to `InstallCause.UNCONFIRMED`.

### Launch application

If installation is `FAIL`:

- `install_cause` is `InstallCause.MISSING` → `SKIP`, `Skipped because {name} is not installed.`
- Any other installation failure, including a missing `install_cause` → `SKIP`, `Skipped because installation of {name} could not be confirmed.`

`monkey` is not called in either skip case.

Otherwise `launch_package` runs:

```text
adb -s {serial} shell monkey -p {package} -c android.intent.category.LAUNCHER 1
```

Stdout and stderr are joined with a newline. `PASS` when exit code is 0 and that text contains neither `No activities found` nor `monkey aborted`. Those two substrings produce their own fail sentences. Any other non-zero or unexpected result is `Launch of {package} failed.` Timeout and other `AdbError` values have their own sentences.

### Failure logs

`_with_log` returns a `PASS` or `SKIP` result unchanged.

On `FAIL`:

1. `logs_dir.mkdir(parents=True, exist_ok=True)`.
2. `adb logcat -d -t 2000` (`LOGCAT_MAX_LINES`). Non-zero exit raises `AdbError`.
3. `_bounded_log` keeps the text when its UTF-8 size is at most 1_048_576 bytes. A larger dump keeps the last 1_048_576 bytes, decoded with `errors="ignore"`, then drops a leading partial line when a newline exists and the remainder is non-empty. The note is `Log truncated to the most recent 1 MB.`
4. Write `logs/{%Y%m%d-%H%M%S}_{slug}.log` as UTF-8. The slug is the check name lowercased with spaces replaced by `_`.

`AdbError` or `OSError` sets `log_error` to `Log capture failed` and leaves `log_path` unset. If the path was chosen and the write failed, the partial file is removed with `missing_ok=True`. A failure of that removal is ignored. The check stays `FAIL`.

The timestamp is local `datetime.now()` at write time. Two failures in the same second get different slugs, so the names still differ.

## Report (`report.py`)

`render_report` always ends with a newline. Layout:

```text
Android Automation Report
=========================

Device
------
Device ID: {serial}
Android version: {version or unknown}
Model: {model or unknown}
Manufacturer: {manufacturer or unknown}
Battery: {n% or unknown}

Checks
------
{name}: {PASS|FAIL|SKIP}
    {explanation}
    Log: {posix path}          # only when log_path is set
    {log_note}                 # only when set
    {log_error}                # only when set

Totals
------
Passed: {n}
Failed: {n}
Skipped: {n}
All: {n}

Overall: {FAIL if Failed > 0 else PASS}
```

Empty string and `None` both render as `unknown` for text fields.

`write_report` creates parent directories, then overwrites the file as UTF-8. `IsADirectoryError` and `PermissionError` have dedicated sentences. Any other `OSError` includes `strerror` or the exception class name.

## Tests

pytest, with `subprocess.run` mocked. No device and no `adb` binary. Cases cover configuration, device selection, a passing run, a failed check, log capture, the report text, and exit codes 0, 1, and 2. GitHub Actions installs from `uv.lock` and runs that suite on Ubuntu with Python 3.12. It does not install platform-tools. Pushes to `main` and pull requests each run the job once.
