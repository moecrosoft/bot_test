import os
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ===================== CONFIG =====================
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
SGT = ZoneInfo("Asia/Singapore")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")   # e.g. "-5256210631"

SENT_FILE = "sent.json"
# =================================================


# -------- Render support: write Google creds from env if provided --------
if os.getenv("CREDENTIALS_JSON") and not os.path.exists("credentials.json"):
    with open("credentials.json", "w") as f:
        f.write(os.getenv("CREDENTIALS_JSON"))

if os.getenv("TOKEN_JSON") and not os.path.exists("token.json"):
    with open("token.json", "w") as f:
        f.write(os.getenv("TOKEN_JSON"))
# -----------------------------------------------------------------------


def load_sent() -> set[str]:
    try:
        with open(SENT_FILE, "r") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_sent(sent: set[str]) -> None:
    with open(SENT_FILE, "w") as f:
        json.dump(sorted(list(sent)), f)


def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": GROUP_CHAT_ID, "text": text}
    r = requests.post(url, json=payload, timeout=20)
    data = r.json()
    if not data.get("ok"):
        print("Telegram error:", data)


def get_calendar_service():
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    return build("calendar", "v3", credentials=creds)


def format_event_message(ev: dict) -> str:
    title = ev.get("summary", "(No title)")
    desc = (ev.get("description") or "").strip()
    location = (ev.get("location") or "TBC").strip()

    start = ev.get("start", {})
    end = ev.get("end", {})

    # Handle all-day vs timed events
    start_dt_raw = start.get("dateTime")
    end_dt_raw = end.get("dateTime")

    if start_dt_raw:  # normal event with time
        start_dt = datetime.fromisoformat(start_dt_raw).astimezone(SGT)
        end_dt = datetime.fromisoformat(end_dt_raw).astimezone(SGT)

        date_str = start_dt.strftime("%d %B %Y")
        time_str = f"{start_dt.strftime('%I:%M%p')} - {end_dt.strftime('%I:%M%p')}"
    else:
        # All-day event
        date_only = datetime.fromisoformat(start.get("date")).date()
        date_str = date_only.strftime("%d %B %Y")
        time_str = "All day"

    # -------- YOUR REQUESTED FORMAT --------
    msg = (
        f"📢 Reminder: {title}\n\n"
        f"{desc}\n\n"
        f"🗓 Date: {date_str}\n"
        f"⏰ Time: {time_str}\n"
        f"📍 Venue: {location}\n\n"
        "See you all there 🔥"
    )
    # --------------------------------------

    return msg


def list_events_tomorrow(service) -> list[dict]:
    now = datetime.now(SGT)

    # Window = tomorrow
    start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=1))

    events_result = service.events().list(
        calendarId="primary",
        timeMin=start.astimezone(timezone.utc).isoformat(),
        timeMax=end.astimezone(timezone.utc).isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=50,
    ).execute()

    return events_result.get("items", [])


def run_daily_reminders():
    if not TELEGRAM_TOKEN or not GROUP_CHAT_ID:
        raise RuntimeError("Set TELEGRAM_TOKEN and GROUP_CHAT_ID in Render.")

    service = get_calendar_service()
    sent = load_sent()

    events = list_events_tomorrow(service)

    if not events:
        print("No events tomorrow — sending nothing.")
        return  # <-- IMPORTANT: sends NOTHING

    for ev in events:
        ev_id = ev.get("id", "")
        start = ev.get("start", {})
        start_key = start.get("dateTime") or start.get("date") or ""

        key = f"{ev_id}:{start_key}:T-1"

        if key in sent:
            continue  # already reminded

        msg = format_event_message(ev)
        send_telegram(msg)

        sent.add(key)

    save_sent(sent)
    print(f"Sent reminders for {len(events)} event(s) tomorrow.")


if __name__ == "__main__":
    run_daily_reminders()
