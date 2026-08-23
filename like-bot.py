"""
Padlet like bot — pure HTTP, no browser. ~1.5s per like.

How it works (discovered by capturing what the web app actually does):
  Clicking a reaction in the browser ends up firing ONE REST call:
      POST https://padlet.com/api/7/reactions
      {"wish_id": <numeric id>, "value": "2764", "reaction_type": "emoji"}
  The heavy Selenium/Chrome version spent ~40s launching a browser and
  rendering a very heavy single-page app just to trigger that one request.
  Here we skip the browser entirely:
      1. GET the post page once  -> anonymous session cookies + CSRF token
      2. resolve the post's numeric id from the hashid in the URL
      3. POST the reaction
  No display-name popup is needed — the server auto-creates an anonymous
  reactor. (Set NAME below if you want the like attributed to a name.)

Requirements:  pip install requests
"""

import os
import re
import sys
import time
import requests
from concurrent.futures import ThreadPoolExecutor

# Make output UTF-8 no matter how the script is launched (fixes the Windows
# 'charmap' crash on the ✓ character). This means you can just run:
#     python like-bot.py 25
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# --- config ------------------------------------------------------------------
# Target post — a board you own or are explicitly authorized to test.
# Set it here, or leave the placeholder and pass it in via the environment:
#     PADLET_URL="https://padlet.com/<user>/<board>/wish/<hashid>" python like-bot.py
PLACEHOLDER_URL = "https://padlet.com/<user>/<board>/wish/<hashid>"
URL = os.environ.get("PADLET_URL") or PLACEHOLDER_URL

# Reaction emoji as a unicode code point (hex), matching Padlet's icons:
#   ❤️ = "2764"   👍 = "1f44d"   😂 = "1f602"   🥳 = "1f973"   😆 = "1f606"
EMOJI = "2764"

# Optional: attribute the like to a display name (e.g. "Ahmed").
# Leave as None to react anonymously (fastest, one request).
NAME = None

# How many likes to place. Each one is a separate anonymous reactor.
# You can also override this on the command line:  python like-bot.py 3
COUNT = 3

# How many likes run at the SAME time. Keep this modest (10-15). The total
# still completes fast, but a smaller stream keeps each like ~1.4s and avoids
# the timeouts/connection resets you get when firing hundreds all at once.
CONCURRENCY = 12

TIMEOUT = 20
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def like(url=URL, emoji=EMOJI, name=NAME):
    """Place one reaction on the padlet post at `url`. Returns True on success."""
    t0 = time.perf_counter()
    s = requests.Session()
    s.headers["User-Agent"] = UA

    # 1) Load the page once: this hands us the anonymous session cookies
    #    (ww_s, ww_d, ...) and the Rails CSRF token the API requires.
    page = s.get(url, timeout=TIMEOUT)
    page.raise_for_status()
    m = re.search(r'name="csrf-token"\s+content="([^"]+)"', page.text)
    if not m:
        raise RuntimeError("Could not find CSRF token — page layout changed or blocked.")
    csrf = m.group(1)

    api = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-CSRF-Token": csrf,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": url,
        "Origin": "https://padlet.com",
    }

    # 2) Resolve the post's numeric id from the hashid in the URL, so this keeps
    #    working even if the numeric id changes. (.../wish/<hashid>)
    hashid = url.rstrip("/").rsplit("/", 1)[-1]
    look = s.get(f"https://padlet.com/api/9/wishes/{hashid}", headers=api, timeout=TIMEOUT)
    look.raise_for_status()
    attrs = look.json()["data"]["attributes"]
    wish_id = attrs["id"]
    headline = (attrs.get("headline") or "").strip()

    # 3) Fire the reaction.
    resp = s.post(
        "https://padlet.com/api/7/reactions",
        headers=api,
        json={"wish_id": wish_id, "value": emoji, "reaction_type": "emoji"},
        timeout=TIMEOUT,
    )
    if resp.status_code not in (200, 201):
        print(f"[x] reaction failed: HTTP {resp.status_code} {resp.text[:200]}")
        return False
    reaction = resp.json()["data"]["attributes"]

    # 4) Optional: attach a display name to this anonymous reactor.
    if name:
        user_id = reaction.get("user_id")
        wall_id = attrs.get("wall_id")
        if user_id and wall_id:
            s.patch(
                f"https://padlet.com/api/1/session/users/{user_id}",
                headers=api,
                json={"data": {"attributes": {"name": name, "wallId": wall_id}}},
                timeout=TIMEOUT,
            )

    dt = time.perf_counter() - t0
    who = f' as "{name}"' if name else " (anonymous)"
    print(f'[✓] liked "{headline}" with {emoji}{who} in {dt:.2f}s '
          f'(reaction id {reaction.get("id")})')
    return True


def _one(i):
    """Run a single like, catching errors so one failure doesn't kill the rest."""
    try:
        return like()
    except requests.RequestException as e:
        print(f"[x] like {i + 1}: network error: {e}")
    except Exception as e:
        print(f"[x] like {i + 1}: failed: {e}")
    return False


if __name__ == "__main__":
    # No target configured: say so clearly instead of failing on a fake URL.
    if URL == PLACEHOLDER_URL or "<" in URL:
        print("No target post configured.\n"
              "Edit URL at the top of this file, or set the environment variable:\n"
              '  PADLET_URL="https://padlet.com/<user>/<board>/wish/<hashid>"\n'
              "Use only a board you own or are explicitly authorized to test.")
        sys.exit(2)

    # Allow "python like-bot.py 5" to override COUNT from the command line.
    count = COUNT
    if len(sys.argv) > 1:
        count = int(sys.argv[1])

    # Run the likes in parallel, but only CONCURRENCY at a time (a steady
    # stream) so we don't open hundreds of connections at once and choke.
    workers = min(CONCURRENCY, count)
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_one, range(count)))
    done = sum(1 for r in results if r)

    dt = time.perf_counter() - t0
    print(f"\nDone: {done} of {count} likes placed in {dt:.2f}s total "
          f"({workers} at a time).")
    sys.exit(0 if done == count else 1)
