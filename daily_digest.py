import os
import json
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ===================== CONFIG =====================
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
SGT = ZoneInfo("Asia/Singapore")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")  # e.g. "-5256210631"

SENT_FILE = "sent.json"
# ==================================================


# ---- Render support: write Google creds from env if provided ----
# Put the full JSON contents into Render env vars: CREDENTIALS_JSON and TOKEN_JSON
if os.getenv("CREDENTIALS_JSON") and not os.path.exists("credentials.json"):
    with open("credentials.json", "w", encoding="utf-8") as f:
        f.write(os.getenv("CREDENTIALS_JSON"))

if os.getenv("TOKEN_JSON") and not os.path.exists("token.json"):
    with open("token.json", "w", encoding="utf-8") as f:
        f.write(os.getenv("TOKEN_JSON"))
# ----------------------------------------------------------------


def load_sent() -> set[str]:
    try:
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_sent(sent: set[str]) -> None:
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(sent)), f)


def tg_send(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": GROUP_CHAT_ID, "text": text}, timeout=20)
    data = r.json()
    if not data.get("ok"):
        print("Telegram error:", data)


def get_calendar_service():
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    return build("calendar", "v3", credentials=creds)


def nice_time(dt: datetime) -> str:
    # Example output: "6:50PM" (no leading 0)
    return dt.strftime("%I:%M%p").lstrip("0")


def format_event_message(ev: dict, *, is_test: bool) -> str:
    title = ev.get("summary", "(No title)")
    desc = (ev.get("description") or "").strip()
    location = (ev.get("location") or "TBC").strip()

    start = ev.get("start", {})
    end = ev.get("end", {})

    # Timed event
    if start.get("dateTime"):
        start_dt = datetime.fromisoformat(start["dateTime"]).astimezone(SGT)

        # end.dateTime is usually present; if not, assume +1 hour
        if end.get("dateTime"):
            end_dt = datetime.fromisoformat(end["dateTime"]).astimezone(SGT)
        else:
            end_dt = start_dt + timedelta(hours=1)

        date_str = start_dt.strftime("%d %B %Y")
        time_str = f"{nice_time(start_dt)} - {nice_time(end_dt)}"
    else:
        # All-day event
        date_only = datetime.fromisoformat(start["date"]).date()
        date_str = date_only.strftime("%d %B %Y")
        time_str = "All day"

    # Optional label so you can tell test runs apart
    header = "📢 Reminder" if not is_test else "🧪 TEST Reminder"

    return (
        f"{header}: {title}\n\n"
        f"{desc}\n\n"
        f"🗓 Date: {date_str}\n"
        f"⏰ Time: {time_str}\n"
        f"📍 Venue: {location}\n\n"
        "See you all there 🔥"
    )


def list_events_tomorrow(service) -> list[dict]:
    now = datetime.now(SGT)
    start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)

    res = service.events().list(
        calendarId="primary",
        timeMin=start.astimezone(timezone.utc).isoformat(),
        timeMax=end.astimezone(timezone.utc).isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=50,
    ).execute()

    return res.get("items", [])


def run_daily(*, is_test: bool):
    """
    Scheduled (default): prevents duplicates using sent.json
    Manual test (--test): ALWAYS sends (doesn't touch sent.json)
    """
    if not TELEGRAM_TOKEN or not GROUP_CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_TOKEN or GROUP_CHAT_ID environment variables.")

    service = get_calendar_service()
    sent = load_sent()

    events = list_events_tomorrow(service)

    # Requirement: if no events tomorrow, send nothing
    if not events:
        print("No events tomorrow — sending nothing.")
        return

    sent_count = 0

    for ev in events:
        ev_id = ev.get("id", "")
        start = ev.get("start", {})
        start_key = start.get("dateTime") or start.get("date") or ""

        key = f"{ev_id}:{start_key}:T-1"

        # Scheduled run skips if already sent
        if not is_test and key in sent:
            continue

        tg_send(format_event_message(ev, is_test=is_test))
        sent_count += 1

        # Only record in scheduled mode (tests won't block real reminders)
        if not is_test:
            sent.add(key)

    if not is_test:
        save_sent(sent)

    print(f"Done. Sent {sent_count} reminder(s). Mode={'TEST' if is_test else 'SCHEDULED'}.")


if __name__ == "__main__":
    is_test = "--test" in sys.argv
    run_daily(is_test=is_test)
