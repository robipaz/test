# Auto HyperOS Unlocker (GitHub Actions packaged)

This repository is packaged to run `hyperosunlocker.py` automatically on GitHub Actions **every day at 15:00 UTC**
(which is **23:00 China time, UTC+8**, i.e. one hour before midnight in China).

## Files
- `hyperosunlocker.py` — the script.
- `config.txt` — in-repo configuration (including cookie value and all timing knobs).
- `requirements.txt` — Python dependencies.
- `.github/workflows/run-daily.yml` — GitHub Actions workflow.

## Config (seconds-based)

Edit `config.txt`:

- `COOKIE_VALUE` — value for the `new_bbs_serviceToken` cookie.
- `SEND_START_BEFORE_SECONDS` — when to START launching main requests relative to "China midnight".
- `INTERVAL_SECONDS` — how often to launch a new request (parallel mode).
- `RUN_LIMIT_SECONDS` — how long to keep launching requests before stopping.
- `SIMULATION` — `on/off`. When `on`, the script ignores real midnight and uses a simulated one.
- `SIM_MIDNIGHT_AFTER_SECONDS` — only when `SIMULATION=1`: simulated midnight is `now + this many seconds`.

Notes:
- If `SIMULATION=0` and the workflow is triggered by a `push`, the script exits immediately (no requests).
- Normal mode keeps the earlier assumption: **China midnight == TARGET_ISRAEL_TIME (default 18:00) Israel time (Asia/Jerusalem)** (SYSTEM is the runner's time).

## Where to see output

GitHub repo → **Actions** → select a workflow run → **run** job → **Run script** step.
