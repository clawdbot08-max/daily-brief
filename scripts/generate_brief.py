#!/usr/bin/env python3
"""Generate the daily morning brief as JSON for the GitHub Pages site.

Pulls: markets (Alpaca), weather (Open-Meteo), geopolitics/tech (RSS), market news (Alpaca).
Writes to data/briefs.json (array of daily entries, newest first, max 30 days).
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

# Alpaca SDK
sys.path.insert(0, "/home/clawdbot/.local/lib/python3.10/site-packages")
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import StockLatestQuoteRequest, StockBarsRequest, NewsRequest
from alpaca.data.timeframe import TimeFrame

REPO_DIR = Path(__file__).resolve().parents[1]
BRIEFS_FILE = REPO_DIR / "data" / "briefs.json"
SECRETS = json.loads(Path("/home/clawdbot/.openclaw/workspace/secrets/alpaca.json").read_text())
MAX_ENTRIES = 30

LOCATION = "Los Angeles, CA"
LATITUDE = 34.05
LONGITUDE = -118.24

SYMBOLS = ["SPY", "DIA", "QQQ"]
SYMBOL_LABELS = {"SPY": "S&P 500 (SPY)", "DIA": "Dow (DIA)", "QQQ": "Nasdaq 100 (QQQ)"}

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
    45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Moderate drizzle",
    55: "Dense drizzle", 61: "Light rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow", 80: "Light showers",
    81: "Moderate showers", 82: "Heavy showers", 95: "Thunderstorm",
}

# --- Alpaca clients ---
alpaca_data = StockHistoricalDataClient(SECRETS["api_key"], SECRETS["secret_key"])
alpaca_news = NewsClient(SECRETS["api_key"], SECRETS["secret_key"])


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={**HEADERS, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="ignore")


# --- Markets (Alpaca) ---
def get_markets() -> list[dict]:
    items = []
    try:
        req = StockLatestQuoteRequest(symbol_or_symbols=SYMBOLS)
        quotes = alpaca_data.get_stock_latest_quote(req)
        # Get previous close via 1-day bars
        bars_req = StockBarsRequest(symbol_or_symbols=SYMBOLS, timeframe=TimeFrame.Day, limit=2)
        bars = alpaca_data.get_stock_bars(bars_req)

        for sym in SYMBOLS:
            label = SYMBOL_LABELS.get(sym, sym)
            q = quotes.get(sym)
            sym_bars = bars.get(sym, [])

            if q and q.ask_price:
                price = float(q.ask_price)
                # Calculate change from previous close
                if len(sym_bars) >= 2:
                    prev_close = float(sym_bars[-2].close)
                    pct = ((price - prev_close) / prev_close) * 100
                    arrow = "▲" if pct > 0 else ("▼" if pct < 0 else "→")
                    items.append({"label": label, "value": f"${price:,.2f}", "change": f"{arrow} {pct:+.2f}%", "direction": "up" if pct > 0 else "down" if pct < 0 else "flat"})
                else:
                    items.append({"label": label, "value": f"${price:,.2f}", "change": "", "direction": "flat"})
            else:
                items.append({"label": label, "value": "unavailable", "change": "", "direction": "flat"})
    except Exception as e:
        for sym in SYMBOLS:
            items.append({"label": SYMBOL_LABELS.get(sym, sym), "value": "unavailable", "change": str(e)[:50], "direction": "flat"})
    return items


# --- Market News (Alpaca) ---
def get_market_news() -> list[dict]:
    try:
        req = NewsRequest(limit=5)
        result = alpaca_news.get_news(req)
        articles = []
        for n in result.data["news"][:5]:
            articles.append({
                "headline": n.headline,
                "source": n.source,
                "symbols": n.symbols[:4] if n.symbols else [],
                "summary": (n.summary[:180] + "..." if n.summary and len(n.summary) > 180 else n.summary or ""),
                "url": n.url,
                "created": n.created_at.isoformat() if n.created_at else "",
            })
        return articles
    except Exception:
        return []


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
        "temp": f"{temp:.0f}°F" if temp is not None else None,
        "feelsLike": f"{feels:.0f}°F" if feels is not None else None,
        "high": f"{high:.0f}°F" if high is not None else None,
        "low": f"{low:.0f}°F" if low is not None else None,
        "precipChance": f"{precip}%" if precip is not None else None,
    }


# --- News (RSS) ---
class TextExtractor(HTMLParser):
    SKIP_TAGS = {"script", "style", "noscript", "svg", "head"}
    def __init__(self):
        super().__init__()
        self.segments: list[str] = []
        self._skip = 0
    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.SKIP_TAGS: self._skip += 1
    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP_TAGS and self._skip > 0: self._skip -= 1
    def handle_data(self, data):
        if self._skip == 0 and data.strip(): self.segments.append(data.strip())


def extract_text(html: str) -> str:
    p = TextExtractor()
    try: p.feed(html)
    except: pass
    return " ".join(p.segments)


def summarize_text(text: str, max_len: int = 180) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out = ""
    for s in sentences:
        if len(out) + len(s) > max_len: break
        out += (" " if out else "") + s
    return out.strip()


def get_feed_items(feed_url: str) -> list[ET.Element]:
    try:
        xml_text = fetch_text(feed_url)
        root = ET.fromstring(xml_text)
        channel = root.find("channel")
        return channel.findall("item") if channel is not None else []
    except: return []


def get_news() -> list[dict]:
    results = []
    for region, url in STATE_FEEDS + [TECH_FEED]:
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
        "marketNews": get_market_news(),
        "weather": get_weather(),
        "news": get_news(),
    }

    BRIEFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        briefs = json.loads(BRIEFS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        briefs = []

    briefs = [b for b in briefs if b.get("date") != today_str]
    briefs.append(brief)
    briefs.sort(key=lambda b: b.get("date", ""), reverse=True)
    briefs = briefs[:MAX_ENTRIES]

    BRIEFS_FILE.write_text(json.dumps(briefs, indent=2) + "\n", encoding="utf-8")
    print(f"Brief generated: {brief['title']} ({len(brief['markets'])} markets, {len(brief.get('marketNews',[]))} articles, {len(brief['news'])} world/tech)")


if __name__ == "__main__":
    main()
