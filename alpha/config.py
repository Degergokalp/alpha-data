"""Alphalyze data service configuration.

All reads are public: the Supabase `reports` storage bucket (public objects)
and anon-readable tables. The anon key is a publishable key by design.
"""

import os

SUPABASE_URL = os.environ.get(
    "ALPHA_SUPABASE_URL", "https://bvggujwxalckfrrzjbwg.supabase.co"
).rstrip("/")

SUPABASE_ANON_KEY = os.environ.get(
    "ALPHA_SUPABASE_ANON_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJ2Z2d1and4YWxja2ZycnpqYndnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTk5MTMwNTAsImV4cCI6MjA3NTQ4OTA1MH0."
    "yB6mCYc1T40bQutRdwQoi1SRvA4C-JD5yLeeNZd036E",
)

STORAGE_URL = f"{SUPABASE_URL}/storage/v1/object/public/reports"
REST_URL = f"{SUPABASE_URL}/rest/v1"

REPORT_TYPES = [
    "macro_overview",
    "nx_spy_bias",
    "hood_stocks",
    "sector_rotation",
    "earnings_radar",
]

WIDGET_IDS = [
    "sector_rotation_live",
    "momentum_screener",
    "options_flow_heatmap",
    "options_analytics",
    "risk_reward_matrix",
    "options_strategy_cards",
    "catalyst_timeline",
]

# How many days back the "latest" resolution walks.
MAX_LOOKBACK_DAYS = int(os.environ.get("ALPHA_MAX_LOOKBACK_DAYS", "14"))

CACHE_TTL_SEC = int(os.environ.get("ALPHA_CACHE_TTL_SEC", "300"))
