# High-Level Design

Command-line checks for one Android device. One process reads `config.json`, talks to the device through the `adb` binary, runs nine checks, writes `report.txt` and `report.json`, and saves recent logcat lines when a check fails.

Python 3.12 or newer. The program uses the standard library only.

## Goals

- Validate configuration before any ADB call, and stop with a single sentence when the file is unusable.
- Select one ready device. Several ready devices prompt on a terminal. `device_id` skips the prompt. Without a terminal, several ready devices require `device_id`.
- Run all nine checks in a fixed order. A failed check does not cancel the rest.
- Repeat that pass when `interval_seconds` is set. Ctrl+C stops with the exit code of the last completed pass.
- Leave a text report and a JSON report that a person or a script can read, and a log file for each failed check.
- Return a stable exit code: `0` when nothing failed, `1` when a check failed, `2` when the run could not start or the report could not be written.

## Out of scope

- A background service, cron, or a graphical interface.
- Authorizing the computer, installing a package, granting permissions, turning the screen on, unlocking it, joining a network, or freeing storage.
- Checking more than one device in one run.

## Context

```text
config.json ──read──►  main.py
                          │
                          ├── android_runner.config   validate
                          ├── android_runner.adb      adb binary ──USB/TCP──► device
                          ├── android_runner.runner   nine checks
                          └── android_runner.report   report.txt
                                                       report.json
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
| `android_runner.runner` | Run the nine checks and attach a failure log. |
| `android_runner.report` | Render and write `report.txt` and the sibling JSON report. |

Dependencies point inward from `main.py`. `runner` uses `adb` and `config`. `report` uses the device and check types. Nothing imports `main`.

## Run sequence

1. Parse arguments. Defaults are `config.json`, `report.txt`, and `logs`.
2. Load configuration. `ConfigError` prints one sentence to stderr and exits `2`. No ADB call has happened.
3. Select a serial and read device info. `AdbError` prints one sentence to stderr and exits `2`. That pass does not write a report.
4. Run the nine checks. Version and battery reuse facts already collected. A failed installation skips launch and the foreground check, so `monkey` is not started and the resumed activity is not read.
5. Write the text report and the JSON report. `ReportError` prints one sentence to stderr and exits `2`.
6. Print `Connected device: {serial}`, including when that serial was the only ready device. Then print the local time with seconds, `Overall: PASS` or `Overall: FAIL`, and both report paths. Exit `1` when any check is `FAIL`. `SKIP` is not a failure, so exit `0`.
7. When `interval_seconds` is set and the pass returned `0` or `1`, sleep that many seconds and repeat from step 3. Later passes use the serial from the first pass, so a terminal prompt is not repeated. Each completed pass overwrites both report files. Ctrl+C prints `Stopped.` and returns the last completed code. Ctrl+C before any report prints `Stopped before a report was written.` and returns `2`. A later pass that cannot select a device or write a report stops the loop with exit `2` and leaves the previous report files in place.

Unread version, model, manufacturer, and battery stay empty on the device record and do not abort the run. The version and battery checks then fail with their own explanations.

## Checks

| Order | Check | Evidence |
| --- | --- | --- |
| 1 | Device is reachable | `adb shell echo hello` exits 0 and prints `hello`. |
| 2 | Android version | Major number of `ro.build.version.release` is at least the configured minimum. `8.1.0` counts as 8. |
| 3 | Battery level | Percent from `dumpsys battery` is strictly above the configured minimum. Equal fails. |
| 4 | Free storage | Available mebibytes from `df -k /data` are strictly above `minimum_free_storage_mb`. Equal fails. The kibibyte value is divided by 1024 and truncated. |
| 5 | Network | `ip route show table all` has a line whose first field is `default` and that does not contain `dummy0`. |
| 6 | Screen state | `dumpsys power` reports wakefulness `Awake`. When that line is absent, `mScreenOn=true` passes. The screen is not changed. |
| 7 | Application installation | `pm path <package>` exits 0 and a line starts with `package:`. |
| 8 | Launch application | `monkey` with category `android.intent.category.LAUNCHER` exits 0, and the output does not contain `No activities found` or `monkey aborted`. |
| 9 | Foreground activity | The first `topResumedActivity` package is the configured package. When that marker is absent, the first `mResumedActivity` is used. |

## External interfaces

**Command line.** `python main.py [--config PATH] [--report PATH] [--logs DIR]`.

**Configuration.** A JSON object. Required: `package_name` (Android package name), `minimum_android_version` (integer 1–99), `minimum_battery` (integer 0–100), `minimum_free_storage_mb` (integer 0–1048576). Optional: `device_id` (exact serial). Missing, `null`, and `""` mean the only ready device, or a terminal prompt when several are ready. Optional: `interval_seconds` (integer 1–86400). Missing and `null` mean one pass. Unknown keys, floats, and booleans are rejected.

**ADB.** Argument lists only. `shell=True` is not used. Each call waits at most 20 seconds. Commands used: `adb devices`, `getprop`, `dumpsys battery`, `echo`, `df -k /data`, `ip route show table all`, `dumpsys power`, `pm path`, `monkey`, `dumpsys activity activities`, `date +%m-%dT%H:%M:%S`, `logcat -d -t`. The logcat buffer is not cleared.

**Report.** UTF-8 text, overwritten on each successful run, plus a JSON file beside it. The JSON path is the text path with a `.json` suffix, so `report.txt` is accompanied by `report.json`. A `--report` path that already ends in `.json` keeps that path for the text file, and the JSON copy is `<name>.json.json`. Text sections: title (the connected serial), run time with seconds, device, checks, totals, overall. The JSON report includes the same local `ran_at` value. Unread device fields print as `unknown` in the text file and as JSON `null` in the JSON file. An empty string stays `""` in JSON. `log_path` is a POSIX path when a failure log was saved.

**Failure logs.** `logs/YYYYMMDD-HHMMSS_<check_name>.log`. Before each check the tool reads the device clock and, on `FAIL`, saves `logcat -d -t MM-DD HH:MM:SS.mmm` from that instant. An empty window is saved as returned. If the clock cannot be read, no log file is written and the report says the device clock could not be read. Installation, launch, and foreground logs then keep lines that contain the package name, and a `FATAL EXCEPTION` or `AndroidRuntime` block through the next blank line. Other checks keep the whole window. Threadtime `E` and `F` lines in the saved text add `N error lines in this check. First: ...` and do not change the check status. A dump still larger than 1 MB is cut to the most recent 1 MB. `PASS` and `SKIP` do not write a file. A failed capture leaves the check as `FAIL` and names the failed step: the device clock, logcat, or writing the log file.

## Exit codes

| Code | When | Report |
| --- | --- | --- |
| 0 | No check is `FAIL`. | Written |
| 1 | At least one check is `FAIL`. | Written |
| 2 | Bad configuration, missing `adb`, no selectable device, or either report file cannot be written. | Not written for that pass |

Exit `2` does not print a traceback.

## Constraints

- One process and one device. One pass, unless `interval_seconds` is set.
- No third-party runtime dependencies. Tests use pytest and mock `subprocess.run`.
- Device selection prompts only on a terminal, and only when `device_id` is unset and more than one device is ready. `select_device` does not store the choice. A continuous run reuses the first serial for later passes of that process.
- Launch success is the `monkey` result. A later check reads the resumed activity. Screen state is observed and is not changed.
- The saved log starts at the device clock reading taken before that check. Installation, launch, and foreground logs also keep the package and crash blocks. Other checks keep the whole window.
