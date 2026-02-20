import hashlib
import random
import os
import re
import time
import urllib3
import json
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from colorama import init, Fore, Style

init(autoreset=True)
col_g = Fore.GREEN
col_gb = Style.BRIGHT + Fore.GREEN
col_b = Fore.BLUE
col_bb = Style.BRIGHT + Fore.BLUE
col_y = Fore.YELLOW
col_yb = Style.BRIGHT + Fore.YELLOW
col_r = Fore.RED
col_rb = Style.BRIGHT + Fore.RED

# =========================
# USER OVERRIDES (per request)
# =========================
# 1) Do NOT try to retrieve China/Beijing time via NTP.
# 1) Do NOT try to retrieve China/Beijing time via NTP. We use a configurable target time instead.
#    That means Beijing time is assumed to be system_time + 6 hours.

# 2) Cookie is read from config.txt (COOKIE_VALUE)


# =========================
# CONFIG (in-repo text file)
# =========================
# The repo contains a text file 'config.txt' that controls:
# - SIMULATION: 0/1
# - SIM_MINUTE: 0-59 (only used when SIMULATION=1)
# - COOKIE_VALUE: value for cookie 'new_bbs_serviceToken'
# - START_BEFORE_MINUTES: how many minutes before "China midnight" to start sending requests
# - INTERVAL_SECONDS: time between request launches (parallel scheduler)
#
# File format (key=value, one per line; # comments allowed):
#   SIMULATION=0
#   SIM_MINUTE=0
#   COOKIE_VALUE=...
#   START_BEFORE_MINUTES=60
#   INTERVAL_SECONDS=1.0
#
CONFIG_FILE = "config.txt"

# Feedtime/phase-shift in milliseconds (original script default ~1400ms).
# Used to offset the send moment slightly earlier than the target.
FEEDTIME_MS = 1400.0

def read_config(path: str = CONFIG_FILE) -> dict:
    cfg = {
        "SIMULATION": False,
        # Simulation: treat a point in the near future as "China midnight".
        # The simulated midnight will be now + SIM_MIDNIGHT_AFTER_SECONDS.
        "SIM_MIDNIGHT_AFTER_SECONDS": 120.0,

        "COOKIE_VALUE": "",

        # Target "China midnight" reference time expressed in Israel local time (Asia/Jerusalem).
        # Example: 18:00 means "when it's 18:00 in Israel, treat that as China midnight".
        "TARGET_ISRAEL_TIME": "18:00",

        # Start sending requests this many seconds before the target time.
        "SEND_START_BEFORE_SECONDS": 3600.0,

        # Parallel launch cadence.
        "INTERVAL_SECONDS": 1.0,

        # How long to keep launching requests in the main phase before stopping.
        "RUN_LIMIT_SECONDS": 3.0,

        # Milliseconds to subtract as phase shift.
        "FEEDTIME_MS": 1400.0,

        # Backward-compat (old keys):
        "SIM_MINUTE": 0,
        "START_BEFORE_MINUTES": 60,
    }
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = [x.strip() for x in line.split("=", 1)]
                k = k.upper()
                if k == "SIMULATION":
                    cfg["SIMULATION"] = v.strip() == "1"
                elif k == "SIM_MIDNIGHT_AFTER_SECONDS":
                    try:
                        cfg["SIM_MIDNIGHT_AFTER_SECONDS"] = float(v)
                    except ValueError:
                        pass
                elif k == "SEND_START_BEFORE_SECONDS":
                    try:
                        cfg["SEND_START_BEFORE_SECONDS"] = float(v)
                    except ValueError:
                        pass
                elif k == "SIM_MINUTE":  # backward-compat
                    try:
                        cfg["SIM_MINUTE"] = int(v)
                    except ValueError:
                        pass
                elif k == "START_BEFORE_MINUTES":  # backward-compat
                    try:
                        cfg["START_BEFORE_MINUTES"] = int(float(v))
                    except ValueError:
                        pass
                elif k == "COOKIE_VALUE":
                    cfg["COOKIE_VALUE"] = v
                elif k == "TARGET_ISRAEL_TIME":
                    cfg["TARGET_ISRAEL_TIME"] = v
                elif k == "INTERVAL_SECONDS":
                    try:
                        cfg["INTERVAL_SECONDS"] = float(v)
                    except ValueError:
                        pass
                elif k == "FEEDTIME_MS":
                    try:
                        cfg["FEEDTIME_MS"] = float(v)
                    except ValueError:
                        pass
                elif k == "RUN_LIMIT_SECONDS":
                    try:
                        cfg["RUN_LIMIT_SECONDS"] = float(v)
                    except ValueError:
                        pass
    except FileNotFoundError:
        pass

    # normalize & clamp (seconds-based)
    # Backward-compat handling:
    # - If the user only provided START_BEFORE_MINUTES, convert it to seconds.
    sb_minutes = cfg.get("START_BEFORE_MINUTES", 60)
    try:
        sb_minutes = int(sb_minutes)
    except Exception:
        sb_minutes = 60
    if sb_minutes < 0:
        sb_minutes = 0
    if sb_minutes > 24 * 60:
        sb_minutes = 24 * 60
    cfg["START_BEFORE_MINUTES"] = int(sb_minutes)

    send_before = cfg.get("SEND_START_BEFORE_SECONDS", 3600.0)
    try:
        send_before = float(send_before)
    except Exception:
        send_before = 3600.0
    # If SEND_START_BEFORE_SECONDS was left at default but START_BEFORE_MINUTES was changed, honor the minutes value.
    if abs(send_before - 3600.0) < 1e-9 and sb_minutes != 60:
        send_before = float(sb_minutes) * 60.0
    if send_before < 0:
        send_before = 0.0
    if send_before > 24 * 3600:
        send_before = float(24 * 3600)
    cfg["SEND_START_BEFORE_SECONDS"] = float(send_before)

    # Simulation target: by default, simulated midnight is now + SIM_MIDNIGHT_AFTER_SECONDS.
    sim_after = cfg.get("SIM_MIDNIGHT_AFTER_SECONDS", 120.0)
    try:
        sim_after = float(sim_after)
    except Exception:
        sim_after = 120.0
    if sim_after < 1.0:
        sim_after = 1.0
    if sim_after > 24 * 3600:
        sim_after = float(24 * 3600)
    cfg["SIM_MIDNIGHT_AFTER_SECONDS"] = float(sim_after)

    # Keep SIM_MINUTE clamped for backward-compat.
    m_ = cfg.get("SIM_MINUTE", 0)
    try:
        m_ = int(m_)
    except Exception:
        m_ = 0
    cfg["SIM_MINUTE"] = 0 if m_ < 0 else 59 if m_ > 59 else m_

    itv = cfg.get("INTERVAL_SECONDS", 1.0)
    try:
        itv = float(itv)
    except Exception:
        itv = 1.0
    if itv <= 0:
        itv = 1.0
    if itv < 0.05:
        itv = 0.05
    cfg["INTERVAL_SECONDS"] = float(itv)

    rl = cfg.get("RUN_LIMIT_SECONDS", 3.0)
    try:
        rl = float(rl)
    except Exception:
        rl = 3.0
    if rl <= 0:
        rl = 3.0
    if rl > 600:
        rl = 600.0
    cfg["RUN_LIMIT_SECONDS"] = float(rl)

    # Target time in Israel local time (HH:MM or HH:MM:SS)
    tstr = str(cfg.get("TARGET_ISRAEL_TIME") or "18:00").strip()
    m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", tstr)
    if not m:
        tstr = "18:00"
        m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", tstr)
    hh = int(m.group(1))
    mm = int(m.group(2))
    ss = int(m.group(3) or 0)
    # clamp
    hh = 0 if hh < 0 else 23 if hh > 23 else hh
    mm = 0 if mm < 0 else 59 if mm > 59 else mm
    ss = 0 if ss < 0 else 59 if ss > 59 else ss
    cfg["TARGET_ISRAEL_TIME"] = f"{hh:02d}:{mm:02d}:{ss:02d}"

    return cfg


def github_event_name() -> str:
    # In GitHub Actions this will be set (e.g., 'schedule', 'push', 'workflow_dispatch').
    return os.environ.get("GITHUB_EVENT_NAME", "").strip().lower()


def should_exit_for_push_when_not_simulating(sim_enabled: bool, target_israel_time_hms: str) -> bool:
    """ 
    If triggered by a code change (push) and SIMULATION is OFF, we normally exit to avoid
    unintended real requests.

    **Exception (requested):** if the push happens within 1.5 hours *before* the assumed
    China midnight, behave like a scheduled run (keep running and wait for the send window).
    """
    ev = github_event_name()
    if sim_enabled:
        return False
    if ev != "push":
        return False

    # Allow push-triggered runs close to midnight (<= 1.5h before midnight).
    try:
        PUSH_GRACE_WINDOW_SECONDS = 90 * 60  # 1.5 hours
        now_utc = datetime.utcnow()
        midnight_utc = target_utc_from_israel_time(now_utc, target_israel_time_hms)
        seconds_to_midnight = (midnight_utc - now_utc).total_seconds()
        if 0 <= seconds_to_midnight <= PUSH_GRACE_WINDOW_SECONDS:
            print(
                col_y
                + f"[Info] Push-triggered run within {PUSH_GRACE_WINDOW_SECONDS:.0f}s of assumed China midnight -> continuing as scheduled run."
                + Fore.RESET
            )
            return False
    except Exception:
        # If we can't compute safely, fail closed (exit).
        return True

    return True


feedtime = float(1400)  # ms
feed_time_shift = feedtime
feed_time_shift_s = feed_time_shift / 1000.0


def generate_device_id():
    random_data = f"{random.random()}-{time.time()}"
    device_id = hashlib.sha1(random_data.encode("utf-8")).hexdigest().upper()
    return device_id


def israel_now_from_utc(utc_dt: datetime) -> datetime:
    """Convert a naive UTC datetime into a naive Israel-local datetime (Asia/Jerusalem) for logging."""
    tz_il = ZoneInfo("Asia/Jerusalem")
    return utc_dt.replace(tzinfo=timezone.utc).astimezone(tz_il).replace(tzinfo=None)


def target_utc_from_israel_time(now_utc: datetime, israel_time_hms: str) -> datetime:
    """Return the next UTC datetime corresponding to a given Israel local time (Asia/Jerusalem).

    This lets us treat a configurable Israel clock time (e.g., 18:00) as the reference 'China midnight'
    moment, regardless of where the code runs (GitHub runners are typically UTC).
    """
    tz_il = ZoneInfo("Asia/Jerusalem")
    # Ensure aware UTC datetime
    now_utc_aware = now_utc.replace(tzinfo=timezone.utc)
    now_il = now_utc_aware.astimezone(tz_il)

    m = re.match(r"^(\d{2}):(\d{2}):(\d{2})$", israel_time_hms.strip())
    if not m:
        israel_time_hms = "18:00:00"
        m = re.match(r"^(\d{2}):(\d{2}):(\d{2})$", israel_time_hms)
    hh, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3))

    target_il = now_il.replace(hour=hh, minute=mm, second=ss, microsecond=0)
    if now_il >= target_il:
        target_il += timedelta(days=1)

    target_utc = target_il.astimezone(timezone.utc).replace(tzinfo=None)
    return target_utc


def system_target_time_for_simulated_beijing_midnight(sim_minute: int) -> datetime:
    """Return the next SYSTEM datetime where minutes == sim_minute (seconds=0), used as simulated 'China midnight'."""
    now_sys = datetime.now()
    candidate = now_sys.replace(second=0, microsecond=0, minute=sim_minute)
    if candidate <= now_sys:
        candidate += timedelta(hours=1)
    return candidate




def wait_until_target_time(
    sim_enabled: bool,
    sim_midnight_after_seconds: float,
    sim_minute_backward: int,
    send_start_before_seconds: float,
    target_israel_time_hms: str,
):
    """
    Wait until the moment when we should START SENDING the main requests.

    Timing model (portable across machines/runner timezones):

    - Normal mode (SIMULATION=0):
        Compute the next target time in UTC derived from TARGET_ISRAEL_TIME (Asia/Jerusalem).
        We wait until: (target_utc - SEND_START_BEFORE_SECONDS - phase_shift).

    - Simulation mode (SIMULATION=1, seconds-based):
        Treat (now + SIM_MIDNIGHT_AFTER_SECONDS) as "China midnight" and wait until:
        (sim_midnight - SEND_START_BEFORE_SECONDS - phase_shift).

    Backward-compat (legacy):
        If SIM_MIDNIGHT_AFTER_SECONDS is not provided but SIM_MINUTE is present, we emulate
        a "midnight" at the next local time where minute == SIM_MINUTE (mostly for older setups).
    """
    feed_time_shift_s = FEEDTIME_MS / 1000.0

    # --- Simulation mode (seconds-based) ---
    if sim_enabled:
        now_sys = datetime.now()

        # Legacy fallback: if user didn't set SIM_MIDNIGHT_AFTER_SECONDS meaningfully
        # but provided SIM_MINUTE, simulate midnight at the next time where minute==SIM_MINUTE.
        use_legacy = (sim_midnight_after_seconds is None) or (float(sim_midnight_after_seconds) <= 0 and sim_minute_backward is not None)
        if use_legacy:
            target_minute = int(sim_minute_backward) % 60
            # next occurrence in local time where minute matches
            base = now_sys.replace(second=0, microsecond=0)
            candidate = base.replace(minute=target_minute)
            if candidate <= base:
                candidate += timedelta(hours=1)
            midnight_sys = candidate  # treat this as "midnight"
            target_label = f"SIM midnight@*: {target_minute:02d} (legacy)"
        else:
            midnight_sys = (now_sys + timedelta(seconds=float(sim_midnight_after_seconds))).replace(microsecond=0)
            target_label = f"SIM midnight in +{float(sim_midnight_after_seconds):.3f}s"

        target_sys = midnight_sys - timedelta(seconds=float(send_start_before_seconds)) - timedelta(seconds=feed_time_shift_s)

        print(col_g + "[Phase Shift Established]: " + Fore.RESET + f"{FEEDTIME_MS:.2f} ms.")
        print(col_g + "[Start Before]: " + Fore.RESET + f"{float(send_start_before_seconds):.3f} second(s) before midnight.")
        print(
            col_g + "[Waiting until ... 🥱]: " + Fore.RESET +
            f"{target_sys.strftime('%Y-%m-%d %H:%M:%S.%f')} (SYSTEM)"
        )
        print(col_y + f"[Target]: {target_label}  (midnight_sys={midnight_sys.strftime('%Y-%m-%d %H:%M:%S')})" + Fore.RESET)
        print("Do not exit")

        while True:
            now_sys = datetime.now()
            time_diff = (target_sys - now_sys).total_seconds()
            if time_diff <= 0:
                break
            time.sleep(min(0.25, max(0.01, time_diff)))

        print(f"It's time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')} (SYSTEM). Starting requests")
        return

    # --- Normal mode: real China midnight using UTC ---
    now_utc = datetime.utcnow()
    midnight_utc = target_utc_from_israel_time(now_utc, target_israel_time_hms)
    target_utc = midnight_utc - timedelta(seconds=float(send_start_before_seconds)) - timedelta(seconds=feed_time_shift_s)

    # Estimate system offset from UTC for readable logs (works for GitHub runners too).
    utc_offset = datetime.now() - datetime.utcnow()
    target_sys_est = target_utc + utc_offset
    midnight_sys_est = midnight_utc + utc_offset

    print(col_g + "[Phase Shift Established]: " + Fore.RESET + f"{FEEDTIME_MS:.2f} ms.")
    print(col_g + "[Start Before]: " + Fore.RESET + f"{float(send_start_before_seconds):.3f} second(s) before midnight.")
    print(
        col_g + "[Waiting until ... 🥱]: " + Fore.RESET +
        f"{target_utc.strftime('%Y-%m-%d %H:%M:%S.%f')} (UTC) "
        f"== {target_sys_est.strftime('%Y-%m-%d %H:%M:%S.%f')} (SYSTEM est)"
    )
    print(col_y + f"[Target]: Target time (UTC)={midnight_utc.strftime('%Y-%m-%d %H:%M:%S')} (SYSTEM est={midnight_sys_est.strftime('%Y-%m-%d %H:%M:%S')})" + Fore.RESET)
    print("Do not exit")

    while True:
        now_utc = datetime.utcnow()
        time_diff = (target_utc - now_utc).total_seconds()
        if time_diff <= 0:
            break
        time.sleep(min(0.25, max(0.01, time_diff)))

    print(f"It's time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')} (SYSTEM). Starting requests")
    return
def check_unlock_status(session, cookie_value, device_id):
    try:
        url = "https://sgp-api.buy.mi.com/bbs/api/global/user/bl-switch/state"
        headers = {
            "Cookie": f"new_bbs_serviceToken={cookie_value};versionCode=500411;versionName=5.4.11;deviceId={device_id};"
        }

        response = session.make_request("GET", url, headers=headers)
        if response is None:
            print("[Error] It was not possible retrieve unlock status.")
            return False

        response_data = json.loads(response.data.decode("utf-8"))
        response.release_conn()
        # Note: The API sometimes returns code=100004 transiently (rate-limit / auth refresh / geo edge),
        # even when the cookie is still valid. Treat it as an auth warning and retry a few times before failing.
        if response_data.get("code") == 100004:
            msg = str(response_data.get("msg", "")).strip()
            print(col_r + f"[Auth Warning] code=100004 from state endpoint. msg={msg!r}. Retrying..." + Fore.RESET)
            for attempt in range(1, 4):
                time.sleep(attempt)  # 1s, 2s, 3s
                response2 = session.make_request("GET", url, headers=headers)
                if response2 is None:
                    continue
                try:
                    response_data2 = json.loads(response2.data.decode("utf-8"))
                except Exception:
                    response_data2 = {"_raw_text": response2.data.decode("utf-8", errors="replace")}
                finally:
                    try:
                        response2.release_conn()
                    except Exception:
                        pass

                if response_data2.get("code") != 100004:
                    response_data = response_data2
                    break

                msg2 = str(response_data2.get("msg", "")).strip()
                print(col_r + f"[Auth Warning] Still code=100004 (attempt {attempt}/3). msg={msg2!r}" + Fore.RESET)

            if response_data.get("code") == 100004:
                print(col_r + "[Error] Authentication failed (code=100004) after retries. "
                      "This can mean cookie invalid/blocked or the endpoint requires additional cookies." + Fore.RESET)
                print(col_r + f"[Debug] Last state response: {json.dumps(response_data, ensure_ascii=False)}" + Fore.RESET)
                return False

        data = response_data.get("data", {})
        is_pass = data.get("is_pass")
        button_state = data.get("button_state")
        deadline_format = data.get("deadline_format", "")

        if is_pass == 4:
            if button_state == 1:
                print(col_g + "[Account Status]: " + Fore.RESET + "the requests will be sent.")
                return True

            elif button_state == 2:
                print(
                    col_g + "[Account Satus]: " + Fore.RESET +
                    f"requests blocked untill {deadline_format} (Month/Day)."
                )
                status_2 = input(f"Continue ({col_b}Yes/No{Fore.RESET})?: ")
                if status_2.lower() in ("y", "yes"):
                    return True
                exit()

            elif button_state == 3:
                print(col_g + "[Account Status]: " + Fore.RESET + "Account created date lesser than 30 days")
                status_3 = input(f"Continue ({col_b}Yes/No{Fore.RESET})?: ")
                if status_3.lower() in ("y", "yes"):
                    return True
                exit()

        elif is_pass == 1:
            print(col_g + "[Account Status]: " + Fore.RESET + f"Request approved, unblock untill {deadline_format}.")
            input("Press Enter to close...")
            exit()

        print(col_g + "[Account Status]: " + Fore.RESET + "Unknown State.")
        exit()

    except Exception as e:
        print(f"[Error at status checking] {e}")
        return False


class HTTP11Session:
    def __init__(self):
        self.http = urllib3.PoolManager(
            maxsize=10,
            retries=True,
            timeout=urllib3.Timeout(connect=2.0, read=15.0),
            headers={},
        )

    def make_request(self, method, url, headers=None, body=None):
        try:
            request_headers = {}
            if headers:
                request_headers.update(headers)
                request_headers["Content-Type"] = "application/json; charset=utf-8"

            if method == "POST":
                if body is None:
                    body = '{"is_retry":true}'.encode("utf-8")
                request_headers["Content-Length"] = str(len(body))
                request_headers["Accept-Encoding"] = "gzip, deflate, br"
                request_headers["User-Agent"] = "okhttp/4.12.0"
                request_headers["Connection"] = "keep-alive"

            response = self.http.request(
                method,
                url,
                headers=request_headers,
                body=body,
                preload_content=False,
            )

            return response
        except Exception as e:
            print(f"[Network Error] {e}")
            return None



def send_bl_auth_request(session, cookie_value, device_id, req_id: int | None = None):
    """Send one POST to the bl-auth endpoint.

    Returns:
        (json_response, raw_text, request_time_utc, response_time_utc)
        - json_response: dict or None
        - raw_text: str or None
        - request_time_utc/response_time_utc: naive UTC datetime (for stable ordering/compare)
    """
    url = "https://sgp-api.buy.mi.com/bbs/api/global/apply/bl-auth"
    headers = {
        "Cookie": f"new_bbs_serviceToken={cookie_value};versionCode=500411;versionName=5.4.11;deviceId={device_id};"
    }

    rid = f"REQ {req_id}" if req_id is not None else "REQ ?"
    request_time_utc = datetime.utcnow()
    request_time_il = israel_now_from_utc(request_time_utc)
    print(
        col_g + f"[{rid}] [Request]: " + Fore.RESET +
        f"sent at {request_time_utc.strftime('%Y-%m-%d %H:%M:%S.%f')} (UTC) "
        f"== {request_time_il.strftime('%Y-%m-%d %H:%M:%S.%f')} (Israel)"
    )

    response = session.make_request("POST", url, headers=headers)
    if response is None:
        return None, None, request_time_utc, None

    response_time_utc = datetime.utcnow()
    response_time_il = israel_now_from_utc(response_time_utc)
    print(
        col_g + f"[{rid}] [Response]: " + Fore.RESET +
        f"recv at {response_time_utc.strftime('%Y-%m-%d %H:%M:%S.%f')} (UTC) "
        f"== {response_time_il.strftime('%Y-%m-%d %H:%M:%S.%f')} (Israel)"
    )

    try:
        response_data = response.data
        response.release_conn()
        raw_text = response_data.decode("utf-8", errors="replace")
        json_response = json.loads(raw_text)
        return json_response, raw_text, request_time_utc, response_time_utc
    except Exception as e:
        print(col_r + f"[{rid}] [Parse Error]: " + Fore.RESET + str(e))
        return None, None, request_time_utc, response_time_utc


def main():
    cfg_local = read_config()

    sim_enabled = bool(cfg_local.get("SIMULATION"))

    cookie_value = str(cfg_local.get("COOKIE_VALUE") or "").strip() or COOKIE_VALUE
    sim_midnight_after_seconds = float(cfg_local.get("SIM_MIDNIGHT_AFTER_SECONDS") or 120.0)
    send_start_before_seconds = float(cfg_local.get("SEND_START_BEFORE_SECONDS") or 3600.0)
    interval_seconds = float(cfg_local.get("INTERVAL_SECONDS") or INTERVAL_SECONDS)
    run_limit_seconds = float(cfg_local.get("RUN_LIMIT_SECONDS") or 3.0)

    global FEEDTIME_MS
    FEEDTIME_MS = float(cfg_local.get("FEEDTIME_MS") or FEEDTIME_MS)

    # Backward-compat (legacy simulation minute)
    sim_minute_legacy = int(cfg_local.get("SIM_MINUTE") or 0)

    ev = github_event_name()
    target_israel_time_hms = str(cfg_local.get("TARGET_ISRAEL_TIME") or "18:00:00")

    if should_exit_for_push_when_not_simulating(sim_enabled, target_israel_time_hms):
        print(col_y + "[Info] Triggered by GitHub 'push' event and SIMULATION is OFF -> exiting." + Fore.RESET)
        return

    if ev:
        print(col_b + f"[Info] GitHub event: {ev}" + Fore.RESET)
    print(
        col_b + f"[Info] Simulation: {'ON' if sim_enabled else 'OFF'}" + Fore.RESET
        + (f", SIM_MIDNIGHT_AFTER_SECONDS={sim_midnight_after_seconds}" if sim_enabled else "")
    )
    if not sim_enabled:
        print(col_b + f"[Info] Target (Israel time): {target_israel_time_hms} (Asia/Jerusalem)" + Fore.RESET)

    device_id = generate_device_id()
    session = HTTP11Session()
    # cookie_value loaded from config.txt (or fallback)
    if check_unlock_status(session, cookie_value, device_id):
        # ---------------------------
        # Preflight test (per request)
        # ---------------------------
        print(col_y + "\n[Preflight]: sending 5 test requests immediately (ignoring time)..." + Fore.RESET)
        for i in range(1, 6):
            print(col_bb + f"\n[Preflight {i}/5]" + Fore.RESET)
            json_response, raw_text, req_bj, resp_bj = send_bl_auth_request(session, cookie_value, device_id, req_id=i)
            if json_response is None:
                print(col_r + "[Preflight Result]: " + Fore.RESET + "No/invalid response (network or parse error).")
            else:
                # Print a compact summary plus the full JSON
                try:
                    code_val = json_response.get("code")
                    desc = json_response.get("desc") or json_response.get("message") or ""
                    print(col_g + "[Preflight Summary]: " + Fore.RESET + f"code={code_val} {desc}")
                except Exception:
                    pass
                print(col_y + "[Preflight JSON]: " + Fore.RESET + json.dumps(json_response, ensure_ascii=False))
            time.sleep(0.5)

        # Wait until the configured target time (from config.txt), minus phase shift.
        wait_until_target_time(sim_enabled, sim_midnight_after_seconds, sim_minute_legacy, send_start_before_seconds, target_israel_time_hms)

        url = "https://sgp-api.buy.mi.com/bbs/api/global/apply/bl-auth"
        headers = {
            "Cookie": f"new_bbs_serviceToken={cookie_value};versionCode=500411;versionName=5.4.11;deviceId={device_id};"
        }

        try:
            # --- Deadline flip tracker (to detect 'too early' vs 'after reset') ---
            request_counter = 0
            last_deadline = None
            last_deadline_last_req = {}
            boundary_printed = False

            # Hard stop: run the main request loop for at most 3 seconds.
            # (Per request: stop even if no deadline/date flip is observed.)
            loop_start = time.monotonic()

            # --- Concurrent request sending (per request) ---
            # Send one request every 1 second, without waiting for the previous response.
            # We keep a 3-second time limit for the whole main phase (already requested).
            lock = threading.Lock()

            def process_response(i: int, json_response: dict | None, req_bj: datetime | None, resp_bj: datetime | None, raw_text: str | None):
                nonlocal last_deadline, last_deadline_last_req, boundary_printed

                if json_response is None:
                    return

                try:
                    code_val = json_response.get("code")
                    data = json_response.get("data", {}) if isinstance(json_response, dict) else {}
                except Exception:
                    return

                # Only track the "try again at <deadline>" style responses
                if code_val == 0:
                    apply_result = data.get("apply_result")
                    if apply_result == 3:
                        deadline_format = data.get("deadline_format") or "Not declared"
                        with lock:
                            if last_deadline is None:
                                last_deadline = deadline_format
                            # If deadline changed, print boundary info (only once)
                            if (deadline_format != last_deadline) and (not boundary_printed):
                                print(col_yb + "\n[Boundary Detected]" + Fore.RESET + f" {last_deadline} -> {deadline_format}")

                                print(col_y + "[Last request for earlier deadline]:" + Fore.RESET + f" deadline={last_deadline}")
                                print(col_y + "  i=" + Fore.RESET + f"{last_deadline_last_req.get('i')}")
                                print(col_y + "  req_time=" + Fore.RESET + f"{last_deadline_last_req.get('req_time')}")
                                print(col_y + "  resp_time=" + Fore.RESET + f"{last_deadline_last_req.get('resp_time')}")
                                print(col_y + "  json=" + Fore.RESET + json.dumps(last_deadline_last_req.get('json'), ensure_ascii=False))

                                print(col_y + "[First request for later deadline]:" + Fore.RESET + f" deadline={deadline_format}")
                                print(col_y + "  i=" + Fore.RESET + f"{i}")
                                print(col_y + "  req_time=" + Fore.RESET + f"{req_bj}")
                                print(col_y + "  resp_time=" + Fore.RESET + f"{resp_bj}")
                                print(col_y + "  json=" + Fore.RESET + json.dumps(json_response, ensure_ascii=False))

                                boundary_printed = True

                            # Update last seen for current deadline
                            last_deadline = deadline_format
                            last_deadline_last_req = {
                                "i": i,
                                "req_time": str(req_bj),
                                "resp_time": str(resp_bj),
                                "json": json_response,
                            }

                        print(col_g + "[Status]: " + Fore.RESET + f"Quota/Retry until {deadline_format} (Month/Day).")
                        return

                    if apply_result == 1:
                        print(col_g + "[Status]: " + Fore.RESET + "Request approved, checking status")
                        check_unlock_status(session, cookie_value, device_id)
                        return
                # code=100004 may appear transiently; don't label it as "expired" immediately.
                if code_val == 100004:
                    msg = str(json_response.get("msg", "")).strip()
                    print(col_r + f"[Auth Warning] code=100004 from bl-auth. msg={msg!r}. "
                                  "Continuing (it may be transient)." + Fore.RESET)
                    print(col_r + f"[Debug] bl-auth response: {json.dumps(json_response, ensure_ascii=False)}" + Fore.RESET)
                    return

                # Print generic status for debugging
                print(col_g + "[Response]: " + Fore.RESET + json.dumps(json_response, ensure_ascii=False))

            def worker(i: int):
                print(col_bb + f"\n[Main Request #{i}]" + Fore.RESET)
                json_response, raw_text, req_bj, resp_bj = send_bl_auth_request(session, cookie_value, device_id, req_id=i)
                process_response(i, json_response, req_bj, resp_bj, raw_text)

            # Schedule sends: one per second, independent of response time
            threads: list[threading.Thread] = []
            request_counter = 0
            next_send = time.monotonic()

            while True:
                now = time.monotonic()
                elapsed = now - loop_start
                if elapsed >= run_limit_seconds:
                    print(col_yb + "\n[Time Limit]" + Fore.RESET + " 3 seconds elapsed -> stopping new sends.")
                    break

                if now >= next_send:
                    request_counter += 1
                    t = threading.Thread(target=worker, args=(request_counter,), daemon=True)
                    threads.append(t)
                    t.start()
                    next_send += interval_seconds
                else:
                    time.sleep(min(0.05, next_send - now))

            # Wait briefly for in-flight requests to finish so logs are complete
            join_deadline = time.monotonic() + 10.0
            for t in threads:
                remaining = join_deadline - time.monotonic()
                if remaining <= 0:
                    break
                t.join(timeout=remaining)

            if not boundary_printed and last_deadline is not None and last_deadline_last_req:
                print(col_y + "\n[No boundary detected within time limit]." + Fore.RESET)
                print(col_y + "[Last observed retry deadline]: " + Fore.RESET + f"deadline={last_deadline}, i={last_deadline_last_req.get('i')}")
                print(col_y + "  req_time=" + Fore.RESET + f"{last_deadline_last_req.get('req_time')}")
                print(col_y + "  resp_time=" + Fore.RESET + f"{last_deadline_last_req.get('resp_time')}")
                print(col_y + "  json=" + Fore.RESET + json.dumps(last_deadline_last_req.get('json'), ensure_ascii=False))

        except Exception as e:
            print(col_g + "[Request Error]: " + Fore.RESET + f"{e}")
            exit()


if __name__ == "__main__":
    main()