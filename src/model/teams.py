"""Canonical team-name normalization across heterogeneous sources.

Elo, bookmakers, models and squad sources all spell country names differently.
We pick one canonical English name per team and map every alias onto it.
"""
from __future__ import annotations

import re
import unicodedata

# Canonical name -> list of aliases (lowercased match is alias-insensitive).
_ALIASES: dict[str, list[str]] = {
    "USA": ["united states", "united states of america", "us", "usa", "u.s.a.", "u.s."],
    "Mexico": ["méxico"],
    "Canada": [],
    "South Africa": ["rsa"],
    "South Korea": ["korea republic", "korea", "rep. of korea", "republic of korea", "kor"],
    "North Korea": ["korea dpr", "dpr korea"],
    "Iran": ["ir iran", "islamic republic of iran"],
    "Ivory Coast": ["côte d'ivoire", "cote d'ivoire", "cote d ivoire", "ivory coast"],
    "Cape Verde": ["cabo verde"],
    "Czechia": ["czech republic"],
    "Bosnia and Herzegovina": ["bosnia", "bosnia & herzegovina", "bih"],
    "Saudi Arabia": ["ksa"],
    "United Arab Emirates": ["uae"],
    "Curacao": ["curaçao"],
    "DR Congo": ["democratic republic of the congo", "congo dr", "dr congo", "drc"],
    "Republic of Ireland": ["ireland", "rep. of ireland"],
    "New Zealand": ["nz"],
    "Trinidad and Tobago": ["trinidad & tobago", "trinidad"],
    "Turkey": ["türkiye", "turkiye"],
}

# Build reverse lookup.
_REVERSE: dict[str, str] = {}
for canon, aliases in _ALIASES.items():
    _REVERSE[canon.lower()] = canon
    for a in aliases:
        _REVERSE[a.lower()] = canon


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_team(name: str) -> str:
    """Return the canonical team name for any reasonable spelling."""
    if name is None:
        return ""
    key = _strip_accents(str(name)).strip().lower()
    key = re.sub(r"\s+", " ", key)
    if key in _REVERSE:
        return _REVERSE[key]
    # Try without accents against canonical names directly.
    for canon in _ALIASES:
        if _strip_accents(canon).lower() == key:
            return canon
    # Title-case fallback, preserving canonical spelling if we know it.
    return str(name).strip()


def register_canonical(names: list[str]) -> None:
    """Register the authoritative 48-team list so its spellings win as canonical."""
    for n in names:
        _REVERSE.setdefault(_strip_accents(n).strip().lower(), n)
