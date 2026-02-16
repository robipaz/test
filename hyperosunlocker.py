import hashlib
import random
import time
import urllib3
import json
from datetime import datetime, timedelta

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
#    Assume: "China midnight (00:00 Beijing) == 18:00 system time".
#    That means Beijing time is assumed to be system_time + 6 hours.
BEIJING_MINUS_SYSTEM_HOURS = 6  # 18:00 system -> 00:00 Beijing

# 2) Do NOT try to fetch the cookie automatically (and do not ask for it).
#    Use a fixed cookie value.
COOKIE_VALUE = (
    "9CRa00QsgS3tiU9N%2BH054ReU0Pn7%2FwX9wzzhcYHaJiyTePDZL9bVcwdsXJqw5PQjkXoYHU0ELgzW%2BREN4rB%2BadDjXcwsRv02ts83imBsIMGI0fhSXjfbpgQC8Fu4Iw6uzqBAByQXVVzUqD3qTzJVwRwEApI9gHZ6RKovWQN7e%2Fo%3D"
)

print(col_y + "Using fixed cookie value for [new_bbs_serviceToken]." + Fore.RESET)

feedtime = float(1400)  # ms
feed_time_shift = feedtime
feed_time_shift_s = feed_time_shift / 1000.0


def generate_device_id():
    random_data = f"{random.random()}-{time.time()}"
    device_id = hashlib.sha1(random_data.encode("utf-8")).hexdigest().upper()
    return device_id


def beijing_now():
    """Return 'Beijing time' based on the user's assumption: Beijing = System + 6 hours."""
    return datetime.now() + timedelta(hours=BEIJING_MINUS_SYSTEM_HOURS)


def system_target_time_for_beijing_midnight():
    """
    Under the assumption: Beijing midnight == 18:00 system time.
    So the target (next Beijing midnight) is the next occurrence of 18:00:00 system time.
    """
    now_sys = datetime.now()
    target = now_sys.replace(hour=18, minute=0, second=0, microsecond=0)
    if now_sys >= target:
        target += timedelta(days=1)
    return target


def wait_until_target_time():
    print(col_y + "\nBootloader unlock request" + Fore.RESET)
    print(col_g + "[Phase Shift Established]: " + Fore.RESET + f"{feed_time_shift:.2f} ms.")

    # Start slightly BEFORE the assumed Beijing midnight (18:00 system) by feed_time_shift_s seconds
    target_sys = system_target_time_for_beijing_midnight() - timedelta(seconds=feed_time_shift_s)

    # For display, show both system target and the corresponding assumed Beijing time
    target_bj = target_sys + timedelta(hours=BEIJING_MINUS_SYSTEM_HOURS)

    print(
        col_g + "[Waiting until ... 🥱]: " + Fore.RESET +
        f"{target_sys.strftime('%Y-%m-%d %H:%M:%S.%f')} (SYSTEM) "
        f"== {target_bj.strftime('%Y-%m-%d %H:%M:%S.%f')} (ASSUMED UTC+8)"
    )
    print("Do not exit")

    while True:
        now_sys = datetime.now()
        time_diff = (target_sys - now_sys).total_seconds()

        if time_diff > 1:
            time.sleep(min(1.0, time_diff - 1))
        elif now_sys >= target_sys:
            print(
                f"It's time: {now_sys.strftime('%Y-%m-%d %H:%M:%S.%f')} (SYSTEM). Starting requests"
            )
            break
        else:
            time.sleep(0.0001)


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

        if response_data.get("code") == 100004:
            print("[Error] Expired Cookie ... try again.")
            exit()

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



def send_bl_auth_request(session, cookie_value, device_id):
    """Send one POST to the bl-auth endpoint.

    Returns:
        (json_response, raw_text, request_time_bj, response_time_bj)
        - json_response: dict or None
        - raw_text: str or None
        - request_time_bj/response_time_bj: datetime (ASSUMED_CN) or None
    """
    url = "https://sgp-api.buy.mi.com/bbs/api/global/apply/bl-auth"
    headers = {
        "Cookie": f"new_bbs_serviceToken={cookie_value};versionCode=500411;versionName=5.4.11;deviceId={device_id};"
    }

    request_time_bj = beijing_now()
    print(
        col_g + "[Request]: " + Fore.RESET +
        f"Request sent at {request_time_bj.strftime('%Y-%m-%d %H:%M:%S.%f')} (ASSUMED_CN)"
    )

    response = session.make_request("POST", url, headers=headers)
    if response is None:
        return None, None, request_time_bj, None

    response_time_bj = beijing_now()
    print(
        col_g + "[Response]: " + Fore.RESET +
        f"Received at {response_time_bj.strftime('%Y-%m-%d %H:%M:%S.%f')} (ASSUMED_CN)"
    )

    try:
        response_data = response.data
        response.release_conn()
        raw_text = response_data.decode("utf-8", errors="replace")
        json_response = json.loads(raw_text)
        return json_response, raw_text, request_time_bj, response_time_bj
    except Exception as e:
        print(col_r + "[Parse Error]: " + Fore.RESET + str(e))
        return None, None, request_time_bj, response_time_bj


def main():
    device_id = generate_device_id()
    session = HTTP11Session()
    cookie_value = COOKIE_VALUE

    if check_unlock_status(session, cookie_value, device_id):
        # ---------------------------
        # Preflight test (per request)
        # ---------------------------
        print(col_y + "\n[Preflight]: sending 5 test requests immediately (ignoring time)..." + Fore.RESET)
        for i in range(1, 6):
            print(col_bb + f"\n[Preflight {i}/5]" + Fore.RESET)
            json_response, raw_text, req_bj, resp_bj = send_bl_auth_request(session, cookie_value, device_id)
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

        # Wait until the assumed "Beijing midnight" moment (18:00 system time), minus phase shift.
        wait_until_target_time()

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

            while True:
                request_counter += 1
                print(col_bb + f"\n[Main Request #{request_counter}]" + Fore.RESET)
                json_response, _raw_text, req_bj, resp_bj = send_bl_auth_request(session, cookie_value, device_id)
                if json_response is None:
                    continue

                try:
                    code = json_response.get("code")
                    data = json_response.get("data", {})



                    if code == 0:
                        apply_result = data.get("apply_result")
                        if apply_result == 1:
                            print(col_g + "[Status]: " + Fore.RESET + "Request approved, checking status")
                            check_unlock_status(session, cookie_value, device_id)
                        elif apply_result == 3:
                            # Quota reached / asked to try again at the returned deadline.
                            deadline_format = data.get("deadline_format") or "Not declared"

                            # Track the moment the server's suggested retry-deadline flips to the next date.
                            # We want:
                            #   - last request that still returned the earlier deadline
                            #   - first request that returned the later deadline
                            current_deadline = str(deadline_format)

                            if last_deadline is None:
                                last_deadline = current_deadline
                                last_deadline_last_req = {
                                    "i": request_counter,
                                    "req_time": req_bj,
                                    "resp_time": resp_bj,
                                    "json": json_response,
                                }
                            elif current_deadline == last_deadline:
                                last_deadline_last_req = {
                                    "i": request_counter,
                                    "req_time": req_bj,
                                    "resp_time": resp_bj,
                                    "json": json_response,
                                }
                            else:
                                # Deadline changed (usually means we've crossed the server's day boundary).
                                if not boundary_printed:
                                    print(col_yb + "\n[Boundary Detected]" + Fore.RESET)
                                    print(col_y + "[Last request for earlier deadline]: " + Fore.RESET +
                                          f"deadline={last_deadline}, i={last_deadline_last_req.get('i')}")
                                    print(col_y + "  req_time=" + Fore.RESET + f"{last_deadline_last_req.get('req_time')}")
                                    print(col_y + "  resp_time=" + Fore.RESET + f"{last_deadline_last_req.get('resp_time')}")
                                    print(col_y + "  json=" + Fore.RESET + json.dumps(last_deadline_last_req.get('json'), ensure_ascii=False))

                                    print(col_y + "[First request for later deadline]: " + Fore.RESET +
                                          f"deadline={current_deadline}, i={request_counter}")
                                    print(col_y + "  req_time=" + Fore.RESET + f"{req_bj}")
                                    print(col_y + "  resp_time=" + Fore.RESET + f"{resp_bj}")
                                    print(col_y + "  json=" + Fore.RESET + json.dumps(json_response, ensure_ascii=False))
                                    boundary_printed = True

                                # Update tracking to new deadline so future flips can also be observed.
                                last_deadline = current_deadline
                                last_deadline_last_req = {
                                    "i": request_counter,
                                    "req_time": req_bj,
                                    "resp_time": resp_bj,
                                    "json": json_response,
                                }

                            print(
                                col_g + "[Status]: " + Fore.RESET +
                                f"Quota reached. Server suggests retry at {deadline_format} (Month/Day)."
                            )
                            # DO NOT exit; keep running so we can observe the deadline flip.
                            time.sleep(0.2)
                        elif apply_result == 4:

                            deadline_format = data.get("deadline_format", "Not declared")
                            print(
                                col_g + "[Status]: " + Fore.RESET +
                                f"Account blocked untill {deadline_format} (Month/Day)."
                            )
                            exit()

                    elif code == 100001:
                        print(col_g + "[Status]: " + Fore.RESET + "Request rejected")
                        print(col_g + "[Response]: " + Fore.RESET + f"{json_response}")

                    elif code == 100003:
                        print(col_g + "[Status]: " + Fore.RESET + "Maybe it was approved, check status")
                        print(col_g + "[Response]: " + Fore.RESET + f"{json_response}")
                        check_unlock_status(session, cookie_value, device_id)

                    elif code is not None:
                        print(col_g + "[Status]: " + Fore.RESET + f"Unknown status: {code}")
                        print(col_g + "[Response]: " + Fore.RESET + f"{json_response}")

                    else:
                        print(col_g + "[Error]: " + Fore.RESET + "Response without status code")
                        print(col_g + "[Response]: " + Fore.RESET + f"{json_response}")

                except json.JSONDecodeError:
                    print(col_g + "[Error]: " + Fore.RESET + "JSON decode error")
                    print(col_g + "[Server Response]: " + Fore.RESET + f"{_raw_text}")
                except Exception as e:
                    print(col_g + "[Error response processing]: " + Fore.RESET + f"{e}")
                    continue

        except Exception as e:
            print(col_g + "[Request Error]: " + Fore.RESET + f"{e}")
            exit()


if __name__ == "__main__":
    main()
