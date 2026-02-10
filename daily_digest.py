import os
import json
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TZ = ZoneInfo("Asia/Singapore")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Comma-separated list of chat IDs, e.g. "-100111,-100222,-100333"
GROUP_CHAT_IDS_RAW = os.getenv("GROUP_CHAT_ID", "")
GROUP_CHAT_IDS = [gid.strip() for gid in GROUP_CHAT_IDS_RAW.split(",") if gid.strip()]

# Paste your local token.json content into this secret
GOOGLE_TOKEN_JSON = os.getenv("GOOGLE_TOKEN_JSON")

TOKEN_FILE = "token.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

# Note: On GitHub Actions, files do not persist between runs.
# This only helps within the same run (still useful if you loop/retry).
SENT_FILE = "sent_daily.json"


def ensure_token_file():
    if os.path.exists(TOKEN_FILE):
        return
    if not GOOGLE_TOKEN_JSON:
        raise RuntimeError("Missing GOOGLE_TOKEN_JSON env var.")
    json.loads(GOOGLE_TOKEN_JSON)  # validate JSON
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(GOOGLE_TOKEN_JSON)


def send_telegram_to_all(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in GROUP_CHAT_IDS:
        payload = {"chat_id": chat_id, "text": text}
        r = requests.post(url, json=payload, timeout=20)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram error for {chat_id}: {data}")


def get_calendar_service():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    return build("calendar", "v3", credentials=creds)


def clean_text(s: str, max_len: int = 240) -> str:
    s = (s or "").strip()
    s = " ".join(s.split())  # collapse whitespace/newlines
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    return s


def format_event_line(ev: dict, tomorrow_date) -> list[str]:
    title = ev.get("summary", "(No title)")

    start = ev.get("start", {})
    is_all_day = "dateTime" not in start

    if is_all_day:
        time_str = "All day"
        date_str = tomorrow_date.strftime("%a %d %b %Y")
    else:
        st = datetime.fromisoformat(start["dateTime"]).astimezone(TZ)
        time_str = st.strftime("%I:%M %p")
        date_str = st.strftime("%a %d %b %Y")

    location = clean_text(ev.get("location", ""), 120)
    description = clean_text(ev.get("description", ""), 240)

    lines = []
    lines.append(f"- {title}")
    lines.append(f"  📅 {date_str}  ⏰ {time_str}")

    if location:
        lines.append(f"  📍 {location}")

    if description:
        lines.append(f"  📝 {description}")

    return lines


def run_once():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Set TELEGRAM_TOKEN in GitHub Secrets.")
    if not GROUP_CHAT_IDS:
        raise RuntimeError(
            "Set GROUP_CHAT_ID in GitHub Secrets as comma-separated chat IDs."
        )

    ensure_token_file()
    service = get_calendar_service()

    tomorrow = datetime.now(TZ).date() + timedelta(days=1)
    start_dt = datetime.combine(tomorrow, dtime(0, 0), tzinfo=TZ)
    end_dt = start_dt + timedelta(days=1)

    events_result = service.events().list(
        calendarId="primary",
        timeMin=start_dt.isoformat(),
        timeMax=end_dt.isoformat(),
        singleEvents=True,  # expands recurring events
        orderBy="startTime",
        maxResults=200,
    ).execute()

    events = events_result.get("items", [])

    if not events:
        msg = f"📅 Tomorrow ({tomorrow.strftime('%a %d %b %Y')}): No events."
        send_telegram_to_all(msg)
        print("Sent: no events")
        return

    header = f"📅 Tomorrow ({tomorrow.strftime('%a %d %b %Y')}) — {len(events)} event(s):"
    body_lines = [header, ""]

    for ev in events:
        body_lines.extend(format_event_line(ev, tomorrow))
        body_lines.append("")  # blank line between events

    msg = "\n".join(body_lines).strip()
    send_telegram_to_all(msg)
    print(f"Sent digest to {len(GROUP_CHAT_IDS)} chat(s).")


if __name__ == "__main__":
    run_once()
