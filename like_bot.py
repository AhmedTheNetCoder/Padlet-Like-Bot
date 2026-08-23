#!/usr/bin/env python3
"""
Padlet Reaction Engine — place Padlet emoji reactions over plain HTTP.

Clicking a reaction in the Padlet web app ends up firing one REST call:

    POST https://padlet.com/api/7/reactions
    {"wish_id": <numeric id>, "value": "2764", "reaction_type": "emoji"}

A Selenium/Chrome implementation spends ~40s launching a browser and rendering
a heavy single-page app just to trigger that one request. This module skips the
browser entirely:

    1. GET the post page once  -> anonymous session cookies + CSRF token
    2. resolve the post's numeric id from the hashid in the URL
    3. POST the reaction
    4. (optional) PATCH the session user to attach a display name
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

__version__ = "1.0.0"

# --- constants ---------------------------------------------------------------

PLACEHOLDER_URL = "https://padlet.com/<user>/<board>/wish/<hashid>"

DEFAULT_EMOJI = "2764"
DEFAULT_COUNT = 3
DEFAULT_CONCURRENCY = 12
DEFAULT_TIMEOUT = 20

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

API_ORIGIN = "https://padlet.com"
WISH_ENDPOINT = API_ORIGIN + "/api/9/wishes/{hashid}"
REACTIONS_ENDPOINT = API_ORIGIN + "/api/7/reactions"
SESSION_USER_ENDPOINT = API_ORIGIN + "/api/1/session/users/{user_id}"

# Reaction values are Unicode code points in hex, matching Padlet's icon set.
EMOJI_ALIASES = {
    "heart": "2764",  # ❤️
    "thumbsup": "1f44d",  # 👍
    "joy": "1f602",  # 😂
    "party": "1f973",  # 🥳
    "grin": "1f606",  # 😆
}

CSRF_RE = re.compile(r'name="csrf-token"\s+content="([^"]+)"')

CONFIG_HELP = (
    "Set a target with --url, or export PADLET_URL:\n"
    '  PADLET_URL="https://padlet.com/<user>/<board>/wish/<hashid>"'
)


# --- errors ------------------------------------------------------------------


class ConfigError(ValueError):
    """The run was misconfigured — bad URL, unknown emoji, etc."""


class ReactionError(RuntimeError):
    """The reaction flow failed against the live site."""


class CsrfTokenNotFound(ReactionError):
    """The CSRF token was missing from the page HTML."""


# --- pure helpers (no network, unit-tested) ----------------------------------


def validate_url(url: str) -> str:
    """Return `url` unchanged if it is a usable Padlet post link.

    Raises ConfigError with an actionable message otherwise.
    """
    if not url or url == PLACEHOLDER_URL or "<" in url:
        raise ConfigError("No target post configured.")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ConfigError(f"URL must start with http:// or https:// — got {url!r}.")
    if not parsed.netloc.endswith("padlet.com"):
        raise ConfigError(f"URL must point at padlet.com — got {parsed.netloc!r}.")
    if "/wish/" not in parsed.path:
        raise ConfigError(
            "URL must be a single post link containing '/wish/<hashid>' — "
            "open the post and copy the link from your browser."
        )
    return url


def extract_hashid(url: str) -> str:
    """Pull the public hashid out of a `.../wish/<hashid>` URL."""
    hashid = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    if not hashid:
        raise ConfigError(f"Could not read a post hashid from {url!r}.")
    return hashid


def extract_csrf(html: str) -> str:
    """Pull the Rails CSRF token out of the page HTML."""
    match = CSRF_RE.search(html)
    if not match:
        raise CsrfTokenNotFound(
            "Could not find CSRF token — page layout changed, or the request was blocked."
        )
    return match.group(1)


def resolve_emoji(value: str) -> str:
    """Accept either a hex code point ('1f44d') or an alias ('thumbsup')."""
    value = value.strip().lower()
    if value in EMOJI_ALIASES:
        return EMOJI_ALIASES[value]
    if re.fullmatch(r"[0-9a-f]{4,6}", value):
        return value
    raise ConfigError(
        f"Unknown emoji {value!r}. Use a hex code point (e.g. 1f44d) or one of: "
        + ", ".join(sorted(EMOJI_ALIASES))
    )


def build_reaction_payload(wish_id: int, emoji: str) -> dict:
    """The exact JSON body the web app sends when you click a reaction."""
    return {"wish_id": wish_id, "value": emoji, "reaction_type": "emoji"}


def build_api_headers(csrf: str, referer: str) -> dict:
    """Mirror the request context the web app attaches to its API calls."""
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-CSRF-Token": csrf,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": referer,
        "Origin": API_ORIGIN,
    }


def worker_count(concurrency: int, count: int) -> int:
    """Never spin up more workers than there is work to do."""
    return max(1, min(concurrency, count))


# --- configuration -----------------------------------------------------------


@dataclass(frozen=True)
class Config:
    """One fully-resolved run configuration."""

    url: str
    emoji: str = DEFAULT_EMOJI
    name: str | None = None
    timeout: int = DEFAULT_TIMEOUT
    dry_run: bool = False


# --- the flow ----------------------------------------------------------------


def place_reaction(cfg: Config, session: requests.Session | None = None) -> bool:
    """Place one reaction on the configured post. Returns True on success.

    With `cfg.dry_run`, every read step still runs — session, CSRF token, id
    resolution — but the write is printed instead of sent.
    """
    started = time.perf_counter()
    s = session or requests.Session()
    s.headers["User-Agent"] = USER_AGENT

    # 1) Load the page once: this hands us the anonymous session cookies
    #    (ww_s, ww_d, ...) and the Rails CSRF token the API requires.
    page = s.get(cfg.url, timeout=cfg.timeout)
    page.raise_for_status()
    csrf = extract_csrf(page.text)
    headers = build_api_headers(csrf, referer=cfg.url)

    # 2) Resolve the post's numeric id from the hashid in the URL, so this keeps
    #    working even if the numeric id changes.
    hashid = extract_hashid(cfg.url)
    look = s.get(WISH_ENDPOINT.format(hashid=hashid), headers=headers, timeout=cfg.timeout)
    look.raise_for_status()
    attrs = look.json()["data"]["attributes"]
    wish_id = attrs["id"]
    headline = (attrs.get("headline") or "").strip()

    payload = build_reaction_payload(wish_id, cfg.emoji)

    # 3) Fire the reaction — or describe it, under --dry-run.
    if cfg.dry_run:
        elapsed = time.perf_counter() - started
        print(
            f'[·] dry-run: would POST {payload} for "{headline}" '
            f"(resolved in {elapsed:.2f}s, CSRF ok, nothing sent)"
        )
        return True

    resp = s.post(REACTIONS_ENDPOINT, headers=headers, json=payload, timeout=cfg.timeout)
    if resp.status_code not in (200, 201):
        print(f"[x] reaction failed: HTTP {resp.status_code} {resp.text[:200]}")
        return False
    reaction = resp.json()["data"]["attributes"]

    # 4) Optional: attach a display name to this anonymous reactor.
    if cfg.name:
        user_id = reaction.get("user_id")
        wall_id = attrs.get("wall_id")
        if user_id and wall_id:
            s.patch(
                SESSION_USER_ENDPOINT.format(user_id=user_id),
                headers=headers,
                json={"data": {"attributes": {"name": cfg.name, "wallId": wall_id}}},
                timeout=cfg.timeout,
            )

    elapsed = time.perf_counter() - started
    who = f' as "{cfg.name}"' if cfg.name else " (anonymous)"
    print(
        f'[✓] liked "{headline}" with {cfg.emoji}{who} in {elapsed:.2f}s '
        f"(reaction id {reaction.get('id')})"
    )
    return True


def _run_one(cfg: Config, index: int) -> bool:
    """Run a single reaction, isolating failures so one can't kill the batch."""
    try:
        return place_reaction(cfg)
    except requests.RequestException as exc:
        print(f"[x] like {index + 1}: network error: {exc}")
    except Exception as exc:  # noqa: BLE001 — one bad worker must not stop the rest
        print(f"[x] like {index + 1}: failed: {exc}")
    return False


def run(cfg: Config, count: int, concurrency: int) -> int:
    """Run `count` reactions, `concurrency` at a time. Returns the success count."""
    workers = worker_count(concurrency, count)
    started = time.perf_counter()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda i: _run_one(cfg, i), range(count)))

    done = sum(1 for ok in results if ok)
    elapsed = time.perf_counter() - started
    verb = "dry-run checks passed" if cfg.dry_run else "likes placed"
    print(f"\nDone: {done} of {count} {verb} in {elapsed:.2f}s total ({workers} at a time).")
    return done


# --- CLI ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="like_bot.py",
        description="Place Padlet emoji reactions over plain HTTP — no browser required.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "count",
        nargs="?",
        type=int,
        default=DEFAULT_COUNT,
        help=f"how many reactions to place (default: {DEFAULT_COUNT})",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("PADLET_URL", ""),
        help="target post URL (default: $PADLET_URL)",
    )
    parser.add_argument(
        "--emoji",
        default=DEFAULT_EMOJI,
        help=f"hex code point or alias (default: {DEFAULT_EMOJI}); see --list-emoji",
    )
    parser.add_argument("--name", default=None, help="attribute reactions to a display name")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"how many run at once (default: {DEFAULT_CONCURRENCY})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"per-request timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve session, CSRF and post id, then print the request instead of sending it",
    )
    parser.add_argument(
        "--list-emoji", action="store_true", help="print the supported reaction values and exit"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code: 0 ok, 1 failures, 2 misconfigured."""
    # Legacy Windows consoles raise on the ✓ character; odd streams simply keep
    # their encoding.
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)

    if args.list_emoji:
        for alias, value in sorted(EMOJI_ALIASES.items(), key=lambda kv: kv[1]):
            print(f"  {value:<6} {alias}")
        return 0

    try:
        cfg = Config(
            url=validate_url(args.url),
            emoji=resolve_emoji(args.emoji),
            name=args.name,
            timeout=args.timeout,
            dry_run=args.dry_run,
        )
        if args.count < 1:
            raise ConfigError(f"count must be 1 or more — got {args.count}.")
    except ConfigError as exc:
        print(f"{exc}\n\n{CONFIG_HELP}")
        return 2

    done = run(cfg, count=args.count, concurrency=args.concurrency)
    return 0 if done == args.count else 1


if __name__ == "__main__":
    sys.exit(main())
