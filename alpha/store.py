"""Data access: Supabase storage bucket (reports/widgets) + PostgREST tables.

Everything is fetched over public endpoints with a small in-memory TTL cache,
mirroring the frontend's alpha-api.ts logic (date folders DD_MM_YYYY, walk-back
"latest" resolution).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from alpha import config

_cache: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str) -> Any | None:
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < config.CACHE_TTL_SEC:
        return hit[1]
    return None


def _cache_put(key: str, value: Any) -> None:
    _cache[key] = (time.time(), value)


# ---------- date folders ----------

def date_folder(dt: datetime) -> str:
    return dt.strftime("%d_%m_%Y")


def recent_date_folders(n: int) -> list[str]:
    today = datetime.now(timezone.utc)
    return [date_folder(today - timedelta(days=i)) for i in range(n)]


def valid_date_folder(s: str) -> bool:
    try:
        datetime.strptime(s, "%d_%m_%Y")
        return True
    except ValueError:
        return False


# ---------- bucket ----------

async def _download_json(client: httpx.AsyncClient, path: str) -> Any | None:
    cached = _cache_get(f"obj:{path}")
    if cached is not None:
        return cached if cached != "__miss__" else None
    try:
        r = await client.get(f"{config.STORAGE_URL}/{path}")
        if r.status_code != 200:
            _cache_put(f"obj:{path}", "__miss__")
            return None
        data = r.json()
        _cache_put(f"obj:{path}", data)
        return data
    except (httpx.HTTPError, ValueError):
        return None


async def fetch_report(client: httpx.AsyncClient, date: str, report_type: str) -> Any | None:
    return await _download_json(client, f"{date}/reports/{report_type}/{report_type}_report.json")


async def fetch_widget(client: httpx.AsyncClient, date: str, widget_id: str) -> Any | None:
    return await _download_json(client, f"{date}/widgets/{widget_id}.json")


async def latest_report(
    client: httpx.AsyncClient, report_type: str, max_back: Optional[int] = None
) -> tuple[str, Any] | None:
    for date in recent_date_folders(max_back or config.MAX_LOOKBACK_DAYS):
        data = await fetch_report(client, date, report_type)
        if data is not None:
            return date, data
    data = await fetch_report(client, config.ARCHIVE_DATE, report_type)
    if data is not None:
        return config.ARCHIVE_DATE, data
    return None


async def latest_widget(
    client: httpx.AsyncClient, widget_id: str, max_back: int = 7
) -> tuple[str, Any] | None:
    for date in recent_date_folders(max_back):
        data = await fetch_widget(client, date, widget_id)
        if data is not None:
            return date, data
    data = await fetch_widget(client, config.ARCHIVE_DATE, widget_id)
    if data is not None:
        return config.ARCHIVE_DATE, data
    return None


async def fetch_radar_stocks(client: httpx.AsyncClient, date: str) -> Any | None:
    return await _download_json(client, f"{date}/radar_stocks/stock.json")


async def latest_radar_stocks(
    client: httpx.AsyncClient, max_back: int = 7
) -> tuple[str, Any] | None:
    for date in recent_date_folders(max_back):
        data = await fetch_radar_stocks(client, date)
        if data and data.get("stocks"):
            return date, data
    data = await fetch_radar_stocks(client, config.ARCHIVE_DATE)
    if data is not None:
        return config.ARCHIVE_DATE, data
    return None


async def fetch_stock_feed(client: httpx.AsyncClient, date: str) -> Any | None:
    return await _download_json(client, f"{date}/feed/stock/feed.json")


async def latest_stock_feed(
    client: httpx.AsyncClient, max_back: int = 7
) -> tuple[str, Any] | None:
    for date in recent_date_folders(max_back):
        data = await fetch_stock_feed(client, date)
        if data and data.get("feeds"):
            return date, data
    data = await fetch_stock_feed(client, config.ARCHIVE_DATE)
    if data is not None:
        return config.ARCHIVE_DATE, data
    return None


async def fetch_research_index(client: httpx.AsyncClient) -> Any | None:
    """Top-500 research cards manifest (stable path, refreshed daily)."""
    return await _download_json(client, "research_cards/index.json")


async def fetch_research_card(client: httpx.AsyncClient, ticker: str) -> Any | None:
    """One bilingual top-500 research card by ticker (stable path)."""
    return await _download_json(client, f"research_cards/{ticker.upper()}.json")


async def fetch_nis_harita(client: httpx.AsyncClient) -> Any | None:
    """Nis Haritasi: all niches with ranked players, structure and moat (stable path, weekly)."""
    return await _download_json(client, "nis/harita.json")


async def fetch_nis_index(client: httpx.AsyncClient) -> Any | None:
    """Niche card manifest, one row per public player (stable path, refreshed daily)."""
    return await _download_json(client, "nis/index.json")


async def fetch_nis_kart(client: httpx.AsyncClient, ticker: str) -> Any | None:
    """One bilingual niche card: valuation table, thesis, invalidation, daily measurement."""
    return await _download_json(client, f"nis/kartlar/{ticker.upper()}.json")


async def available_dates(client: httpx.AsyncClient, max_back: Optional[int] = None) -> list[str]:
    """Dates (newest first) that have at least one known report."""
    cached = _cache_get("dates")
    if cached is not None:
        return cached
    dates: list[str] = []
    for date in recent_date_folders(max_back or config.MAX_LOOKBACK_DAYS):
        for marker in ("macro_overview", "earnings_radar"):
            if await fetch_report(client, date, marker) is not None:
                dates.append(date)
                break
    if config.ARCHIVE_DATE not in dates:
        if await fetch_report(client, config.ARCHIVE_DATE, "macro_overview") is not None:
            dates.append(config.ARCHIVE_DATE)
    _cache_put("dates", dates)
    return dates


# ---------- tables (PostgREST, anon) ----------

async def rest_get(client: httpx.AsyncClient, path_and_query: str) -> Any:
    cached = _cache_get(f"rest:{path_and_query}")
    if cached is not None:
        return cached
    r = await client.get(
        f"{config.REST_URL}/{path_and_query}",
        headers={
            "apikey": config.SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {config.SUPABASE_ANON_KEY}",
        },
    )
    r.raise_for_status()
    data = r.json()
    _cache_put(f"rest:{path_and_query}", data)
    return data
