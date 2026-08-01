"""
Alphalyze Data API v1 — machine-readable US market research for humans and AI
agents. REST under /v1/..., MCP (streamable HTTP) under /mcp.

Data sources: the Supabase `reports` bucket written daily by the research
agent (deep-research reports + widget JSONs) and anon-readable Supabase tables
(Reddit alpha signals, narrative events, market state, news feed).
"""

import json
import urllib.parse
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from alpha import config, store
from mcp_server.server import bind_asgi_app, mcp as alpha_mcp


@asynccontextmanager
async def _lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=20)
    async with alpha_mcp.session_manager.run():
        yield
    await app.state.http.aclose()


_DESCRIPTION = """
Machine-readable US market research for AI agents, from **Alphalyze**.

- **Reports**: daily deep-research reports (macro overview, NDX/SPY bias,
  per-ticker stock cards, sector rotation, weekly earnings radar), archived by date.
- **Widgets**: live structured snapshots — sector rotation regime, momentum
  screener, options flow, IV-vs-RV analytics, risk/reward matrix.
- **Social**: Reddit alpha signal feed, narrative events, extracted market state.
- **News**: curated market news with sentiment and impact tags.

**MCP**: point any MCP client at `/mcp` (streamable HTTP) for 12 tools over this API.

Free during beta, no API key. AI-generated research: may be wrong, not financial advice.
"""

app = FastAPI(
    title="Alphalyze Data API",
    version="1.0.0",
    description=_DESCRIPTION,
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _http(request_app: FastAPI = app) -> httpx.AsyncClient:
    return request_app.state.http


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "alpha-data", "mcp": "/mcp", "docs": "/docs"}


# ---------- reports ----------

@app.get("/v1/reports/dates")
async def report_dates():
    """Date folders (DD_MM_YYYY, newest first) that have reports."""
    dates = await store.available_dates(_http())
    return {"dates": dates, "report_types": config.REPORT_TYPES}


@app.get("/v1/reports/latest")
async def latest_report(type: str = Query(..., description="Report type")):
    if type not in config.REPORT_TYPES:
        raise HTTPException(422, f"unknown report type; one of {config.REPORT_TYPES}")
    hit = await store.latest_report(_http(), type)
    if hit is None:
        raise HTTPException(404, f"no {type} report in the last {config.MAX_LOOKBACK_DAYS} days")
    date, data = hit
    return {"date": date, "type": type, "report": data}


@app.get("/v1/reports/{date}/{type}")
async def report_at(date: str, type: str):
    if not store.valid_date_folder(date):
        raise HTTPException(422, "date must be DD_MM_YYYY")
    if type not in config.REPORT_TYPES:
        raise HTTPException(422, f"unknown report type; one of {config.REPORT_TYPES}")
    data = await store.fetch_report(_http(), date, type)
    if data is None:
        raise HTTPException(404, f"no {type} report on {date}")
    return {"date": date, "type": type, "report": data}


# ---------- widgets ----------

@app.get("/v1/widgets/{widget_id}")
async def latest_widget(widget_id: str):
    """Latest widget JSON by id (walks back up to 7 days)."""
    hit = await store.latest_widget(_http(), widget_id)
    if hit is None:
        raise HTTPException(404, f"widget {widget_id} not found in the last 7 days")
    date, data = hit
    return {"date": date, "id": widget_id, "widget": data}


@app.get("/v1/widgets/{date}/{widget_id}")
async def widget_at(date: str, widget_id: str):
    if not store.valid_date_folder(date):
        raise HTTPException(422, "date must be DD_MM_YYYY")
    data = await store.fetch_widget(_http(), date, widget_id)
    if data is None:
        raise HTTPException(404, f"widget {widget_id} not found on {date}")
    return {"date": date, "id": widget_id, "widget": data}


# ---------- research shortcuts ----------

@app.get("/v1/earnings/radar")
async def earnings_radar():
    """Latest weekly earnings radar (Monday cadence)."""
    hit = await store.latest_report(_http(), "earnings_radar")
    if hit is None:
        raise HTTPException(404, "no earnings_radar report in the archive window")
    date, data = hit
    return {"date": date, "report": data}


@app.get("/v1/research/stocks")
async def stock_research(ticker: str | None = Query(None, description="Filter to one ticker")):
    """Latest per-ticker research cards (score, thesis, catalysts, risks)."""
    hit = await store.latest_report(_http(), "hood_stocks")
    if hit is None:
        raise HTTPException(404, "no stock research report in the archive window")
    date, data = hit
    cards = data.get("cards", []) if isinstance(data, dict) else []
    if ticker:
        t = ticker.upper()
        cards = [c for c in cards if str(c.get("ticker", "")).upper() == t]
        if not cards:
            raise HTTPException(404, f"no research card for {t}")
    return {
        "date": date,
        "as_of": data.get("as_of") if isinstance(data, dict) else None,
        "macro_note": data.get("macro_note") if isinstance(data, dict) else None,
        "cards": cards,
    }


@app.get("/v1/market/regime")
async def market_regime():
    """Live sector rotation: regime label, 11-sector scoreboard, themes."""
    hit = await store.latest_widget(_http(), "sector_rotation_live")
    if hit is None:
        raise HTTPException(404, "sector_rotation_live widget unavailable")
    date, data = hit
    return {"date": date, **(data if isinstance(data, dict) else {"data": data})}


@app.get("/v1/market/momentum")
async def market_momentum(limit: int = Query(20, ge=1, le=50)):
    """Top 7-day gainers and losers with momentum classification."""
    hit = await store.latest_widget(_http(), "momentum_screener")
    if hit is None:
        raise HTTPException(404, "momentum_screener widget unavailable")
    date, data = hit
    return {
        "date": date,
        "generated_at": data.get("generated_at"),
        "gainers": (data.get("gainers") or [])[:limit],
        "losers": (data.get("losers") or [])[:limit],
    }


# ---------- social ----------

@app.get("/v1/social/signals")
async def social_signals(
    ticker: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """Reddit alpha signal feed, scored and de-noised (newest first)."""
    q = (
        "reddit_alpha_signal_feed?select=id,signal_type,tickers,alpha_score,summary,"
        f"subreddits,evidence_score,generated_at&order=generated_at.desc&limit={limit}"
    )
    if ticker:
        contains = urllib.parse.quote(json.dumps([ticker.upper()]))
        q += f"&tickers=cs={contains}"
    rows = await store.rest_get(_http(), q)
    return {"count": len(rows), "signals": rows}


@app.get("/v1/social/market-state")
async def social_market_state(limit: int = Query(12, ge=1, le=50)):
    """Reddit-derived market state history (newest first)."""
    rows = await store.rest_get(
        _http(),
        "reddit_alpha_market_state?select=id,market_state,confidence,summary,watch,"
        f"signal_count,generated_at&order=generated_at.desc&limit={limit}",
    )
    return {"count": len(rows), "states": rows}


@app.get("/v1/social/narratives")
async def social_narratives(
    ticker: str | None = Query(None),
    limit: int = Query(50, ge=1, le=300),
):
    """Classified narrative events from subreddit ingest, noise filtered."""
    q = (
        "reddit_alpha_narrative_events?select=id,signal_type,narrative_tags,"
        "tickers_mentioned,options_language,evidence_score,summary,subreddit,created_at"
        f"&is_noise=eq.false&order=created_at.desc&limit={limit}"
    )
    if ticker:
        contains = urllib.parse.quote(json.dumps([ticker.upper()]))
        q += f"&tickers_mentioned=cs={contains}"
    rows = await store.rest_get(_http(), q)
    return {"count": len(rows), "events": rows}


@app.get("/v1/news/feed")
async def news_feed(limit: int = Query(50, ge=1, le=200)):
    """Curated market news feed with sentiment and impact tags."""
    rows = await store.rest_get(
        _http(),
        "news_feed?select=id,created_at,published_at,source_channel,original_text,"
        f"translation,category,impact,sentiment,reason&order=created_at.desc&limit={limit}",
    )
    return {"count": len(rows), "items": rows}


# ---------- status ----------

@app.get("/v1/status")
async def status():
    """Data freshness per source."""
    client = _http()
    out: dict = {"service": "alpha-data", "sources": {}}

    regime = await store.latest_widget(client, "sector_rotation_live")
    out["sources"]["sector_rotation_live"] = {
        "date": regime[0] if regime else None,
        "generated_at": regime[1].get("generated_at") if regime else None,
        "ok": regime is not None,
    }
    macro = await store.latest_report(client, "macro_overview")
    out["sources"]["macro_overview"] = {"date": macro[0] if macro else None, "ok": macro is not None}
    try:
        states = await store.rest_get(
            client,
            "reddit_alpha_market_state?select=generated_at&order=generated_at.desc&limit=1",
        )
        out["sources"]["reddit_alpha"] = {
            "last_state_at": states[0]["generated_at"] if states else None,
            "ok": bool(states),
        }
    except httpx.HTTPError:
        out["sources"]["reddit_alpha"] = {"ok": False}
    out["status"] = "ok" if all(s.get("ok") for s in out["sources"].values()) else "degraded"
    return out


# Mounted last: the FastMCP app takes over the /mcp path; tools call this API
# in-process via ASGITransport (no network egress).
bind_asgi_app(app)
app.mount("/", alpha_mcp.streamable_http_app())
