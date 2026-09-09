"""
Country matching from publicly published store text.

This module never infers country from Shopify CDN IPs, urlscan
scanner location, or a myshopify.com hostname.
"""

from __future__ import annotations

COUNTRY_CHOICES: list[tuple[str, str]] = [
    ("NG", "Nigeria"),
    ("US", "United States"),
    ("GB", "United Kingdom"),
    ("CA", "Canada"),
    ("AU", "Australia"),
    ("GH", "Ghana"),
    ("KE", "Kenya"),
    ("ZA", "South Africa"),
    ("IE", "Ireland"),
    ("IN", "India"),
    ("DE", "Germany"),
    ("FR", "France"),
    ("NL", "Netherlands"),
    ("ES", "Spain"),
    ("IT", "Italy"),
    ("SE", "Sweden"),
    ("NO", "Norway"),
    ("DK", "Denmark"),
    ("NZ", "New Zealand"),
    ("SG", "Singapore"),
    ("AE", "United Arab Emirates"),
    ("SA", "Saudi Arabia"),
    ("PH", "Philippines"),
    ("MY", "Malaysia"),
    ("PK", "Pakistan"),
    ("BD", "Bangladesh"),
    ("EG", "Egypt"),
    ("MA", "Morocco"),
    ("TZ", "Tanzania"),
    ("UG", "Uganda"),
    ("RW", "Rwanda"),
    ("BR", "Brazil"),
    ("MX", "Mexico"),
    ("JP", "Japan"),
    ("KR", "South Korea"),
    ("PL", "Poland"),
    ("PT", "Portugal"),
    ("BE", "Belgium"),
    ("CH", "Switzerland"),
    ("AT", "Austria"),
]

_ALIASES = {
    "nigeria": "NG",
    "nigerian": "NG",
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "u.s.": "US",
    "u.s.a.": "US",
    "america": "US",
    "united kingdom": "GB",
    "great britain": "GB",
    "britain": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "uk": "GB",
    "u.k.": "GB",
    "canada": "CA",
    "australian": "AU",
    "australia": "AU",
    "ghana": "GH",
    "kenya": "KE",
    "south africa": "ZA",
    "rsa": "ZA",
    "ireland": "IE",
    "india": "IN",
    "germany": "DE",
    "deutschland": "DE",
    "france": "FR",
    "netherlands": "NL",
    "holland": "NL",
    "spain": "ES",
    "italy": "IT",
    "sweden": "SE",
    "norway": "NO",
    "denmark": "DK",
    "new zealand": "NZ",
    "singapore": "SG",
    "united arab emirates": "AE",
    "uae": "AE",
    "saudi arabia": "SA",
    "ksa": "SA",
    "philippines": "PH",
    "malaysia": "MY",
    "pakistan": "PK",
    "bangladesh": "BD",
    "egypt": "EG",
    "morocco": "MA",
    "tanzania": "TZ",
    "uganda": "UG",
    "rwanda": "RW",
    "brazil": "BR",
    "mexico": "MX",
    "japan": "JP",
    "south korea": "KR",
    "korea": "KR",
    "poland": "PL",
    "portugal": "PT",
    "belgium": "BE",
    "switzerland": "CH",
    "austria": "AT",
}

for code, name in COUNTRY_CHOICES:
    _ALIASES[code.lower()] = code
    _ALIASES[name.lower()] = code


def country_label(code: str) -> str:
    for item, name in COUNTRY_CHOICES:
        if item == code:
            return f"{name} ({code})"
    return code


def normalize_country(text: str) -> str:
    """Return an ISO country code when the published text is clear."""
    raw = " ".join((text or "").strip().lower().replace("_", " ").split())
    if not raw:
        return ""
    if raw in _ALIASES:
        return _ALIASES[raw]
    if "-" in raw or "/" in raw:
        parts = raw.replace("/", "-").split("-")
        prefix = parts[0].strip()
        suffix = parts[-1].strip()
        if prefix in _ALIASES:
            return _ALIASES[prefix]
        if suffix in _ALIASES:
            return _ALIASES[suffix]
    for alias, code in _ALIASES.items():
        if len(alias) > 3 and alias in raw:
            return code
    return ""


def country_allows(
    published: str,
    target_codes: list[str],
    keep_unknown: bool,
) -> tuple[bool, str]:
    """
    Decide whether a store may be saved for the selected countries.

    Returns (allowed, reason). reason is empty when allowed.
    """
    wanted = {item.upper() for item in target_codes if item}
    if not wanted:
        return True, ""
    code = normalize_country(published)
    if not code:
        if keep_unknown:
            return True, ""
        return False, "country_unknown"
    if code in wanted:
        return True, ""
    return False, "country_mismatch"
