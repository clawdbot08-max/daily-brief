#!/usr/bin/env python3
"""Generate the daily morning brief as JSON for the GitHub Pages site.

Pulls the same data as the SMS morning brief: markets, weather, geopolitics, tech news.
Writes to data/briefs.json (array of daily entries, newest first, max 30 days).
"""
from __future__ import annotations

import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
BRIEFS_FILE = REPO_DIR / "data" / "briefs.json"
MAX_ENTRIES = 30

LOCATION = "Los Angeles, CA"
LATITUDE = 34.05
LONGITUDE = -118.24

SYMBOLS = [
    ("^spx", "S&P 500"),
    ("^dji", "Dow"),
    ("^ndx", "Nasdaq 100"),
]

STATE_FEEDS = [
    ("World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Middle East/Global", "https://www.aljazeera.com/xml/rss/all.xml"),
]
TECH_FEED = ("Tech", "https://www.bleepingcomputer.com/feed/")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0 Safari/537.36",
}

WEATHER_CODE_DESCRIPTIONS = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog", 51: "Light drizzle", 53: "Moderate drizzle",
    55: "Dense drizzle", 61: "Light rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow", 80: "Slight rain showers",
    81: "Moderate rain showers", 82: "Violent rain showers", 95: "Thunderstorm",
}


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={**HEADERS, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="ignore")


# --- Markets ---
def fetch_stooq_daily(symbol_code: str) -> dict | None:
    url = f"https://stooq.com/q/d/l/?s={symbol_code}&i=d"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            rows = resp.read().decode("utf-8").strip().splitlines()
    except Exception:
        return None
    if len(rows) < 2:
        return None
    last = rows[-1].split(",")
    if len(last) < 6:
        return None
    try:
        return {"Open": float(last[1]), "Close": float(last[4])}
    except ValueError:
        return None


def get_markets() -> list[dict]:
    items = []
    for symbol, label in SYMBOLS:
        row = fetch_stooq_daily(symbol)
        if row:
            op, cl = row["Open"], row["Close"]
            pct = ((cl - op) / op) * 100 if op else 0
            arrow = "▲" if pct > 0 else ("▼" if pct < 0 else "→")
            items.append({"label": label, "value": f"{cl:,.2f}", "change": f"{arrow} {pct:+.2f}%"})
        else:
            items.append({"label": label, "value": "unavailable", "change": ""})
    return items


# --- Weather ---
def get_weather() -> dict:
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={LATITUDE}&longitude={LONGITUDE}"
        "&current=temperature_2m,apparent_temperature,weather_code"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        "&temperature_unit=fahrenheit&timezone=America/Los_Angeles"
    )
    try:
        data = fetch_json(url)
    except Exception:
        return {"location": LOCATION, "conditions": "unavailable"}

    current = data.get("current", {})
    daily = data.get("daily", {})
    temp = current.get("temperature_2m")
    feels = current.get("apparent_temperature")
    code = current.get("weather_code")
    desc = WEATHER_CODE_DESCRIPTIONS.get(code, "Unknown") if code is not None else "Unknown"
    high = (daily.get("temperature_2m_max") or [None])[0]
    low = (daily.get("temperature_2m_min") or [None])[0]
    precip = (daily.get("precipitation_probability_max") or [None])[0]

    return {
        "location": LOCATION,
        "conditions": desc,
        "temp": f"{temp:.1f}°F" if temp is not None else None,
        "feelsLike": f"{feels:.1f}°F" if feels is not None else None,
        "high": f"{high:.1f}°F" if high is not None else None,
        "low": f"{low:.1f}°F" if low is not None else None,
        "precipChance": f"{precip}%" if precip is not None else None,
    }


# --- News (RSS) ---
class TextExtractor(HTMLParser):
    SKIP_TAGS = {"script", "style", "noscript", "svg", "head"}

    def __init__(self):
        super().__init__()
        self.segments: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0 and data.strip():
            self.segments.append(data.strip())


def extract_text(html: str) -> str:
    parser = TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        return ""
    return " ".join(parser.segments)


def summarize_text(text: str, max_sentences: int = 2) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(sentences[:max_sentences]).strip()[:200]


def get_feed_items(feed_url: str) -> list[ET.Element]:
    try:
        xml_text = fetch_text(feed_url)
        root = ET.fromstring(xml_text)
        channel = root.find("channel")
        return channel.findall("item") if channel is not None else []
    except Exception:
        return []


def get_news() -> list[dict]:
    results = []
    all_feeds = STATE_FEEDS + [TECH_FEED]
    for region, url in all_feeds:
        items = get_feed_items(url)
        if items:
            item = items[0]
            title = (item.findtext("title") or "").strip()
            desc_html = (item.findtext("description") or "").strip()
            summary = summarize_text(extract_text(desc_html)) if desc_html else ""
            link = (item.findtext("link") or "").strip()
            results.append({"category": region, "headline": title, "summary": summary, "url": link})
        else:
            results.append({"category": region, "headline": "unavailable", "summary": "", "url": ""})
    return results


def main() -> None:
    now = datetime.now(timezone.utc).astimezone()
    today_str = now.strftime("%Y-%m-%d")

    brief = {
        "date": today_str,
        "generatedAt": now.isoformat(),
        "title": now.strftime("Morning Brief — %a %b %d, %Y"),
        "markets": get_markets(),
        "weather": get_weather(),
        "news": get_news(),
    }

    BRIEFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        briefs = json.loads(BRIEFS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        briefs = []

    # Replace today's entry if it exists, or append
    briefs = [b for b in briefs if b.get("date") != today_str]
    briefs.append(brief)
    # Keep only last N days
    briefs.sort(key=lambda b: b.get("date", ""), reverse=True)
    briefs = briefs[:MAX_ENTRIES]

    BRIEFS_FILE.write_text(json.dumps(briefs, indent=2) + "\n", encoding="utf-8")
    print(f"Brief generated: {brief['title']} ({len(brief['markets'])} markets, {len(brief['news'])} news)")


if __name__ == "__main__":
    main()
