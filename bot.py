import os
import sys
import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
SGT = ZoneInfo("Asia/Singapore")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROUP_CHAT_ID_RAW = os.getenv("GROUP_CHAT_ID", "")
GROUP_IDS = [x.strip() for x in GROUP_CHAT_ID_RAW.split(",") if x.strip()]

if os.getenv("CREDENTIALS_JSON") and not os.path.exists("credentials.json"):
    with open("credentials.json", "w", encoding="utf-8") as f:
        f.write(os.getenv("CREDENTIALS_JSON"))

if os.getenv("GOOGLE_TOKEN_JSON") and not os.path.exists("token.json"):
    with open("token.json", "w", encoding="utf-8") as f:
        f.write(os.getenv("GOOGLE_TOKEN_JSON"))

SENT_FILE = "sent.json"

GROUPS_BY_TAG = {
    "TECHNICAL": ["-5256210631"],
    "MARKETING": ["-5047168117"],
    "ALL": ["-5256210631", "-5047168117"],
}

DEFAULT_GROUPS = GROUP_IDS


def load_sent() -> set[str]:
    try:
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_sent(sent: set[str]) -> None:
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(sent)), f)


def tg_send(chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=20)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram error for {chat_id}: {data}")


def tg_send_many(chat_ids: list[str], text: str) -> None:
    for gid in chat_ids:
        tg_send(gid, text)


def get_calendar_service():
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    return build("calendar", "v3", credentials=creds)


def nice_time(dt: datetime) -> str:
    return dt.strftime("%I:%M%p").lstrip("0")


def pick_target_groups(ev: dict) -> list[str]:
    title = (ev.get("summary") or "").strip().upper()
    for tag, groups in GROUPS_BY_TAG.items():
        if f"[{tag}]" in title:
            return groups
    return DEFAULT_GROUPS


def clean_title(title: str) -> str:
    t = title.strip()
    t = re.sub(r"\[.*?\]", "", t)
    return " ".join(t.split()).strip() or "(No title)"


def format_event_message(ev: dict, *, is_test: bool) -> str:
    raw_title = ev.get("summary", "(No title)")
    title = clean_title(raw_title)
    desc = (ev.get("description") or "").strip()
    location = (ev.get("location") or "TBC").strip()

    start = ev.get("start", {})
    end = ev.get("end", {})

    if start.get("dateTime"):
        start_dt = datetime.fromisoformat(start["dateTime"]).astimezone(SGT)
        end_dt = (
            datetime.fromisoformat(end["dateTime"]).astimezone(SGT)
            if end.get("dateTime")
            else start_dt + timedelta(hours=1)
        )
        date_str = start_dt.strftime("%d %B %Y")
        time_str = f"{nice_time(start_dt)} - {nice_time(end_dt)}"
    else:
        date_only = datetime.fromisoformat(start["date"]).date()
        date_str = date_only.strftime("%d %B %Y")
        time_str = "All day"

    header = "📢 Reminder" if not is_test else "🧪 TEST Reminder"

    if desc:
        return (
            f"{header}: {title}\n\n"
            f"{desc}\n\n"
            f"🗓 Date: {date_str}\n"
            f"⏰ Time: {time_str}\n"
            f"📍 Venue: {location}\n\n"
            "See you all there 🔥"
        )

    return (
        f"{header}: {title}\n\n"
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
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Missing TELEGRAM_TOKEN.")
    if not GROUP_IDS:
        raise RuntimeError("Missing GROUP_CHAT_ID (use comma-separated IDs if multiple).")
    if not os.path.exists("token.json"):
        raise RuntimeError("token.json not found (GOOGLE_TOKEN_JSON secret missing or not written).")

    service = get_calendar_service()
    sent = load_sent()

    events = list_events_tomorrow(service)
    if not events:
        return

    for ev in events:
        ev_id = ev.get("id", "")
        start = ev.get("start", {})
        start_key = start.get("dateTime") or start.get("date") or ""
        key = f"{ev_id}:{start_key}:T-1"

        if not is_test and key in sent:
            continue

        msg = format_event_message(ev, is_test=is_test)
        targets = pick_target_groups(ev)
        tg_send_many(targets, msg)

        if not is_test:
            sent.add(key)

    if not is_test:
        save_sent(sent)


if __name__ == "__main__":
    run_daily(is_test=("--test" in sys.argv))
