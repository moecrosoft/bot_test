import os
import json
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ===== Settings =====
TZ = ZoneInfo("Asia/Singapore")

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
TOKEN_FILE = "token.json"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Comma-separated list, e.g. "-1001111,-1002222"
GROUP_CHAT_IDS_RAW = os.getenv("GROUP_CHAT_ID", "")
GROUP_CHAT_IDS = [x.strip() for x in GROUP_CHAT_IDS_RAW.split(",") if x.strip()]

# This should be the full contents of token.json, stored in GitHub Secrets
GOOGLE_TOKEN_JSON = os.getenv("GOOGLE_TOKEN_JSON")


def ensure_token_file() -> None:
    """
    GitHub Actions runner doesn't have token.json unless we create it.
    We store token.json contents inside a GitHub Secret (GOOGLE_TOKEN_JSON),
    then write it to token.json at runtime.
    """
    if os.path.exists(TOKEN_FILE):
        return

    if not GOOGLE_TOKEN_JSON:
        raise RuntimeError("Missing GOOGLE_TOKEN_JSON secret/env var.")

    # Validate JSON to catch copy/paste mistakes early
    json.loads(GOOGLE_TOKEN_JSON)

    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(GOOGLE_TOKEN_JSON)


def get_calendar_service():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    return build("calendar", "v3", credentials=creds)


def clean_text(s: str, max_len: int) -> str:
    s = (s or "").strip()
    s = " ".join(s.split())  # collapse whitespace/newlines
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    return s


def send_telegram_to_all(message: str) -> None:
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Missing TELEGRAM_TOKEN secret/env var.")
    if not GROUP_CHAT_IDS:
        raise RuntimeError("Missing GROUP_CHAT_ID secret/env var (comma-separated list).")

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    for chat_id in GROUP_CHAT_IDS:
        payload = {"chat_id": chat_id, "text": message}
        r = requests.post(url, json=payload, timeout=20)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram error for chat_id={chat_id}: {data}")


def format_event(ev: dict, fallback_date) -> str:
    title = ev.get("summary", "(No title)")

    start = ev.get("start", {})
    if "dateTime" in start:
        st = datetime.fromisoformat(start["dateTime"]).astimezone(TZ)
        date_str = st.strftime("%a %d %b %Y")
        time_str = st.strftime("%I:%M %p")
    else:
        # All-day event
        date_str = fallback_date.strftime("%a %d %b %Y")
        time_str = "All day"

    location = clean_text(ev.get("location", ""), 120)
    description = clean_text(ev.get("description", ""), 300)

    lines = [
        f"- {title}",
        f"  📅 {date_str}  ⏰ {time_str}",
    ]

    if location:
        lines.append(f"  📍 {location}")

    if description:
        lines.append(f"  📝 {description}")

    return "\n".join(lines)


def run_daily_digest() -> None:
    ensure_token_file()
    service = get_calendar_service()

    # Tomorrow window in Singapore time: 00:00 to next day 00:00
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

    if not events:
        msg = f"📅 Tomorrow ({tomorrow.strftime('%a %d %b %Y')}): No events."
        send_telegram_to_all(msg)
        print("Sent digest (no events).")
        return

    header = f"📅 Tomorrow ({tomorrow.strftime('%a %d %b %Y')}) — {len(events)} event(s):"
    parts = [header, ""]

    for ev in events:
        parts.append(format_event(ev, tomorrow))
        parts.append("")  # blank line between events

    msg = "\n".join(parts).strip()
    send_telegram_to_all(msg)
    print(f"Sent digest to {len(GROUP_CHAT_IDS)} chat(s).")


if __name__ == "__main__":
    run_daily_digest()
