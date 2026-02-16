# Auto HyperOS Unlocker (GitHub Actions packaged)

This repository is packaged to run `hyperosunlocker.py` automatically on GitHub Actions **every day at 15:00 UTC**
(which is **23:00 China time, UTC+8**, i.e. one hour before midnight in China).

## Files
- `hyperosunlocker.py` — the script (cookie is embedded inside the code).
- `requirements.txt` — Python dependencies.
- `.github/workflows/run-daily.yml` — scheduled workflow.

## How to use
1. Create a new GitHub repository.
2. Upload/extract all files from this ZIP into the repo root (keeping the folder structure).
3. Commit & push.
4. In GitHub → Actions, enable workflows if prompted.
5. (Optional) Run manually via the **workflow_dispatch** button to test.

Notes:
- GitHub scheduled workflows are not guaranteed to start at an exact second; the script itself waits for midnight logic.
