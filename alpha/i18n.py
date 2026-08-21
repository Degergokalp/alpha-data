"""Language selection for bilingual research payloads.

Two conventions exist in the bucket:
- Widgets carry `field_tr` with translated `field_en` siblings (plus
  `field_en_translation_hash` bookkeeping) added by the translation worker.
- Reports keep their Turkish structure and carry a flat `i18n_en` map keyed
  by dotted path with array indices ("sections.macro_data.today[0].indicator").

`localize(data, lang)` resolves both into a single-language payload: `_tr/_en`
pairs collapse to one unsuffixed field, report i18n maps are applied in place
(lang="en") and all translation bookkeeping is stripped. `lang=None` returns
the data untouched so existing raw consumers keep the bilingual shape.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Optional

_META_KEYS = {"i18n_en", "i18n_en_hash", "translated_at"}
_SEGMENT_RE = re.compile(r"([^\[]+)((?:\[\d+\])*)$")
_INDEX_RE = re.compile(r"\[(\d+)\]")


def _apply_dotted(root: Any, path: str, value: Any) -> None:
    """Set `value` at a dotted path with optional [N] indices; silently skip
    paths that no longer resolve (report structure may have drifted)."""
    node = root
    segments = path.split(".")
    for i, seg in enumerate(segments):
        m = _SEGMENT_RE.match(seg)
        if not m:
            return
        name, indices = m.group(1), _INDEX_RE.findall(m.group(2))
        last = i == len(segments) - 1
        if not isinstance(node, dict) or name not in node:
            return
        if last and not indices:
            node[name] = value
            return
        node = node[name]
        for j, idx in enumerate(indices):
            k = int(idx)
            if not isinstance(node, list) or k >= len(node):
                return
            if last and j == len(indices) - 1:
                node[k] = value
                return
            node = node[k]


def _collapse(node: Any, lang: str) -> Any:
    """Collapse `_tr`/`_en` pairs into one unsuffixed field, preferring the
    requested language and falling back to the other. Strips hash keys."""
    if isinstance(node, list):
        return [_collapse(v, lang) for v in node]
    if not isinstance(node, dict):
        return node

    out: dict = {}
    for key, val in node.items():
        if key.endswith("_translation_hash") or key in _META_KEYS:
            continue
        if key.endswith(("_tr", "_en")):
            base = key[:-3]
            if base in node:
                # Nadir çakışma: hem `field` hem `field_tr` var — suffixli aynen kalır
                out[key] = _collapse(val, lang)
                continue
            if base in out:
                continue  # çiftin diğer yarısı zaten işlendi
            preferred = node.get(f"{base}_{lang}")
            fallback = node.get(f"{base}_tr") if lang == "en" else node.get(f"{base}_en")
            chosen = preferred if preferred not in (None, "") else fallback
            out[base] = _collapse(chosen, lang)
            continue
        out[key] = _collapse(val, lang)
    return out


def localize(data: Any, lang: Optional[str]) -> Any:
    """Return `data` in one language. `lang=None` → untouched bilingual raw.

    The TTL cache in alpha.store returns shared objects, so this always
    deepcopies before mutating.
    """
    if lang not in ("tr", "en") or data is None:
        return data
    data = copy.deepcopy(data)
    if isinstance(data, dict) and isinstance(data.get("i18n_en"), dict):
        if lang == "en":
            for path, value in data["i18n_en"].items():
                _apply_dotted(data, str(path), value)
        for key in _META_KEYS:
            data.pop(key, None)
    return _collapse(data, lang)


def slice_report(report: dict, section: Optional[str]) -> Any:
    """Slice a report: None → full; "toc" → section keys+titles plus
    non-standard top-level parts; a key → that section (checked in `sections`
    first, then top-level). Raises KeyError listing available keys."""
    if section is None:
        return report
    sections = report.get("sections") if isinstance(report.get("sections"), dict) else {}
    std_keys = {"headline", "as_of", "intro", "sections", "footer", "report_type",
                "created_at", "references", "generated_at"}
    extra_keys = [k for k in report if k not in std_keys and k not in _META_KEYS]
    if section == "toc":
        return {
            "headline": report.get("headline"),
            "as_of": report.get("as_of"),
            "sections": [
                {"key": k, "title": (v or {}).get("title") if isinstance(v, dict) else None}
                for k, v in sections.items()
            ],
            "extra_keys": extra_keys,
        }
    if section in sections:
        return {"key": section, **(sections[section] if isinstance(sections[section], dict)
                                   else {"content": sections[section]})}
    if section in report:
        return {"key": section, "content": report[section]}
    raise KeyError(f"unknown section '{section}'; sections: {list(sections.keys())}, "
                   f"extra: {extra_keys}, or 'toc'")
