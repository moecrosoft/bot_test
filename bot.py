import os
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ====== CONFIG (Render Env Vars) ======
TIMEZONE = ZoneInfo("Asia/Singapore")

REMIND_MINUTES = int(os.getenv("REMIND_MINUTES", "10"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")  # string is fine

# Google OAuth token content (paste token.json contents into this env var on Render)
GOOGLE_TOKEN_JSON = os.getenv("GOOGLE_TOKEN_JSON")

SENT_FILE = "sent.json"
TOKEN_FILE = "token.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def ensure_token_file():
    """
    Render doesn't have your local token.json unless you provide it.
    This function writes token.json from the GOOGLE_TOKEN_JSON env var.
    """
    if os.path.exists(TOKEN_FILE):
        return

    if not GOOGLE_TOKEN_JSON:
        raise RuntimeError(
            "Missing token.json. Set GOOGLE_TOKEN_JSON env var (paste token.json contents) "
            "or include token.json in the repo (not recommended)."
        )

    try:
        # Validate it is JSON
        json.loads(GOOGLE_TOKEN_JSON)
    except Exception as e:
        raise RuntimeError(f"GOOGLE_TOKEN_JSON is not valid JSON: {e}")

    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(GOOGLE_TOKEN_JSON)


def load_sent():
    if not os.path.exists(SENT_FILE):
        return set()
    try:
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_sent(sent_set: set[str]):
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(sent_set)), f)


def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": GROUP_CHAT_ID, "text": text}
    r = requests.post(url, json=payload, timeout=20)
    data = r.json()
    if not data.get("ok"):
        print("Telegram error:", data)
    return data


def get_calendar_service():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    return build("calendar", "v3", credentials=creds)


def main():
    # Required env vars
    if not TELEGRAM_TOKEN or not GROUP_CHAT_ID:
        raise RuntimeError(
            "Missing env vars. Set TELEGRAM_TOKEN and GROUP_CHAT_ID on Render."
        )

    # Ensure token file exists for Google Calendar API
    ensure_token_file()

    service = get_calendar_service()
    sent = load_sent()

    print(
        "✅ Bot running | remind:",
        REMIND_MINUTES,
        "min | poll:",
        POLL_SECONDS,
        "sec | tz:",
        TIMEZONE.key,
    )

    while True:
        now = datetime.now(TIMEZONE)
        time_min = now.isoformat()
        time_max = (now + timedelta(hours=24)).isoformat()

        try:
            events_result = (
                service.events()
                .list(
                    calendarId="primary",
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=50,
                )
                .execute()
            )
        except Exception as e:
            print("Google Calendar error:", e)
            time.sleep(POLL_SECONDS)
            continue

        events = events_result.get("items", [])

        for ev in events:
            ev_id = ev.get("id")
            title = ev.get("summary", "(No title)")

            start = ev.get("start", {})
            start_dt = start.get("dateTime") or start.get("date")
            if not start_dt:
                continue

            # Skip all-day events (they use "date", not "dateTime")
            if "dateTime" not in start:
                continue

            try:
                start_time = datetime.fromisoformat(start_dt).astimezone(TIMEZONE)
            except Exception:
                continue

            minutes_left = (start_time - now).total_seconds() / 60

            # Within the reminder window (0..REMIND_MINUTES)
            if 0 <= minutes_left <= REMIND_MINUTES:
                key = f"{ev_id}:{start_dt}:{REMIND_MINUTES}"
                if key in sent:
                    continue

                msg = (
                    f"⏰ Reminder: {title}\n"
                    f"Starts at {start_time.strftime('%I:%M %p')} "
                    f"(in {max(0, int(minutes_left))} min)"
                )
                send_telegram(msg)
                sent.add(key)
                save_sent(sent)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
