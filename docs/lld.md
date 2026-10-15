# Low-Level Design

Module behavior behind the [high-level design](hld.md). Names below match the code.

## Types

`AppConfig` (`config.py`) is frozen. Fields: `package_name: str`, `minimum_android_version: int`, `minimum_battery: int`, `minimum_free_storage_mb: int`, `device_id: str | None`, `interval_seconds: int | None`.

`DeviceRecord` is one `adb devices` row: `serial`, `state`.

`DeviceInfo` holds facts for the selected serial. `android_version`, `android_major`, `model`, `manufacturer`, and `battery_percent` are `None` when that read failed or timed out. `serial` is always set.

`CommandResult` is `returncode`, `stdout`, and `stderr` of one ADB invocation. A non-zero shell exit is returned to the caller. It is not an `AdbError`.

`CheckResult` is one check: `name`, `status` (`PASS`, `FAIL`, or `SKIP`), `explanation`, and optional `log_path`, `log_error`, `log_note`, and `install_cause`. `install_cause` is set only on a failed installation check.

`ConfigError`, `AdbError`, and `ReportError` each carry one sentence. `AdbError.timed_out` is true only when `subprocess` hits the time limit.

## `main.main`

`_parse_args` accepts `--config` (default `config.json`), `--report` (default `report.txt`), and `--logs` (default `logs`).

```text
load_config(args.config)          ConfigError → stderr, return 2
selected = device_id
loop:
  select_device(selected)         AdbError    → stderr, return 2
  read_device_info(...)
  run_checks(...)
  write_report(...)               ReportError → stderr, return 2
  print overall line and both paths
  code = 1 if any Status.FAIL else 0
  interval_seconds is None        return code
  selected = serial from this pass
  time.sleep(interval_seconds)    KeyboardInterrupt → stop
```

`SKIP` does not count as failed. The process entry point is `raise SystemExit(main())`.

`interval_seconds` absent or JSON `null` is one pass. A set value repeats from device selection. Sleep starts only after a pass that returned `0` or `1`. Both report paths are overwritten on each completed pass. Later passes pass the first serial into `select_device`, so a terminal is asked once per process.

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

Unknown and missing keys are reported together, in one sentence. Unknown names are sorted. Missing names stay in required order: `package_name`, `minimum_android_version`, `minimum_battery`, `minimum_free_storage_mb`.

`package_name` must be a string matching `^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$`. A value longer than 80 characters omits the received text from the message.

`minimum_android_version` is an integer from 1 through 99. `minimum_battery` is an integer from 0 through 100. `minimum_free_storage_mb` is an integer from 0 through 1048576. `bool` is rejected before the integer check, because `bool` is a subclass of `int` in Python. Floats are rejected.

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

`select_device(None)` and `select_device("")` select a ready device:

| Connected devices | Result |
| --- | --- |
| None | `No Android device or emulator is connected.` |
| Rows exist, none in state `device` | Lists `serial (state)` and a correction for the states present. |
| Exactly one `device` | That serial. |
| More than one `device`, stdin is not a terminal | Names the ready serials and tells the caller to set `device_id`. |
| More than one `device`, stdin is a terminal | Prints a numbered list of ready serials to stderr and reads one choice. |

`unauthorized` adds “accept the USB debugging prompt on the device”. `offline` adds “reconnect a device that is offline”. Any other state, or a set with no specific hint, adds the generic reconnect sentence. Two or more hints are joined with commas and a final `or`.

`_prompt_ready` lists only state `device`, in `adb devices` order. The choice may be a number from 1 through that count, or an exact serial. A token that is both a valid number and a serial is the number. Any other line prints `Enter a number from 1-N, or a serial from the list.` and asks again. EOF raises `AdbError`: `Several Android devices are ready and no choice was entered.`

The choice is used for that call only. A later `select_device(None)` asks again. `device_id` never prompts, including on a terminal.

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

Order is fixed: reachable, version, battery, storage, network, screen, installation, launch, foreground. Each result passes through `_run_one` before the next check starts.

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

### Free storage

`adb shell df -k /data`. Timeout: `The storage check timed out.` Any other `AdbError`: `The storage check failed.` A non-zero exit, or stdout with no `Available` integer immediately before a token that ends in `%`, is `Free storage could not be read.` The first such pair wins. Available kibibytes are divided by 1024 and truncated. Comparison with `minimum_free_storage_mb` is strict, with the same equal and lower sentences as battery, using `MB`.

### Network

`adb shell ip route show table all`. Timeout: `The network check timed out.` A non-zero exit or any other `AdbError`: `The network check failed.` Exit 0 with a line whose first field is `default`, ignoring `default dev dummy0`: `PASS`, `A default network route is present.` Exit 0 without such a line, including empty stdout and a table that contains only the `dummy0` placeholder: `FAIL`, `No default network route is present, so the device cannot reach other networks.`

### Screen state

`adb shell dumpsys power`. Timeout: `The screen check timed out.` Any other `AdbError`: `The screen check failed.` A non-zero exit is `Screen state could not be read.`

On exit 0, the first `mWakefulness=` value is used. `Awake` and `1` pass with `Screen state is Awake.` `Asleep` and `0`, `Dreaming` and `2`, and `Dozing` and `3` fail with `Screen state is {name}.` Any other value is `Screen state could not be read.`, and `mScreenOn` is not consulted. When no wakefulness line exists, `mScreenOn=true` is `Awake` and `mScreenOn=false` is `Asleep`. Neither line is `Screen state could not be read.`

### Application installation

`adb shell pm path {package_name}`. `PASS` when exit code is 0 and some stdout line, after `strip`, starts with `package:`. Otherwise `FAIL` and `{package} is not installed`, with `install_cause` `InstallCause.MISSING`, unless the call timed out (`installation check ... timed out`) or raised a non-timeout `AdbError` (`installation check ... failed`). Those two failures set `install_cause` to `InstallCause.UNCONFIRMED`.

### Launch application

`_install_skip` applies when installation is `FAIL`. Launch and foreground both use it.

- `install_cause` is `InstallCause.MISSING` → `SKIP`, `Skipped because {name} is not installed.`
- Any other installation failure, including a missing `install_cause` → `SKIP`, `Skipped because installation of {name} could not be confirmed.`

`monkey` is not called in either skip case. The foreground command is not called either.

Otherwise `launch_package` runs:

```text
adb -s {serial} shell monkey -p {package} -c android.intent.category.LAUNCHER 1
```

Stdout and stderr are joined with a newline. `PASS` when exit code is 0 and that text contains neither `No activities found` nor `monkey aborted`. Those two substrings produce their own fail sentences. Any other non-zero or unexpected result is `Launch of {package} failed.` Timeout and other `AdbError` values have their own sentences.

### Foreground activity

Uses `_install_skip` with the same sentences as launch. `dumpsys activity activities` is not called in that case.

Otherwise `adb shell dumpsys activity activities`. Timeout: `The foreground check for {package} timed out.` Any other `AdbError`: `The foreground check for {package} failed.` A non-zero exit, or no component, is `Foreground activity could not be read.`

The component is the first `topResumedActivity=` match of `package/class`. When that marker is absent, the first `mResumedActivity:` match is used. The class may contain `.`, `_`, and `$`. `PASS` when the package before `/` equals `package_name`: `Foreground activity is {component}.` Otherwise `FAIL`: `Foreground activity is {component}, not {package}.`

### Failure logs

`_run_one` calls `device_log_time`, runs the check, then `_with_log`. `PASS` and `SKIP` do not call `logcat`.

`device_log_time` runs `adb shell date +%m-%dT%H:%M:%S`. The `T` is replaced with a space. Stdout that then matches `MM-DD HH:MM:SS` is returned with `.000` appended. A non-zero exit, a timeout, an empty read, or any other text returns `None`. `logcat -c` is not used.

On `FAIL`:

1. When the clock was not read, `log_error` is `Log capture failed because the device clock could not be read.` and `logcat` is not called.
2. `logs_dir.mkdir(parents=True, exist_ok=True)`.
3. `adb logcat -d -t MM-DD HH:MM:SS.mmm`. The time is one argument. Empty or whitespace stdout is kept. A non-zero exit raises `AdbError`.
4. Installation, launch, and foreground pass `package_name` into `_filter_package_log`. A line that contains the package is kept. A line that contains `FATAL EXCEPTION` or `AndroidRuntime` starts a block: that line and the following lines are kept until the first empty line. The empty line is dropped. Other checks skip this filter.
5. `_bounded_log` keeps the text when its UTF-8 size is at most 1_048_576 bytes. A larger dump keeps the last 1_048_576 bytes, decoded with `errors="ignore"`, then drops a leading partial line when a newline exists and the remainder is non-empty. The note is `Log truncated to the most recent 1 MB.`
6. `_error_note` counts saved lines in threadtime form whose level is `E` or `F`. One line uses `1 error line in this check. First: TAG: message.` More than one uses `lines`. No such line leaves this note unset. The count does not change `status`.
7. Write `logs/{%Y%m%d-%H%M%S}_{slug}.log` as UTF-8. The slug is the check name lowercased with spaces replaced by `_`. `log_note` joins the truncation note and the error note with a space. Both unset leaves `log_note` unset.

`AdbError` sets `log_error` to `Log capture failed because logcat could not be read.` `OSError` sets `log_error` to `Log capture failed because the log file could not be written.` Either leaves `log_path` unset. If the path was chosen and the write failed, the partial file is removed with `missing_ok=True`. A failure of that removal is ignored. The check stays `FAIL`.

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

`render_json` is the same pass as JSON, including the trailing newline. Keys stay in this order:

```text
device.id
device.android_version
device.android_major
device.model
device.manufacturer
device.battery_percent
checks[]                name, status, explanation, log_path, log_note, log_error
totals.passed
totals.failed
totals.skipped
totals.all
overall                 FAIL if Failed > 0 else PASS
```

`install_cause` is not included. `log_path` is a POSIX path, or `null`. Unread device fields stay `null`. An empty string stays `""`. `android_major` is included even though the text report prints only the version string.

`json_report_path` replaces the text suffix with `.json`. `report.txt` is accompanied by `report.json`. A text path whose suffix is already `.json` uses `<name>.json.json`.

`write_report` creates parent directories, writes the text file, then writes the JSON file. Both are UTF-8. `IsADirectoryError` and `PermissionError` have dedicated sentences that name the path that failed. Any other `OSError` includes `strerror` or the exception class name. A failure while creating the parent names the text path.

## Tests

pytest, with `subprocess.run` mocked. No device and no `adb` binary. Cases cover configuration, device selection, a passing run, a failed check, log capture, the text report, the JSON report, and exit codes 0, 1, and 2. GitHub Actions installs from `uv.lock` and runs that suite on Ubuntu with Python 3.12. It does not install platform-tools. Pushes to `main` and pull requests each run the job once.
