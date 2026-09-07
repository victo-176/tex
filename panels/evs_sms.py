#!/usr/bin/env python3
"""
EVS SMS OTP Bot — Clean Version
Uses hardcoded credentials, same bot token/groups as main bot.

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

# Resolve the main bot's DB
def _resolve_db_path():
    env = os.environ.get("DB_PATH")
    if env:
        return env
    for candidate in ("/app/data/bot.db", "data/bot.db", "data/ivasms_bot.db"):
        if os.path.isfile(candidate):
            return candidate
    return "/app/data/bot.db"

DB_PATH = _resolve_db_path()

EVS_CONFIG = {
    "username": "Mustapha",
    "password": "@Mm64500589",
    "login_url": "http://57.129.107.62/ints/login",
    "signin_url": "http://57.129.107.62/ints/signin",
    "api_url": "http://57.129.107.62/ints/agent/res/data_smscdr.php",
}

POLL_INTERVAL = 15

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
    t = os.environ.get("BOT_TOKEN")
    return t if t else get_setting("bot_token")

def get_otp_groups():
    raw = get_setting("otp_groups", "[]")
    try:
        return [int(g) for g in json.loads(raw)]
    except Exception:
        return []

def get_admin_ids():
    raw = get_setting("admin_ids", "")
    if not raw:
        return []
    try:
        return [int(x.strip()) for x in raw.split(",") if x.strip()]
    except Exception:
        return []

# =========================== VALIDATE ===========================
BOT_TOKEN = get_bot_token()
OTP_GROUPS = get_otp_groups()
ADMIN_IDS = get_admin_ids()

if not BOT_TOKEN:
    print("ERROR: BOT_TOKEN missing")
    sys.exit(1)
if not OTP_GROUPS:
    OTP_GROUPS = [-1003598369115]  # fallback default group

# =========================== COUNTRY FLAGS ===========================
COUNTRY_FLAGS = {
    'EGYPT': '🇪🇬', 'GHANA': '🇬🇭', 'NIGERIA': '🇳🇬',
    'KENYA': '🇰🇪', 'SOUTH AFRICA': '🇿🇦', 'MOROCCO': '🇲🇦',
    'UAE': '🇦🇪', 'INDIA': '🇮🇳', 'PAKISTAN': '🇵🇰',
    'TURKEY': '🇹🇷', 'USA': '🇺🇸', 'UK': '🇬🇧',
    'CANADA': '🇨🇦', 'AUSTRALIA': '🇦🇺', 'GERMANY': '🇩🇪',
    'FRANCE': '🇫🇷', 'SPAIN': '🇪🇸', 'ITALY': '🇮🇹',
    'BRAZIL': '🇧🇷', 'MEXICO': '🇲🇽', 'ARGENTINA': '🇦🇷',
    'LAOS': '🇱🇦', 'LEBANON': '🇱🇧', 'JORDAN': '🇯🇴',
    'ISRAEL': '🇮🇱', 'SAUDI ARABIA': '🇸🇦', 'KUWAIT': '🇰🇼',
    'QATAR': '🇶🇦', 'OMAN': '🇴🇲', 'BAHRAIN': '🇧🇭',
    'RUSSIA': '🇷🇺', 'CHINA': '🇨🇳', 'JAPAN': '🇯🇵',
    'SOUTH KOREA': '🇰🇷', 'SINGAPORE': '🇸🇬', 'MALAYSIA': '🇲🇾',
    'INDONESIA': '🇮🇩', 'PHILIPPINES': '🇵🇭', 'VIETNAM': '🇻🇳',
    'THAILAND': '🇹🇭', 'CAMBODIA': '🇰🇭', 'MYANMAR': '🇲🇲',
    'BANGLADESH': '🇧🇩', 'SRI LANKA': '🇱🇰', 'NEPAL': '🇳🇵',
}

# =========================== SETUP ===========================
logging.basicConfig(level=logging.INFO, format="%(asctime)s [EVS] %(message)s")
logger = logging.getLogger(__name__)

send_base_url = f"https://api.telegram.org/bot{BOT_TOKEN}"

# =========================== TELEGRAM ===========================
def send_to_groups(text, reply_markup=None):
    sent = 0
    for gid in OTP_GROUPS:
        try:
            payload = {"chat_id": gid, "text": text, "parse_mode": "HTML"}
            if reply_markup:
                payload["reply_markup"] = reply_markup
            r = requests.post(f"{send_base_url}/sendMessage", data=payload, timeout=10)
            if r.status_code == 200:
                sent += 1
            else:
                logger.error(f"Telegram send to {gid} failed: {r.status_code}")
        except Exception as exc:
            logger.error(f"Telegram error to {gid}: {exc}")
    return sent > 0


def send_to_admins(text):
    for admin_id in ADMIN_IDS:
        try:
            payload = {"chat_id": admin_id, "text": text, "parse_mode": "HTML"}
            requests.post(f"{send_base_url}/sendMessage", data=payload, timeout=10)
        except Exception:
            pass


# =========================== EVS LOGIN ===========================
def evs_login():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': 'application/json, text/javascript, */*',
    })
    try:
        response = session.get(EVS_CONFIG["login_url"], timeout=30)
        soup = BeautifulSoup(response.text, 'html.parser')
        numbers = re.findall(r'(\d+)\s*\+\s*(\d+)', soup.get_text())
        login_data = {'username': EVS_CONFIG["username"], 'password': EVS_CONFIG["password"]}
        if numbers:
            num1, num2 = numbers[0]
            login_data['capt'] = str(int(num1) + int(num2))
        response = session.post(EVS_CONFIG["signin_url"], data=login_data, timeout=30, allow_redirects=True)
        if "dashboard" in response.url.lower():
            logger.info("EVS Login successful!")
            return session
        logger.warning("EVS Login failed")
        return None
    except Exception as e:
        logger.error(f"EVS Login error: {e}")
        return None


# =========================== FETCH OTPS ===========================
def evs_fetch_otps(session):
    all_otps = []
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        two_days_ago = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        for date in [today, yesterday, two_days_ago]:
            params = {
                "draw": "1", "start": "0", "length": "500",
                "search[value]": "", "search[regex]": "false",
                "order[0][column]": "0", "order[0][dir]": "asc",
                "fdate1": f"{date} 00:00:00", "fdate2": f"{date} 23:59:59",
                "frange": "", "fclient": "", "fnum": "", "fcli": "",
                "fgdate": "", "fgmonth": "", "fgrange": "",
                "fgclient": "", "fgnumber": "", "fgcli": "",
                "fg": "0",
            }
            response = session.get(EVS_CONFIG["api_url"], params=params, timeout=30)
            if response.status_code == 200:
                data = response.json()
                if 'aaData' in data:
                    for record in data['aaData']:
                        if isinstance(record, list) and len(record) >= 6:
                            timestamp = record[0] if record[0] else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            range_name = str(record[1]) if record[1] else ""
                            number = str(record[2]) if record[2] else ""
                            service = str(record[3]) if record[3] else "Unknown"
                            full_text = str(record[5]) if len(record) > 5 and record[5] else ""
                            country_from_range = ""
                            if range_name:
                                parts = range_name.split()
                                if parts:
                                    country_from_range = parts[0]
                            otp_patterns = [
                                r'code\s+(\d{4,6})',
                                r'code[:]\s*(\d{4,6})',
                                r'use code\s+(\d{4,6})',
                                r'code\s*[:]?\s*(\d{4,6})',
                            ]
                            otp = None
                            for pattern in otp_patterns:
                                match = re.search(pattern, full_text, re.IGNORECASE)
                                if match:
                                    candidate = match.group(1)
                                    if candidate not in ['2026', '2025', '2024', '2023']:
                                        otp = candidate
                                        break
                            if otp:
                                all_otps.append({
                                    'otp': otp, 'number': number, 'service': service,
                                    'message': full_text, 'timestamp': timestamp,
                                    'range': range_name, 'country_from_range': country_from_range,
                                })
        return all_otps
    except Exception as e:
        logger.error(f"EVS fetch error: {e}")
        return []


# =========================== FORMAT MESSAGE ===========================
def evs_format_message(otp_data):
    country = "Unknown"
    if otp_data.get('country_from_range'):
        country = otp_data['country_from_range'].upper()
    if country == "Unknown":
        country_match = re.search(
            r'(EGYPT|GHANA|NIGERIA|KENYA|SOUTH AFRICA|MOROCCO|UAE|INDIA|PAKISTAN|TURKEY|USA|UK|CANADA|AUSTRALIA|GERMANY|FRANCE|SPAIN|ITALY|BRAZIL|MEXICO|ARGENTINA|LAOS|LEBANON|JORDAN|ISRAEL|SAUDI ARABIA|KUWAIT|QATAR|OMAN|BAHRAIN|RUSSIA|CHINA|JAPAN|SOUTH KOREA|SINGAPORE|MALAYSIA|INDONESIA|PHILIPPINES|VIETNAM|THAILAND|CAMBODIA|MYANMAR|BANGLADESH|SRI LANKA|NEPAL)',
            otp_data['message'], re.IGNORECASE,
        )
        if country_match:
            country = country_match.group(1).upper()
    service = otp_data.get('service', 'Unknown')
    if service == 'Unknown':
        service_match = re.search(
            r'(BOLT|GOOGLE|WHATSAPP|UBER|FACEBOOK|INSTAGRAM|PAYPAL|AMAZON|MICROSOFT|MEIZU|VERIFICATION|IATSMS|INDRIVE)',
            otp_data['message'], re.IGNORECASE,
        )
        if service_match:
            service = service_match.group(1).upper()
    flag = COUNTRY_FLAGS.get(country.upper(), '🌍')
    full_text = otp_data['message']
    full_text = re.sub(r'\b\w+-EVS\d+\w*\b', '', full_text, flags=re.IGNORECASE)
    full_text = re.sub(r'\b\w+-choice-\w+-\d+\w*\b', '', full_text, flags=re.IGNORECASE)
    full_text = re.sub(r'€\s*[\d.]+\s*[\d.]*', '', full_text)
    full_text = re.sub(r'\$\s*[\d.]+\s*[\d.]*', '', full_text)
    full_text = re.sub(r'USD\s*[\d.]+\s*[\d.]*', '', full_text, flags=re.IGNORECASE)
    full_text = re.sub(r'My Payout\s*[\d.]*', '', full_text, flags=re.IGNORECASE)
    full_text = re.sub(r'Client Payout\s*[\d.]*', '', full_text, flags=re.IGNORECASE)
    full_text = re.sub(r'\s+', ' ', full_text).strip()
    return f"""🔥 {country} {service.upper()} OTP Received!


📅 Time: {otp_data['timestamp']}
🗺️ Country: {country} {flag}
📱 Service: {service.upper()}
📞 Number: {otp_data['number']}
🔑 OTP: {otp_data['otp']}


📩 Message:
{full_text[:500]}"""


# =========================== MAIN ===========================
def main():
    last_hashes = set()
    total_sent = 0
    first_run = True

    print("=" * 50)
    print("  EVS SMS OTP Bot — Clean Version")
    print("=" * 50)
    print(f"  Panel: {EVS_CONFIG['api_url']}")
    print(f"  Groups: {len(OTP_GROUPS)}")
    print(f"  Admins: {len(ADMIN_IDS)}")
    print("=" * 50)

    # Send startup messages
    startup_msg = "🟢 <b>EVS Bot Online!</b>\n📡 Monitoring OTPs..."
    send_to_groups(startup_msg)
    send_to_admins(startup_msg)

    # Send test OTP
    test_msg = f"""🔥 <b>TEST OTP — EVS Scraper</b>

📅 Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
🗺️ Country: 🌍 TEST
📱 Service: EVS
📞 Number: +1234567890
🔑 OTP: 123456

📩 Message:
This is a test message to confirm EVS forwarder is working correctly!"""
    send_to_groups(test_msg)
    send_to_admins(test_msg)
    logger.info("Startup messages + test OTP sent!")

    while True:
        try:
            session = evs_login()
            if not session:
                logger.error("EVS: Login failed, retrying in 30s...")
                time.sleep(30)
                continue
            logger.info("EVS: Logged in, monitoring OTPs...")
            while True:
                otps = evs_fetch_otps(session)
                for otp_data in otps:
                    sms_id = hashlib.md5(
                        (otp_data['otp'] + otp_data['timestamp']).encode()
                    ).hexdigest()
                    if sms_id not in last_hashes:
                        if not first_run:
                            msg = evs_format_message(otp_data)
                            send_to_groups(msg)
                            send_to_admins(msg)
                            total_sent += 1
                            logger.info(f"✅ EVS: Sent {otp_data['otp']} (Total: {total_sent})")
                        last_hashes.add(sms_id)
                if first_run:
                    logger.info(f"EVS: Initialized with {len(last_hashes)} existing OTPs")
                    first_run = False
                time.sleep(POLL_INTERVAL)
        except Exception as e:
            logger.error(f"EVS: Loop error: {e}")
            time.sleep(30)


if __name__ == "__main__":
    main()
