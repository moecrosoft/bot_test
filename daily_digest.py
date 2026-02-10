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

# Comma-separated list supported: "-5256210631,-1001234567890"
GROUP_CHAT_IDS_RAW = os.getenv("GROUP_CHAT_ID", "")
GROUP_CHAT_IDS = [x.strip() for x in GROUP_CHAT_IDS_RAW.split(",") if x.strip()]

SENT_FILE = "sent.json"
TOKEN_FILE = "token.json"
# =================================================


def ensure_token_file() -> None:
    """
    Creates token.json at runtime from env vars (for GitHub Actions / Render),
    so you never commit/push token.json.
    """
    if os.path.exists(TOKEN_FILE):
        return

    # Prefer GOOGLE_TOKEN_JSON (GitHub Secrets), fallback to TOKEN_JSON (older name)
    token_raw = os.getenv("GOOGLE_TOKEN_JSON") or os.getenv("TOKEN_JSON")
    if not token_raw:
        raise RuntimeError(
            "Missing GOOGLE_TOKEN_JSON (recommended) or TOKEN_JSON env var. "
            "Put your token.json contents into a GitHub Secret and pass it in."
        )

    # Validate JSON (catches copy/paste mistakes early)
    json.loads(token_raw)

    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token_raw)


def load_sent() -> set[str]:
    try:
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_sent(sent: set[str]) -> None:
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(sent)), f)


def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    for chat_id in GROUP_CHAT_IDS:
        payload = {"chat_id": chat_id, "text": text}
        r = requests.post(url, json=payload, timeout=20)
        data = r.json()
        if not data.get("ok"):
            print(f"Telegram error for {chat_id}:", data)


def get_calendar_service():
    ensure_token_file()
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    return build("calendar", "v3", credentials=creds)


def clean_text(s: str, max_len: int = 400) -> str:
    s = (s or "").strip()
    s = " ".join(s.split())  # collapse whitespace/newlines
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    return s


def format_event_message(ev: dict) -> str:
    title = ev.get("summary", "(No title)")
    desc = clean_text(ev.get("description", ""), 500)
    location = clean_text(ev.get("location", ""), 120) or "TBC"

    start = ev.get("start", {})
    end = ev.get("end", {})

    start_dt_raw = start.get("dateTime")
    end_dt_raw = end.get("dateTime")

    if start_dt_raw:  # timed event
        start_dt = datetime.fromisoformat(start_dt_raw).astimezone(SGT)
        end_dt = datetime.fromisoformat(end_dt_raw).astimezone(SGT) if end_dt_raw else None

        date_str = start_dt.strftime("%d %B %Y")
        if end_dt:
            time_str = f"{start_dt.strftime('%I:%M %p')} - {end_dt.strftime('%I:%M %p')}"
        else:
            time_str = start_dt.strftime("%I:%M %p")
    else:
        # all-day event
        date_only = datetime.fromisoformat(start.get("date")).date()
        date_str = date_only.strftime("%d %B %Y")
        time_str = "All day"

    msg = (
        f"📢 Reminder: {title}\n\n"
        f"{desc}\n\n"
        f"🗓 Date: {date_str}\n"
        f"⏰ Time: {time_str}\n"
        f"📍 Venue: {location}\n\n"
        "See you all there 🔥"
    )

    return msg


def list_events_tomorrow(service) -> list[dict]:
    now = datetime.now(SGT)

    # Window = tomorrow in Singapore time
    start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)

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
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Set TELEGRAM_TOKEN.")
    if not GROUP_CHAT_IDS:
        raise RuntimeError("Set GROUP_CHAT_ID (comma-separated allowed).")

    service = get_calendar_service()
    sent = load_sent()

    events = list_events_tomorrow(service)

    if not events:
        print("No events tomorrow — sending nothing.")
        return  # sends NOTHING

    for ev in events:
        ev_id = ev.get("id", "")
        start = ev.get("start", {})
        start_key = start.get("dateTime") or start.get("date") or ""

        key = f"{ev_id}:{start_key}:T-1"

        if key in sent:
            continue  # already reminded (note: on GitHub Actions, this file won't persist)

        msg = format_event_message(ev)
        send_telegram(msg)

        sent.add(key)

    save_sent(sent)
    print(f"Sent reminders for {len(events)} event(s) tomorrow.")


if __name__ == "__main__":
    run_daily_reminders()
