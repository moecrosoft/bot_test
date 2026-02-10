import os
import json
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TZ = ZoneInfo("Asia/Singapore")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")

# Paste your local token.json content into this secret
GOOGLE_TOKEN_JSON = os.getenv("GOOGLE_TOKEN_JSON")

TOKEN_FILE = "token.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

# Prevent double-send in the same day (useful if the workflow reruns)
SENT_FILE = "sent_daily.json"


def ensure_token_file():
    if os.path.exists(TOKEN_FILE):
        return
    if not GOOGLE_TOKEN_JSON:
        raise RuntimeError("Missing GOOGLE_TOKEN_JSON env var.")
    # Validate it's JSON
    json.loads(GOOGLE_TOKEN_JSON)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(GOOGLE_TOKEN_JSON)


def load_sent_days():
    try:
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_sent_days(days: set[str]):
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(days)), f)


def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": GROUP_CHAT_ID, "text": text}
    r = requests.post(url, json=payload, timeout=20)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram error: {data}")
    return data


def get_calendar_service():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    return build("calendar", "v3", credentials=creds)


def run_once():
    if not TELEGRAM_TOKEN or not GROUP_CHAT_ID:
        raise RuntimeError("Set TELEGRAM_TOKEN and GROUP_CHAT_ID env vars.")

    ensure_token_file()
    service = get_calendar_service()

    # Keyed by Singapore date so we only send once per day
    today_key = datetime.now(TZ).date().isoformat()
    sent_days = load_sent_days()
    if today_key in sent_days:
        print("Already sent today's digest. Exiting.")
        return

    # Tomorrow: 00:00 to next day 00:00 in SG time
    tomorrow = datetime.now(TZ).date() + timedelta(days=1)
    start_dt = datetime.combine(tomorrow, dtime(0, 0), tzinfo=TZ)
    end_dt = start_dt + timedelta(days=1)

    events_result = service.events().list(
        calendarId="primary",
        timeMin=start_dt.isoformat(),
        timeMax=end_dt.isoformat(),
        singleEvents=True,     # expands recurring events
        orderBy="startTime",
        maxResults=200,
    ).execute()

    events = events_result.get("items", [])

    # Build message
    if not events:
        msg = f"📅 Tomorrow ({tomorrow.strftime('%a %d %b')}): No events."
    else:
        lines = [f"📅 Tomorrow ({tomorrow.strftime('%a %d %b')}):"]
        for ev in events:
            title = ev.get("summary", "(No title)")
            s = ev.get("start", {})
            if "dateTime" in s:
                st = datetime.fromisoformat(s["dateTime"]).astimezone(TZ)
                tstr = st.strftime("%I:%M %p")
            else:
                # All-day event
                tstr = "All day"
            lines.append(f"- {tstr} — {title}")
        msg = "\n".join(lines)

    send_telegram(msg)

    # Mark as sent today
    sent_days.add(today_key)
    save_sent_days(sent_days)
    print("Sent tomorrow digest.")


if __name__ == "__main__":
    run_once()
