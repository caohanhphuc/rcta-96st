#!/usr/bin/env python3
"""
Calendly Weekend Availability Crawler
Polls https://calendly.com/96th-st-rcta every 10 minutes.
Emails hanhphuc296@gmail.com only when NEW Fri/Sat/Sun slots appear.
"""

import requests
import smtplib
import time
import logging
import os
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ─── CONFIG ───────────────────────────────────────────────────────────────────

CALENDLY_USERNAME = "96th-st-rcta"

EMAIL_TO = "hanhphuc296@gmail.com"
EMAIL_FROM = "hanhphuc296@gmail.com"

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "hanhphuc296@gmail.com"
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]

CHECK_INTERVAL = 600  # 10 min
WEEKS_AHEAD = 6
TARGET_WEEKDAYS = {4: "Friday", 5: "Saturday", 6: "Sunday"}

# ─── LOGGING ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M",
)

log = logging.getLogger(__name__)

# ─── REQUEST HEADERS ──────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"https://calendly.com/{CALENDLY_USERNAME}",
}

# ─── CALENDLY HELPERS ─────────────────────────────────────────────────────────


def get_event_types() -> list[dict]:
    """
    Fetch actual Calendly event type UUIDs.
    """

    url = f"https://calendly.com/api/booking/profiles/{CALENDLY_USERNAME}/event_types"

    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()

    data = r.json()

    if isinstance(data, dict):
        raw = (
            data.get("collection")
            or data.get("data")
            or data.get("event_types")
            or data.get("results")
            or []
        )
    elif isinstance(data, list):
        raw = data
    else:
        raw = []

    event_types = []

    for et in raw:
        if not isinstance(et, dict):
            continue

        event_id = (
            et.get("uuid")
            or et.get("id")
            or et.get("uri", "").rstrip("/").split("/")[-1]
        )

        name = et.get("name", "Unknown")

        if not event_id:
            log.warning("Skipping malformed event type: %s", et)
            continue

        event_types.append({
            "id": event_id,
            "name": name,
        })

    return event_types


def get_available_days(event_id: str, start: datetime, end: datetime) -> list[str]:
    """
    Get available dates for an event type UUID.
    """

    url = (
        f"https://calendly.com/api/booking/event_types/"
        f"{event_id}/calendar/range"
    )

    params = {
        "timezone": "America/New_York",
        "diagnostics": "false",
        "range_start": start.strftime("%Y-%m-%d"),
        "range_end": end.strftime("%Y-%m-%d"),
    }

    r = requests.get(
        url,
        headers=HEADERS,
        params=params,
        timeout=20,
    )

    r.raise_for_status()

    body = r.json()

    if isinstance(body, list):
        days = body
    elif isinstance(body, dict):
        days = body.get("days", [])
    else:
        days = []

    return [
        d["date"]
        for d in days
        if isinstance(d, dict) and d.get("status") == "available"
    ]


def get_available_times(event_id: str, date: str) -> list[str]:
    """
    Get available times for a specific date.
    """

    url = (
        f"https://calendly.com/api/booking/event_types/"
        f"{event_id}/calendar/spots"
    )

    r = requests.get(
        url,
        headers=HEADERS,
        params={
            "timezone": "America/New_York",
            "date": date,
        },
        timeout=20,
    )

    r.raise_for_status()

    body = r.json()

    if isinstance(body, list):
        spots = body
    elif isinstance(body, dict):
        spots = body.get("spots", [])
    else:
        spots = []

    times = []

    for spot in spots:
        if not isinstance(spot, dict):
            continue

        if spot.get("status") != "available":
            continue

        raw = spot.get("start_time")

        if not raw:
            continue

        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))

        et = dt.astimezone(
            timezone(timedelta(hours=-4))
        )

        times.append(et.strftime("%-I:%M %p"))

    return times


def scan() -> dict[str, list[str]]:
    """
    Scan all Calendly event types for Fri/Sat/Sun openings.
    """

    event_types = get_event_types()

    log.info("Found %d event type(s)", len(event_types))

    today = datetime.now().date()

    start = datetime.combine(today, datetime.min.time())
    end = start + timedelta(weeks=WEEKS_AHEAD)

    found: dict[str, list[str]] = {}

    for et in event_types:
        event_id = et["id"]
        name = et["name"]

        log.info("Checking event type: %s (%s)", name, event_id)

        try:
            available = get_available_days(
                event_id,
                start,
                end,
            )

        except Exception as e:
            log.error(
                "Error fetching days for %s: %s",
                name,
                e,
            )
            continue

        for date_str in available:
            dt = datetime.strptime(date_str, "%Y-%m-%d")

            if dt.weekday() not in TARGET_WEEKDAYS:
                continue

            if date_str in found:
                continue

            try:
                times = get_available_times(
                    event_id,
                    date_str,
                )
            except Exception:
                times = []

            found[date_str] = times

    return found


# ─── EMAIL ────────────────────────────────────────────────────────────────────


def send_email(new_slots: dict[str, list[str]]):

    lines = [
        "New weekend private lesson slots just opened at 96th St / RCTA!\n"
    ]

    for date_str in sorted(new_slots):

        dt = datetime.strptime(date_str, "%Y-%m-%d")

        label = dt.strftime("%A, %B %-d")

        times = new_slots[date_str]

        time_str = (
            ", ".join(times)
            if times
            else "(see Calendly for exact times)"
        )

        lines.append(f"📅 {label}: {time_str}")

    lines += [
        "",
        f"Book now → https://calendly.com/{CALENDLY_USERNAME}",
        "",
        "(You'll only be notified when new slots appear.)",
    ]

    body = "\n".join(lines)

    msg = MIMEMultipart("alternative")

    msg["Subject"] = "🎾 New Weekend Lesson Slot — 96th St/RCTA"
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()

        server.login(
            SMTP_USER,
            SMTP_PASSWORD,
        )

        server.sendmail(
            EMAIL_FROM,
            EMAIL_TO,
            msg.as_string(),
        )

    log.info(
        "📧 Email sent for slots: %s",
        list(new_slots.keys()),
    )


# ─── MAIN LOOP ────────────────────────────────────────────────────────────────


def main():

    log.info(
        "Crawler started — checking every %d min.",
        CHECK_INTERVAL // 60,
    )

    alerted: set[str] = set()

    while True:

        try:

            log.info("Scanning Calendly…")

            current = scan()

            if current:
                log.info(
                    "Available weekend days: %s",
                    list(current.keys()),
                )
            else:
                log.info(
                    "No Fri/Sat/Sun openings right now."
                )

            new_slots = {
                d: t
                for d, t in current.items()
                if d not in alerted
            }

            if new_slots:

                log.info(
                    "🆕 New slots detected: %s",
                    list(new_slots.keys()),
                )

                try:
                    send_email(new_slots)

                    alerted.update(
                        new_slots.keys()
                    )

                except Exception as e:
                    log.error(
                        "Email failed: %s",
                        e,
                    )

            else:
                log.info(
                    "No new slots since last alert."
                )

            # remove past dates
            today_str = datetime.now().strftime("%Y-%m-%d")

            alerted = {
                d
                for d in alerted
                if d >= today_str
            }

        except Exception as e:
            log.error("Scan error: %s", e)

        log.info(
            "Sleeping %d min…\n",
            CHECK_INTERVAL // 60,
        )

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
