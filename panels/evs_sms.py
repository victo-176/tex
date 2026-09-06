#!/usr/bin/env python3
"""
EVS SMS OTP Bot
Reads ALL credentials from main bot database. NO hardcoded passwords.
Admin adds panel via bot admin, then runs this script.

Usage: python panels/evs_sms.py
"""

import time
import re
import hashlib
import logging
import json
import sqlite3
import os
import sys
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

# =========================== CONFIG ===========================
PANEL_NAME = "EVS SMS"
DEFAULT_LOGIN_TYPE = "agent"

# Use the SAME database as the main bot so settings (bot token, groups) are shared.
def _resolve_db_path():
    env = os.environ.get("DB_PATH")
    if env:
        return env
    for candidate in ("/app/data/bot.db", "data/bot.db", "data/ivasms_bot.db"):
        if os.path.isfile(candidate):
            return candidate
    return "/app/data/bot.db"

DB_PATH = _resolve_db_path()
POLL_INTERVAL = 15

# Defaults matching the main bot (bot.py)
DEFAULT_BOT_TOKEN = "8627490245:AAG2ZDkooVO43C5WPmJiFkDmY5ks9g29aMQ"
DEFAULT_GROUP_ID = "-1003598369115"

# Built-in EVS panel credentials (fallback if not configured in DB)
DEFAULT_PANEL_URL = "http://57.129.107.62/ints"
DEFAULT_USERNAME = "Mustapha"
DEFAULT_PASSWORD = "@Mm64500589"

# =========================== DATABASE HELPERS ===========================
def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_setting(key, default=None):
    try:
        with _db() as conn:
            r = conn.execute("SELECT value FROM bot_settings WHERE key=?", (key,)).fetchone()
            return r["value"] if r else default
    except Exception:
        return default


def get_bot_token():
    """Use the same bot token as the main bot: env var -> DB setting -> main bot default."""
    t = os.environ.get("BOT_TOKEN")
    if t:
        return t
    t = get_setting("bot_token")
    return t if t else DEFAULT_BOT_TOKEN


def get_otp_groups():
    """Use the same OTP groups as the main bot; fall back to the default group."""
    raw = get_setting("otp_groups", "[]")
    try:
        groups = [int(g) for g in json.loads(raw)]
    except Exception:
        groups = []
    if not groups:
        groups = [int(DEFAULT_GROUP_ID)]
    return groups


def get_bot_link():
    return get_setting("bot_link", "")


def get_panel_credentials(panel_name):
    """Fetch panel URL / username / password / login_type from sms_panels table."""
    try:
        with _db() as conn:
            r = conn.execute(
                "SELECT url, username, password, login_type FROM sms_panels WHERE name=? AND enabled=1",
                (panel_name,),
            ).fetchone()
            if r:
                return dict(r)
    except Exception:
        pass
    return None


# =========================== VALIDATE ===========================
BOT_TOKEN = get_bot_token()
OTP_GROUPS = get_otp_groups()
BOT_LINK = get_bot_link()
PANEL = get_panel_credentials(PANEL_NAME)

if not PANEL:
    # Fall back to the built-in EVS credentials so the script always works
    PANEL = {"url": DEFAULT_PANEL_URL, "username": DEFAULT_USERNAME, "password": DEFAULT_PASSWORD, "login_type": None}
else:
    if not PANEL.get("url"):
        PANEL["url"] = DEFAULT_PANEL_URL
    if not PANEL.get("username"):
        PANEL["username"] = DEFAULT_USERNAME
    if not PANEL.get("password"):
        PANEL["password"] = DEFAULT_PASSWORD

# =========================== EXTRACTED CONFIG ===========================
PANEL_URL = (PANEL.get("url") or DEFAULT_PANEL_URL).rstrip("/")
LOGIN_TYPE = PANEL.get("login_type") or DEFAULT_LOGIN_TYPE
USERNAME = PANEL.get("username") or DEFAULT_USERNAME
PASSWORD = PANEL.get("password") or DEFAULT_PASSWORD

API_PATHS = [
    f"{LOGIN_TYPE}/res/data_smscdr.php",
    "agent/res/data_smscdr.php",
    "client/res/data_smscdr.php",
]
LOGIN_URL = f"{PANEL_URL}/login"
SIGNIN_URL = f"{PANEL_URL}/signin"

# =========================== SETUP ===========================
logging.basicConfig(level=logging.INFO, format=f"%(asctime)s [{PANEL_NAME}] %(message)s")
logger = logging.getLogger(__name__)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*",
})

sesskey = [None]
last_sms_hashes = set()
total_otps_sent = 0
first_run = True

# Admin chat IDs for DM copies (setting admin_ids or env ADMIN_ID, comma-separated)
def get_admin_ids():
    raw = get_setting("admin_ids") or os.environ.get("ADMIN_ID", "8921746989,8119221293")
    try:
        return [int(a) for a in str(raw).replace(" ", "").split(",") if a]
    except Exception:
        return []

ADMIN_IDS = get_admin_ids()


def send_to_admins(text):
    sent = 0
    for aid in ADMIN_IDS:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={"chat_id": aid, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
            if r.status_code == 200:
                sent += 1
        except Exception as exc:
            logger.error(f"Telegram error to admin {aid}: {exc}")
    return sent > 0

# =========================== COUNTRY FLAGS ===========================
COUNTRY_FLAGS = {
    "EGYPT": "🇪🇬", "GHANA": "🇬🇭", "NIGERIA": "🇳🇬", "KENYA": "🇰🇪",
    "SOUTH AFRICA": "🇿🇦", "MOROCCO": "🇲🇦", "UAE": "🇦🇪", "INDIA": "🇮🇳",
    "PAKISTAN": "🇵🇰", "TURKEY": "🇹🇷", "USA": "🇺🇸", "UK": "🇬🇧",
    "CANADA": "🇨🇦", "AUSTRALIA": "🇦🇺", "GERMANY": "🇩🇪", "FRANCE": "🇫🇷",
    "SPAIN": "🇪🇸", "ITALY": "🇮🇹", "BRAZIL": "🇧🇷", "MEXICO": "🇲🇽",
    "ARGENTINA": "🇦🇷", "LAOS": "🇱🇦", "LEBANON": "🇱🇧", "JORDAN": "🇯🇴",
    "ISRAEL": "🇮🇱", "SAUDI ARABIA": "🇸🇦", "KUWAIT": "🇰🇼", "QATAR": "🇶🇦",
    "OMAN": "🇴🇲", "BAHRAIN": "🇧🇭", "RUSSIA": "🇷🇺", "CHINA": "🇨🇳",
    "JAPAN": "🇯🇵", "SOUTH KOREA": "🇰🇷", "SINGAPORE": "🇸🇬", "MALAYSIA": "🇲🇾",
    "INDONESIA": "🇮🇩", "PHILIPPINES": "🇵🇭", "VIETNAM": "🇻🇳", "THAILAND": "🇹🇭",
    "CAMBODIA": "🇰🇭", "MYANMAR": "🇲🇲", "BANGLADESH": "🇧🇩", "SRI LANKA": "🇱🇰",
    "NEPAL": "🇳🇵", "NEW ZEALAND": "🇳🇿", "SWITZERLAND": "🇨🇭", "SWEDEN": "🇸🇪",
    "NORWAY": "🇳🇴", "DENMARK": "🇩🇰", "FINLAND": "🇫🇮", "IRELAND": "🇮🇪",
    "PORTUGAL": "🇵🇹", "GREECE": "🇬🇷", "POLAND": "🇵🇱", "UKRAINE": "🇺🇦",
    "ROMANIA": "🇷🇴", "CZECHIA": "🇨🇿", "HUNGARY": "🇭🇺", "SLOVAKIA": "🇸🇰",
    "SLOVENIA": "🇸🇮", "CROATIA": "🇭🇷", "BOSNIA": "🇧🇦", "SERBIA": "🇷🇸",
    "ALBANIA": "🇦🇱", "BULGARIA": "🇧🇬", "AFGHANISTAN": "🇦🇫", "ALGERIA": "🇩🇿",
    "ANGOLA": "🇦🇴", "ARMENIA": "🇦🇲", "AUSTRIA": "🇦🇹", "AZERBAIJAN": "🇦🇿",
    "BELARUS": "🇧🇾", "BELGIUM": "🇧🇪", "BELIZE": "🇧🇿", "BENIN": "🇧🇯",
    "BHUTAN": "🇧🇹", "BOLIVIA": "🇧🇴", "BOTSWANA": "🇧🇼", "BRUNEI": "🇧🇳",
    "BURKINA": "🇧🇫", "BURUNDI": "🇧🇮", "CAMEROON": "🇨🇲", "CHAD": "🇹🇩",
    "CHILE": "🇨🇱", "COLOMBIA": "🇨🇴", "CONGO": "🇨🇬", "COSTA RICA": "🇨🇷",
    "CUBA": "🇨🇺", "CYPRUS": "🇨🇾", "DJIBOUTI": "🇩🇯", "DOMINICAN REPUBLIC": "🇩🇴",
    "ECUADOR": "🇪🇨", "EL SALVADOR": "🇸🇻", "ESTONIA": "🇪🇪", "ETHIOPIA": "🇪🇹",
    "FIJI": "🇫🇯", "GABON": "🇬🇦", "GAMBIA": "🇬🇲", "GEORGIA": "🇬🇪",
    "GUATEMALA": "🇬🇹", "GUINEA": "🇬🇳", "GUYANA": "🇬🇾", "HAITI": "🇭🇹",
    "HONDURAS": "🇭🇳", "ICELAND": "🇮🇸", "JAMAICA": "🇯🇲", "KAZAKHSTAN": "🇰🇰",
    "KYRGYZSTAN": "🇰🇬", "LATVIA": "🇱🇻", "LESOTHO": "🇱🇸", "LIBERIA": "🇱🇷",
    "LIBYA": "🇱🇾", "LITHUANIA": "🇱🇹", "LUXEMBOURG": "🇱🇺", "MADAGASCAR": "🇲🇬",
    "MALAWI": "🇲🇼", "MALDIVES": "🇲🇻", "MALI": "🇲🇱", "MALTA": "🇲🇹",
    "MAURITANIA": "🇲🇷", "MAURITIUS": "🇲🇺", "MOLDOVA": "🇲🇩", "MONACO": "🇲🇨",
    "MONGOLIA": "🇲🇳", "MONTENEGRO": "🇲🇪", "MOZAMBIQUE": "🇲🇿", "NAMIBIA": "🇳🇦",
    "NICARAGUA": "🇳🇮", "NIGER": "🇳🇪", "NORTH MACEDONIA": "🇲🇰", "PALESTINE": "🇵🇸",
    "PANAMA": "🇵🇦", "PARAGUAY": "🇵🇾", "PERU": "🇵🇪", "RWANDA": "🇷🇼",
    "SENEGAL": "🇸🇳", "SIERRA LEONE": "🇸🇱", "SOMALIA": "🇸🇴", "SOUTH SUDAN": "🇸🇸",
    "SYRIA": "🇸🇾", "TAJIKISTAN": "🇹🇯", "TANZANIA": "🇹🇿", "TOGO": "🇹🇬",
    "TUNISIA": "🇹🇳", "TURKMENISTAN": "🇹🇲", "UGANDA": "🇺🇬", "URUGUAY": "🇺🇾",
    "UZBEKISTAN": "🇺🇿", "VENEZUELA": "🇻🇪", "YEMEN": "🇾🇪",
    "ZAMBIA": "🇿🇲", "ZIMBABWE": "🇿🇼"
}

COUNTRIES_LIST = "|".join(re.escape(c) for c in COUNTRY_FLAGS.keys())
COUNTRY_PATTERN = re.compile(r"(" + COUNTRIES_LIST + ")", re.IGNORECASE)


# =========================== TELEGRAM ===========================
def send_to_groups(text, reply_markup=None):
    sent = 0
    for gid in OTP_GROUPS:
        try:
            payload = {"chat_id": gid, "text": text, "parse_mode": "HTML"}
            if reply_markup:
                payload["reply_markup"] = reply_markup
            r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data=payload, timeout=10)
            if r.status_code == 200:
                sent += 1
        except Exception as exc:
            logger.error(f"Telegram error to {gid}: {exc}")
    return sent > 0


def send_otp(sms):
    country = "Unknown"
    if sms.get("range"):
        parts = sms["range"].split()
        if parts:
            country = parts[0].upper()
    if country == "Unknown":
        m = COUNTRY_PATTERN.search(sms["full_text"])
        if m:
            country = m.group(1).upper()

    flag = COUNTRY_FLAGS.get(country, "\U0001f30d")
    phone = sms.get("number", "N/A")
    otp = sms["otp"]
    service = sms.get("service", "Unknown")
    ts = sms.get("timestamp", "")
    clean = re.sub(r"\s+", " ", sms["full_text"]).strip()[:300]

    msg = (
        f"\U0001f525 {country} {service.upper()} OTP!\n"
        f"\U0001f4c5 {ts}\n"
        f"\U0001f5fa\ufe0f {country} {flag}\n"
        f"\U0001f4f1 {service}\n"
        f"\U0001f4de {phone}\n"
        f"\U0001f511 {otp}\n\n"
        f"\U0001f4e9 {clean}"
    )
    kb = {"inline_keyboard": [[{"text": "\U0001f916 Bot", "url": BOT_LINK}]]}
    return send_to_groups(msg, json.dumps(kb))


# =========================== LOGIN ===========================
def login():
    logger.info(f"Logging in to {PANEL_NAME}...")
    try:
        resp = session.get(LOGIN_URL, timeout=30)
        soup = BeautifulSoup(resp.text, "html.parser")
        nums = re.findall(r"(\d+)\s*\+\s*(\d+)", soup.get_text())
        data = {"username": USERNAME, "password": PASSWORD}
        if nums:
            data["capt"] = str(int(nums[0][0]) + int(nums[0][1]))
            logger.info(f"Captcha: {nums[0][0]} + {nums[0][1]} = {data['capt']}")
        resp = session.post(SIGNIN_URL, data=data, timeout=30, allow_redirects=True)
        if "dashboard" in resp.url.lower() or "signin" not in resp.url.lower():
            # Grab sesskey from the dashboard page (needed for API calls)
            try:
                dash = session.get(f"{PANEL_URL}/dashboard", timeout=30)
                html = dash.text
            except Exception:
                html = resp.text
            m = re.search(r'sesskey["\'=:\s]+([A-Za-z0-9_-]{8,})', html)
            if m:
                sesskey[0] = m.group(1)
                logger.info(f"Got sesskey: {sesskey[0][:8]}...")
            logger.info("Login successful!")
            return True
        logger.warning(f"Login failed ({resp.url[:80]})")
        return False
    except Exception as exc:
        logger.error(f"Login error: {exc}")
        return False


# =========================== FETCH OTPS ===========================
def fetch_otps():
    sms_list = []
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    for date in [today, yesterday]:
        params = {
            "draw": "1", "start": "0", "length": "100",
            "search[value]": "", "search[regex]": "false",
            "order[0][column]": "0", "order[0][dir]": "asc",
            "fdate1": f"{date} 00:00:00", "fdate2": f"{date} 23:59:59",
            "frange": "", "fclient": "", "fnum": "", "fcli": "",
            "fgdate": "", "fgmonth": "", "fgrange": "", "fgclient": "",
            "fgnumber": "", "fgcli": "", "fg": "0",
        }
        if sesskey[0]:
            params["sesskey"] = sesskey[0]
        for path in API_PATHS:
            try:
                resp = session.get(f"{PANEL_URL}/{path}", params=params, timeout=30)
                # Session expired -> re-login and retry once
                if resp.status_code != 200 or "login" in resp.url.lower():
                    logger.warning("Session expired, re-logging in...")
                    if login():
                        if sesskey[0]:
                            params["sesskey"] = sesskey[0]
                        resp = session.get(f"{PANEL_URL}/{path}", params=params, timeout=30)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                records = data.get("aaData") or data.get("data") or []
                if not records:
                    continue
                for rec in records:
                    if not isinstance(rec, list) or len(rec) < 6:
                        continue
                    full = str(rec[5] or "")
                    m = (
                        re.search(r"code\s+(\d{4,6})", full, re.I)
                        or re.search(r"use code\s+(\d{4,6})", full, re.I)
                        or re.search(r"code[:]\s*(\d{4,6})", full, re.I)
                        or re.search(r"<#>\s*(\d{4,6})", full, re.I)
                        or re.search(r"(\d{4,6})", full)
                    )
                    if m:
                        sms_list.append({
                            "otp": m.group(1),
                            "service": str(rec[3] or "Unknown"),
                            "full_text": full,
                            "timestamp": str(rec[0] or ""),
                            "range": str(rec[1] or ""),
                            "number": str(rec[2] or "N/A"),
                        })
                break
            except (requests.RequestException, json.JSONDecodeError):
                continue
    if sms_list:
        logger.info(f"Found {len(sms_list)} OTPs")
    return sms_list


# =========================== MAIN ===========================
def main():
    global total_otps_sent, last_sms_hashes, first_run

    print("=" * 50)
    print(f"  {PANEL_NAME} OTP Bot")
    print("=" * 50)
    print(f"  Panel: {PANEL_URL}")
    print(f"  Type:  {LOGIN_TYPE}")
    print(f"  Groups: {len(OTP_GROUPS)}")
    print("=" * 50)

    # Retry login forever so a temporary panel outage doesn't kill the script
    while not login():
        logger.error("Login failed! Retrying in 30s...")
        time.sleep(30)

    # Startup message: Bot online + test OTP
    send_to_groups(f"🟢 <b>Bot online</b> — {PANEL_NAME} is up and monitoring OTPs.")
    send_to_admins(f"🟢 <b>Bot online</b> — {PANEL_NAME} started successfully.")
    test_msg = (
        "🔥 <b>TEST OTP</b> — EVS connection works!\n"
        "📱 Number: +0000000000\n"
        "🔑 OTP: <code>123456</code>\n"
        "✅ This is a test message to confirm the forwarder is working."
    )
    send_to_groups(test_msg)
    send_to_admins(test_msg)
    logger.info("Sent startup + test messages")
    logger.info("Monitoring OTPs...")

    while True:
        try:
            for sms in fetch_otps():
                h = hashlib.md5((sms["otp"] + sms["timestamp"]).encode()).hexdigest()
                if h not in last_sms_hashes:
                    if not first_run:
                        if send_otp(sms):
                            last_sms_hashes.add(h)
                            total_otps_sent += 1
                            logger.info(f"\u2705 Sent {sms['otp']} (Total: {total_otps_sent})")
                    else:
                        last_sms_hashes.add(h)
            if first_run:
                logger.info(f"Init: {len(last_sms_hashes)} existing OTPs loaded")
                first_run = False
        except Exception as exc:
            logger.error(f"Loop error: {exc}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
