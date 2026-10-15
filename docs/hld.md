# High-Level Design

Command-line checks for one Android device. One process reads `config.json`, talks to the device through the `adb` binary, runs five checks, writes `report.txt`, and saves recent logcat lines when a check fails.

Python 3.12 or newer. The program uses the standard library only.

## Goals

- Validate configuration before any ADB call, and stop with a single sentence when the file is unusable.
- Select one ready device. Several ready devices prompt on a terminal. `device_id` skips the prompt. A non-interactive run still requires `device_id`.
- Run all five checks in a fixed order. A failed check does not cancel the rest.
- Repeat that pass when `interval_seconds` is set. Ctrl+C stops with the exit code of the last completed pass.
- Leave a text report that a person or a script can read, and a log file for each failed check.
- Return a stable exit code: `0` when nothing failed, `1` when a check failed, `2` when the run could not start or the report could not be written.

## Out of scope

- A background service, cron, or a graphical interface.
- Authorizing the computer, installing a package, granting permissions, or unlocking the screen.
- Checking more than one device in one run.
- Confirming that the launched activity is in the foreground.
- A JSON report, or a failure log limited to one package and one time window.

## Context

```text
config.json ──read──►  main.py
                          │
                          ├── android_runner.config   validate
                          ├── android_runner.adb      adb binary ──USB/TCP──► device
                          ├── android_runner.runner   five checks
                          └── android_runner.report   report.txt
                                                       logs/*.log
stderr  setup failures (exit 2), and Stopped. on Ctrl+C
stdout  one overall line after each completed pass
```

Paths are relative to the current working directory unless the caller passes absolute paths. The tool does not search for `adb` outside `PATH`.

## Modules

| Module | Responsibility |
| --- | --- |
| `main.py` | Parse `--config`, `--report`, and `--logs`. Own the exit code. |
| `android_runner.config` | Read and validate `config.json` into `AppConfig`. |
| `android_runner.adb` | Run ADB, select a serial, and collect device facts. |
| `android_runner.runner` | Run the five checks and attach a failure log. |
| `android_runner.report` | Render and write `report.txt`. |

Dependencies point inward from `main.py`. `runner` uses `adb` and `config`. `report` uses the device and check types. Nothing imports `main`.

## Run sequence

1. Parse arguments. Defaults are `config.json`, `report.txt`, and `logs`.
2. Load configuration. `ConfigError` prints one sentence to stderr and exits `2`. No ADB call has happened.
3. Select a serial and read device info. `AdbError` prints one sentence to stderr and exits `2`. That pass does not write a report.
4. Run the five checks. Version and battery reuse facts already collected. A failed installation skips launch, so `monkey` is not started.
5. Write the report. `ReportError` prints one sentence to stderr and exits `2`.
6. Print `Overall: PASS` or `Overall: FAIL` with the report path. Exit `1` when any check is `FAIL`. `SKIP` is not a failure, so exit `0`.
7. When `interval_seconds` is set and the pass returned `0` or `1`, sleep that many seconds and repeat from step 3. Later passes use the serial from the first pass, so a terminal prompt is not repeated. Each completed pass overwrites the report. Ctrl+C prints `Stopped.` and returns the last completed code. Ctrl+C before any report prints `Stopped before a report was written.` and returns `2`. A later pass that cannot select a device or write the report stops the loop with exit `2` and leaves the previous report in place.

A field that cannot be read (`android_version`, model, manufacturer, battery) stays empty on the device record. That does not abort the run. The matching check fails later with its own explanation.

## Checks

| Order | Check | Evidence |
| --- | --- | --- |
| 1 | Device is reachable | `adb shell echo hello` exits 0 and prints `hello`. |
| 2 | Android version | Major number of `ro.build.version.release` is at least the configured minimum. `8.1.0` counts as 8. |
| 3 | Battery level | Percent from `dumpsys battery` is strictly above the configured minimum. Equal fails. |
| 4 | Application installation | `pm path <package>` exits 0 and a line starts with `package:`. |
| 5 | Launch application | `monkey` with category `android.intent.category.LAUNCHER` exits 0, and the output does not contain `No activities found` or `monkey aborted`. |

## External interfaces

**Command line.** `python main.py [--config PATH] [--report PATH] [--logs DIR]`.

**Configuration.** A JSON object. Required: `package_name` (Android package name), `minimum_android_version` (integer 1–99), `minimum_battery` (integer 0–100). Optional: `device_id` (exact serial). Missing, `null`, and `""` mean the only ready device, or a terminal prompt when several are ready. Optional: `interval_seconds` (integer 1–86400). Missing and `null` mean one pass. Unknown keys, floats, and booleans are rejected.

**ADB.** Argument lists only. `shell=True` is not used. Each call waits at most 20 seconds. Commands used: `adb devices`, `getprop`, `dumpsys battery`, `echo`, `pm path`, `monkey`, `logcat -d -t 2000`.

**Report.** UTF-8 text, overwritten on each successful run. Sections: title, device, checks, totals, overall. Unread device fields print as `unknown`.

**Failure logs.** `logs/YYYYMMDD-HHMMSS_<check_name>.log`. The body is the most recent 2000 logcat lines, cut to the most recent 1 MB when that dump is still larger. `PASS` and `SKIP` do not write a file. A failed capture leaves the check as `FAIL` and adds `Log capture failed` to the report.

## Exit codes

| Code | When | Report |
| --- | --- | --- |
| 0 | No check is `FAIL`. | Written |
| 1 | At least one check is `FAIL`. | Written |
| 2 | Bad configuration, missing `adb`, no selectable device, or the report cannot be written. | Not written |

Exit `2` does not print a traceback.

## Constraints

- One process and one device. One pass, unless `interval_seconds` is set.
- No third-party runtime dependencies. Tests use pytest and mock `subprocess.run`.
- Device selection prompts only on a terminal, and only when `device_id` is unset and more than one device is ready. `select_device` does not store the choice. A continuous run reuses the first serial for later passes of that process.
- Launch success is the `monkey` result. The screen and the foreground app are not inspected.
- The saved log is recent device logcat. It is not filtered to the failing check or package.
