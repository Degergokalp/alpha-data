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
from alpha.i18n import localize, slice_report
from mcp_server.server import bind_asgi_app, mcp as alpha_mcp


@asynccontextmanager
async def _lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=20)
    async with alpha_mcp.session_manager.run():
        yield
    await app.state.http.aclose()


_DESCRIPTION = """
Machine-readable US market research for AI agents, from **Alphalyze**.

- **Daily brief**: one cross-report AI synthesis per day (regime, bullets,
  opportunities, risks, today-watch) — widget id `daily_brief`.
- **Research cards**: bilingual AI research cards for the top-500 US stocks by
  market cap, refreshed daily (`/v1/research/stocks`, `ticker=` for one card).
- **Reports**: daily deep-research reports (macro overview, NDX/SPY bias,
  sector rotation, crypto, global daily pulse, SPX/NDX profit zones, AI
  impacts, metals compass, weekly earnings radar, macro alpha big-picture
  cards with a priced-in panel), archived by date; fetch a
  single section with `?section=` ("toc" lists section keys first).
- **Widgets**: live structured snapshots — daily analyst bulletin, sector
  rotation regime, momentum screener, options flow, IV-vs-RV analytics,
  metals dashboard, macro calendar, value screener, top picks, risk
  dashboard, AI impact trackers, signal track record.
- **Niche map**: every niche with ranked players, market share, structure and moat;
  per-ticker valuation bands and machine-measured long-term theses (`/v1/niche/map`,
  `/v1/niche/cards`).
- **Social**: Reddit alpha signal feed, narrative events, extracted market state.
- **News**: curated market news with sentiment and impact tags.
- **Language**: research text is Turkish-first with English translations; pass
  `?lang=tr|en` to collapse bilingual fields into one language.

**MCP**: point any MCP client at `/mcp` (streamable HTTP) for 46 tools over this API.

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


def _lang_param(lang: str | None) -> str | None:
    if lang not in (None, "tr", "en"):
        raise HTTPException(422, "lang must be tr or en")
    return lang


def _report_payload(date: str, type: str, data: dict, section: str | None,
                    lang: str | None) -> dict:
    data = localize(data, lang)
    if section is None:
        return {"date": date, "type": type, "report": data}
    try:
        sliced = slice_report(data if isinstance(data, dict) else {}, section)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"date": date, "type": type, "section": section, "report": sliced}


@app.get("/v1/reports/latest")
async def latest_report(
    type: str = Query(..., description="Report type"),
    section: str | None = Query(None, description="Section key, or 'toc' for keys+titles only"),
    lang: str | None = Query(None, description="tr | en — collapse bilingual fields"),
):
    if type not in config.REPORT_TYPES:
        raise HTTPException(422, f"unknown report type; one of {config.REPORT_TYPES}")
    hit = await store.latest_report(_http(), type)
    if hit is None:
        raise HTTPException(404, f"no {type} report in the last {config.MAX_LOOKBACK_DAYS} days")
    date, data = hit
    return _report_payload(date, type, data, section, _lang_param(lang))


@app.get("/v1/reports/{date}/{type}")
async def report_at(
    date: str,
    type: str,
    section: str | None = Query(None, description="Section key, or 'toc'"),
    lang: str | None = Query(None, description="tr | en"),
):
    if not store.valid_date_folder(date):
        raise HTTPException(422, "date must be DD_MM_YYYY")
    if type not in config.REPORT_TYPES:
        raise HTTPException(422, f"unknown report type; one of {config.REPORT_TYPES}")
    data = await store.fetch_report(_http(), date, type)
    if data is None:
        raise HTTPException(404, f"no {type} report on {date}")
    return _report_payload(date, type, data, section, _lang_param(lang))


# ---------- niche map (Nis Haritasi) ----------

_NIS_YAPI = ("monopol", "duopol", "oligopol", "parcali")
_NIS_BOLGE = ("ucuz", "makul", "pahali", "asiri")
_NIS_DURUM = ("acik", "yasiyor", "gecersiz", "elle")
_NIS_SORT = ("giris_mesafe", "hendek", "pay", "sira")


@app.get("/v1/niche/map")
async def niche_map(
    tema: str | None = Query(None, description="ust_tema substring filter (case-insensitive)"),
    yapi: str | None = Query(None, description="monopol | duopol | oligopol | parcali"),
    nis_id: str | None = Query(None, description="One niche in full by id"),
    limit: int = Query(50, ge=1, le=200),
    lang: str | None = Query(None, description="tr | en — collapse bilingual fields"),
):
    """Nis Haritasi: every niche with ranked players, market share (sourced), structure label,
    CR3 and moat score. Bilingual `_tr/_en` fields; `lang` collapses them."""
    if yapi is not None and yapi not in _NIS_YAPI:
        raise HTTPException(422, f"yapi must be one of {_NIS_YAPI}")
    lang_v = _lang_param(lang)
    data = await store.fetch_nis_harita(_http())
    if data is None:
        raise HTTPException(404, "niche map unavailable")
    data = localize(data, lang_v)
    nisler = data.get("nisler") or []
    if nis_id:
        hit = next((n for n in nisler if n.get("id") == nis_id), None)
        if hit is None:
            raise HTTPException(404, f"unknown nis_id; known: {[n.get('id') for n in nisler][:60]}")
        return {"as_of": data.get("as_of"), "nis": hit}
    if tema:
        needle = tema.lower()
        nisler = [n for n in nisler if needle in str(n.get("ust_tema") or "").lower()]
    if yapi:
        nisler = [n for n in nisler if n.get("yapi") == yapi]
    return {
        "as_of": data.get("as_of"), "generated_at": data.get("generated_at"),
        "hafta_etiketi": data.get("hafta_etiketi"), "gruplar": data.get("gruplar"),
        "istatistik": data.get("istatistik"), "total": len(nisler), "count": min(limit, len(nisler)),
        "nisler": nisler[:limit],
    }


@app.get("/v1/niche/cards")
async def niche_cards(
    ticker: str | None = Query(None, description="One full card by ticker"),
    nis_id: str | None = Query(None),
    bolge: str | None = Query(None, description="ucuz | makul | pahali | asiri"),
    durum: str | None = Query(None, description="acik | yasiyor | gecersiz | elle (machine thesis state)"),
    sort: str = Query("giris_mesafe", description="giris_mesafe | hendek | pay | sira"),
    limit: int = Query(50, ge=1, le=600),
    lang: str | None = Query(None, description="tr | en"),
):
    """Per-ticker niche valuation card (rank/share/moat, machine inputs, 3-level entry band,
    long-term thesis, invalidation measured daily). Without `ticker`: the manifest with filters."""
    lang_v = _lang_param(lang)
    if ticker:
        card = await store.fetch_nis_kart(_http(), ticker)
        if card is None:
            raise HTTPException(404, f"no niche card for {ticker.upper()}")
        return {"as_of": card.get("as_of") or card.get("generated_at"), "card": localize(card, lang_v)}
    if bolge is not None and bolge not in _NIS_BOLGE:
        raise HTTPException(422, f"bolge must be one of {_NIS_BOLGE}")
    if durum is not None and durum not in _NIS_DURUM:
        raise HTTPException(422, f"durum must be one of {_NIS_DURUM}")
    if sort not in _NIS_SORT:
        raise HTTPException(422, f"sort must be one of {_NIS_SORT}")
    idx = await store.fetch_nis_index(_http())
    if idx is None:
        raise HTTPException(404, "niche index unavailable")
    rows = list(idx.get("kartlar") or [])
    if nis_id:
        rows = [r for r in rows if r.get("nis_id") == nis_id or any(n.get("nis_id") == nis_id for n in (r.get("nisler") or []))]
    if bolge:
        rows = [r for r in rows if r.get("bolge") == bolge]
    if durum:
        rows = [r for r in rows if r.get("makine_durum") == durum]
    if sort == "giris_mesafe":
        rows.sort(key=lambda r: (r.get("giris_mesafe_pct") is None, r.get("giris_mesafe_pct") or 0))
    elif sort == "hendek":
        rows.sort(key=lambda r: -(r.get("hendek_skoru_0to100") or 0))
    elif sort == "pay":
        rows.sort(key=lambda r: (r.get("pazar_payi_pct") is None, -(r.get("pazar_payi_pct") or 0)))
    else:
        rows.sort(key=lambda r: (r.get("sira") is None, r.get("sira") or 99))
    rows = [localize(r, lang_v) for r in rows[:limit]]
    return {"as_of": idx.get("as_of"), "generated_at": idx.get("generated_at"), "total": idx.get("count"),
            "count": len(rows), "kartlar": rows}


# ---------- widgets ----------

@app.get("/v1/widgets/{widget_id}")
async def latest_widget(widget_id: str, lang: str | None = Query(None, description="tr | en")):
    """Latest widget JSON by id (walks back up to 7 days)."""
    hit = await store.latest_widget(_http(), widget_id)
    if hit is None:
        raise HTTPException(404, f"widget {widget_id} not found in the last 7 days")
    date, data = hit
    return {"date": date, "id": widget_id, "widget": localize(data, _lang_param(lang))}


@app.get("/v1/widgets/{date}/{widget_id}")
async def widget_at(date: str, widget_id: str, lang: str | None = Query(None)):
    if not store.valid_date_folder(date):
        raise HTTPException(422, "date must be DD_MM_YYYY")
    data = await store.fetch_widget(_http(), date, widget_id)
    if data is None:
        raise HTTPException(404, f"widget {widget_id} not found on {date}")
    return {"date": date, "id": widget_id, "widget": localize(data, _lang_param(lang))}


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
async def stock_research(
    ticker: str | None = Query(None, description="One ticker → full research card"),
    sector: str | None = Query(None, description="Manifest filter: sector substring"),
    rating: str | None = Query(None, description="Manifest filter: strong_buy/buy/hold/avoid"),
    limit: int = Query(50, ge=1, le=526),
    sort: str = Query("score", description="Manifest sort: score | mcap_rank"),
    lang: str | None = Query(None, description="tr | en"),
):
    """Top-500 daily research cards. With `ticker`: the full bilingual card
    (score, thesis, catalysts, financials, competitors, news, key risk,
    analyst target). Without: the manifest (ticker, name, sector, mcap rank,
    score, rating, updated_at) with optional filters — never 500 full cards."""
    lang = _lang_param(lang)
    if ticker:
        card = await store.fetch_research_card(_http(), ticker)
        if card is None:
            raise HTTPException(404, f"no research card for {ticker.upper()}")
        card = localize(card, lang)
        return {"as_of": card.get("generated_at"), "card": card}

    index = await store.fetch_research_index(_http())
    if index is None:
        raise HTTPException(404, "research cards index unavailable")
    rows = index.get("tickers") or []
    if sector:
        s = sector.lower()
        rows = [r for r in rows if s in str(r.get("sector", "")).lower()]
    if rating:
        rows = [r for r in rows if str(r.get("rating", "")) == rating]
    if sort == "score":
        rows = sorted(rows, key=lambda r: r.get("score") or 0, reverse=True)
    else:
        rows = sorted(rows, key=lambda r: (r.get("mcap_rank") is None, r.get("mcap_rank") or 0))
    return {
        "as_of": index.get("as_of"),
        "generated_at": index.get("generated_at"),
        "total": len(rows),
        "count": min(len(rows), limit),
        "tickers": rows[:limit],
    }


@app.get("/v1/macro/calendar")
async def macro_calendar(
    part: str = Query("upcoming", description="upcoming | analysis | events"),
    limit: int = Query(20, ge=1, le=100),
    lang: str | None = Query(None, description="tr | en"),
):
    """US macro calendar from the macro_chart widget — never returns the raw
    3-year price bars. `upcoming`: next high-impact events + AI note.
    `analysis`: 1m/3m/1y AI market commentary. `events`: recent past events
    with actual-vs-forecast, newest first."""
    hit = await store.latest_widget(_http(), "macro_chart")
    if hit is None:
        raise HTTPException(404, "macro_chart widget unavailable")
    date, data = hit
    data = localize(data, _lang_param(lang)) or {}
    ai = data.get("ai_analysis") or {}
    if part == "upcoming":
        return {
            "date": date,
            "upcoming": ai.get("upcoming") or data.get("upcoming") or [],
            "note": ai.get("upcoming_note") or ai.get("upcoming_note_tr")
            or data.get("upcoming_note") or data.get("upcoming_note_tr"),
        }
    if part == "analysis":
        return {"date": date, "horizons": ai.get("horizons") or ai, "generated_at": data.get("generated_at")}
    if part == "events":
        events = data.get("events") or []
        events = sorted(events, key=lambda e: str(e.get("date") or ""), reverse=True)
        return {"date": date, "count": min(len(events), limit), "events": events[:limit]}
    raise HTTPException(422, "part must be upcoming | analysis | events")


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


# ---------- AI stock signals + feed ----------

@app.get("/v1/signals/stocks")
async def stock_signals(
    signal: str | None = Query(None, description="Filter: buy / sell / hold / watch"),
    ticker: str | None = Query(None, description="Filter to one ticker"),
):
    """Per-ticker AI calls (buy/sell/hold/watch) with confidence, score and reasoning."""
    hit = await store.latest_radar_stocks(_http())
    if hit is None:
        raise HTTPException(404, "no stock signals in the archive window")
    date, data = hit
    stocks = data.get("stocks", []) if isinstance(data, dict) else []
    if signal:
        s = signal.lower()
        stocks = [x for x in stocks if str(x.get("signal", "")).lower() == s]
    if ticker:
        t = ticker.upper()
        stocks = [x for x in stocks if str(x.get("symbol", "")).upper() == t]
        if not stocks:
            raise HTTPException(404, f"no signal for {t}")
    return {
        "date": date,
        "generated_at": data.get("generated_at") if isinstance(data, dict) else None,
        "by_signal": data.get("by_signal") if isinstance(data, dict) else None,
        "count": len(stocks),
        "stocks": stocks,
    }


@app.get("/v1/feed/stock")
async def stock_feed():
    """Latest raw stock research feed items produced by the agent."""
    hit = await store.latest_stock_feed(_http())
    if hit is None:
        raise HTTPException(404, "no stock feed in the archive window")
    date, data = hit
    return {"date": date, **(data if isinstance(data, dict) else {"data": data})}


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
        q += f"&tickers=cs.{contains}"
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
        q += f"&tickers_mentioned=cs.{contains}"
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
    research = await store.fetch_research_index(client)
    out["sources"]["research_cards"] = {
        "as_of": research.get("as_of") if research else None,
        "count": research.get("count") if research else None,
        "ok": research is not None,
    }
    nis = await store.fetch_nis_index(client)
    out["sources"]["niche_map"] = {
        "as_of": nis.get("as_of") if nis else None,
        "count": nis.get("count") if nis else None,
        "available": nis is not None,
        "ok": True,  # yeni veri seti; dosya olusana kadar servisi degraded yapmasin
    }
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
