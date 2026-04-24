"""Zero-trust URL sanitisation — clean and validate every URL before it enters the pipeline."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

# Schemes we accept; anything else is rejected
_ALLOWED_SCHEMES = {"http", "https"}

# Fragment, tracking params, and junk to strip
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "gclsrc", "dclid", "yclid", "twclid", "igshid",
    "ref", "referrer", "source", "mc_eid", "mc_cid", "_ga", "_gl",
    "msclkid", "zanpid", "origin",
}

# Patterns that suggest non-page resources (skip during crawl)
_SKIP_EXTENSIONS = re.compile(
    r"\.(pdf|zip|gz|tar|rar|7z|exe|dmg|pkg|deb|rpm"
    r"|jpg|jpeg|png|gif|svg|webp|ico|bmp|tiff"
    r"|mp3|mp4|mpeg|avi|mov|wmv|flv|ogg|webm"
    r"|css|js|json|xml|rss|atom|woff|woff2|ttf|eot"
    r"|csv|xls|xlsx|doc|docx|ppt|pptx)$",
    re.IGNORECASE,
)


def sanitise(url: str) -> str | None:
    """Clean and validate a single URL.

    - Strips whitespace and control characters
    - Ensures scheme is http or https (adds https:// if missing)
    - Removes URL fragments (#…)
    - Strips known tracking query parameters
    - Percent-encodes unsafe characters in the path
    - Returns None if the URL is irreparably invalid
    """
    if not url:
        return None

    # Strip whitespace and control characters
    url = re.sub(r"[\x00-\x1f\x7f\s]", "", url).strip()
    if not url:
        return None

    # Add scheme if missing (treat bare domain as https)
    if not url.startswith(("http://", "https://", "//")):
        url = "https://" + url
    elif url.startswith("//"):
        url = "https:" + url

    try:
        parsed = urlparse(url)
    except Exception:
        return None

    # Reject non-http(s) schemes
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return None

    # Require a netloc (hostname)
    netloc = parsed.netloc.strip()
    if not netloc or "." not in netloc.split(":")[0]:
        return None

    # Strip fragment (never sent to server; useless for crawling)
    fragment = ""

    # Strip tracking query params, keep the rest in stable sorted order
    qs_pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                if k.lower() not in _TRACKING_PARAMS]
    query = urlencode(qs_pairs)

    # Sanitise path: percent-encode anything outside the safe set
    path = quote(parsed.path, safe="/:@!$&'()*+,;=-._~%")

    cleaned = urlunparse((
        parsed.scheme.lower(),
        netloc.lower(),
        path,
        parsed.params,
        query,
        fragment,
    ))

    return cleaned


def sanitise_many(urls: list[str]) -> list[str]:
    """Sanitise a list of URLs, dropping any that are invalid. Deduplicates."""
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        clean = sanitise(url)
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def is_page_url(url: str) -> bool:
    """Return False for URLs that point to non-HTML resources (images, PDFs, etc.)."""
    path = urlparse(url).path
    return not _SKIP_EXTENSIONS.search(path)
