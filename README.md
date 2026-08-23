<h1 align="center">⚡ Padlet Reaction Engine</h1>

<p align="center">
  <b>Replacing 40 seconds of Selenium with 1.5 seconds of HTTP —<br>
  a reverse-engineering case study.</b>
</p>

<p align="center">
  <a href="https://github.com/AhmedTheNetCoder/Padlet-Like-Bot/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/AhmedTheNetCoder/Padlet-Like-Bot/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.9%2B-blue?logo=python&logoColor=white">
  <img alt="Requests" src="https://img.shields.io/badge/HTTP-requests-success">
  <img alt="Browser" src="https://img.shields.io/badge/Browser-not%20required-orange">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/License-MIT-yellow"></a>
</p>

<p align="center">
  A Python case study in reverse-engineering the network flow behind a web app's UI and
  reproducing it with direct HTTP requests — no Selenium, no Chrome, no WebDriver,
  no page rendering. The worked example is Padlet's emoji reactions.
</p>

<p align="center">
  <b>📖 <a href="HOW-I-FOUND-IT.md">Read the walkthrough</a></b> — the DevTools-to-Python
  method, step by step, including the part where the replayed request fails and why.
</p>

---

## 🚀 The idea

Clicking a reaction button in a modern web app *looks* like a UI problem. It isn't. Underneath, the browser sends one small REST call.

The original browser-automation approach paid for the whole UI stack to reach it:

```text
Launch Chrome → load Padlet → render the SPA → wait for JS
→ find the post → find the reaction button → click     ≈ 40 s
```

This project skips the interface entirely and reproduces the request flow the browser was performing anyway:

```text
GET the post page → session cookies + CSRF token
→ resolve the internal post id → POST the reaction     ≈ 1.5 s
```

No GUI. No browser. No WebDriver. Just HTTP.

---

## ⚡ Selenium vs. pure HTTP

| | Browser automation | This project |
|---|:---:|:---:|
| Launch a browser | ✅ required | ❌ none |
| ChromeDriver binary | ✅ required | ❌ none |
| Render the JavaScript UI | ✅ required | ❌ none |
| Search the DOM | ✅ required | ❌ none |
| Simulate clicks | ✅ required | ❌ none |
| Maintain an HTTP session | indirect | ✅ explicit |
| Talk to the API directly | ❌ | ✅ |
| Typical run time | ~40 s | ~1.5 s |
| Memory footprint | hundreds of MB | a few MB |
| Testable without a browser | ❌ | ✅ |
| Architecture | UI automation | HTTP workflow |

The point isn't only speed. The real win is deleting an entire layer of moving parts that can break.

---

## 🧠 What was discovered

Capturing the traffic while reacting in the browser shows that the whole interaction reduces to a single write call:

```http
POST https://padlet.com/api/7/reactions
Content-Type: application/json
X-CSRF-Token: <token from the page HTML>
```

```json
{ "wish_id": 123456, "value": "2764", "reaction_type": "emoji" }
```

But firing that request on its own returns `422`. The server expects two things the browser had already collected:

1. **A valid anonymous session** — the cookies handed out by the first page load.
2. **A matching CSRF token** — embedded in the page HTML as `<meta name="csrf-token">`.

And the payload needs the post's **internal numeric id**, while the URL only exposes a public **hashid**. That gap is what the middle step resolves.

> The full derivation — including which headers turn out to be load-bearing, and when this
> technique *doesn't* work — is in **[HOW-I-FOUND-IT.md](HOW-I-FOUND-IT.md)**.

---

## 🏗️ Architecture

```text
┌──────────────────────────────────────┐
│  Padlet post URL (…/wish/<hashid>)   │
└──────────────────┬───────────────────┘
                   ▼
┌──────────────────────────────────────┐
│  requests.Session()                  │
│  persistent cookie jar + browser UA  │
└──────────────────┬───────────────────┘
                   ▼
┌──────────────────────────────────────┐
│  ① GET the post page                 │
│     • anonymous session cookies      │
│     • CSRF token from the HTML       │
└──────────────────┬───────────────────┘
                   ▼
┌──────────────────────────────────────┐
│  ② GET /api/9/wishes/<hashid>        │
│     hashid ➜ internal wish_id        │
└──────────────────┬───────────────────┘
                   ▼
┌──────────────────────────────────────┐
│  ③ POST /api/7/reactions             │
│     { wish_id, value, type }         │
└──────────────────┬───────────────────┘
                   ▼
┌──────────────────────────────────────┐
│  ④ optional: PATCH the session user  │
│     to attach a display name         │
└──────────────────┬───────────────────┘
                   ▼
                ⚡ reaction
```

---

## 🔥 The core

Everything hangs off one session, so cookies and the browser User-Agent carry across every call:

```python
s = requests.Session()
s.headers["User-Agent"] = USER_AGENT
```

**① Establish the session and read the CSRF token.**

```python
page = s.get(cfg.url, timeout=cfg.timeout)
page.raise_for_status()

csrf = extract_csrf(page.text)  # <meta name="csrf-token" content="…">
headers = build_api_headers(csrf, referer=cfg.url)
```

**② Resolve the public hashid into the internal post id.**

```python
hashid = extract_hashid(cfg.url)  # .../wish/<hashid>

look = s.get(WISH_ENDPOINT.format(hashid=hashid), headers=headers, timeout=cfg.timeout)
look.raise_for_status()

attrs = look.json()["data"]["attributes"]
wish_id = attrs["id"]
```

Reading the id at run time instead of hard-coding it means the script keeps working when the numeric id changes.

**③ Send the reaction.**

```python
payload = build_reaction_payload(wish_id, cfg.emoji)
resp = s.post(REACTIONS_ENDPOINT, headers=headers, json=payload, timeout=cfg.timeout)
```

**④ Optionally name the anonymous reactor.**

```python
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
```

Leaving the name unset skips this entirely — the server auto-creates an anonymous reactor, so the whole thing stays at three requests.

That's the conversation the browser was having underneath the interface.

---

## 📦 Installation

```bash
git clone https://github.com/AhmedTheNetCoder/Padlet-Like-Bot.git
cd Padlet-Like-Bot
pip install -r requirements.txt
```

The only runtime dependency is [`requests`](https://pypi.org/project/requests/). Python 3.9+.

---

## ▶️ Usage

Point it at a post, either with `--url` or via the `PADLET_URL` environment variable:

```bash
python like_bot.py --url "https://padlet.com/<user>/<board>/wish/<hashid>" --dry-run
```

Start with `--dry-run`. It performs every read step — session, CSRF token, id resolution — then prints the request it *would* send instead of sending it. That confirms your setup works without writing anything:

```text
[·] dry-run: would POST {'wish_id': 123456, 'value': '2764', 'reaction_type': 'emoji'} for "Demo post" (resolved in 0.61s, CSRF ok, nothing sent)

Done: 1 of 1 dry-run checks passed in 0.62s total (1 at a time).
```

Then run it for real:

```bash
export PADLET_URL="https://padlet.com/<user>/<board>/wish/<hashid>"   # bash/zsh
$env:PADLET_URL = "https://padlet.com/<user>/<board>/wish/<hashid>"   # PowerShell

python like_bot.py              # 3 reactions, the default
python like_bot.py 5            # or pass the count
python like_bot.py 5 --emoji thumbsup --name "Ahmed" --concurrency 4
```

```text
[✓] liked "Demo post" with 2764 (anonymous) in 1.47s (reaction id 123456)
[✓] liked "Demo post" with 2764 (anonymous) in 1.53s (reaction id 123457)
[✓] liked "Demo post" with 2764 (anonymous) in 1.61s (reaction id 123458)

Done: 3 of 3 likes placed in 1.67s total (3 at a time).
```

With no target configured the run exits `2` and tells you what to set — the repo ships with a placeholder, not a real post.

---

## ⚙️ Options

```text
python like_bot.py [count] [options]
```

| Option | Default | What it does |
|---|---|---|
| `count` | `3` | Positional. How many reactions to place. |
| `--url` | `$PADLET_URL` | Target post — a `…/wish/<hashid>` link. Validated before anything is sent. |
| `--emoji` | `2764` | Hex code point or alias — see `--list-emoji`. |
| `--name` | *(none)* | Attribute reactions to a display name (adds the PATCH). |
| `--concurrency` | `12` | How many run at the same time. |
| `--timeout` | `20` | Per-request timeout, in seconds. |
| `--dry-run` | off | Resolve everything, print the request, send nothing. |
| `--list-emoji` | — | Print the supported reaction values and exit. |
| `--version` | — | Print the version and exit. |

### Reaction values

Padlet identifies reactions by Unicode code point in hex. Aliases are accepted for readability:

| Reaction | Value | Alias |
|:---:|---|---|
| ❤️ | `2764` | `heart` |
| 👍 | `1f44d` | `thumbsup` |
| 😂 | `1f602` | `joy` |
| 🥳 | `1f973` | `party` |
| 😆 | `1f606` | `grin` |

### Concurrency

Keep it modest — 10–15 is the sweet spot. The run still finishes fast, but a steady stream keeps each request around 1.4 s and avoids the timeouts and connection resets you get when firing hundreds at once. The pool never exceeds the work available:

```python
def worker_count(concurrency: int, count: int) -> int:
    return max(1, min(concurrency, count))
```

For debugging, `--concurrency 1` keeps the log readable.

---

## 🧵 Concurrency model

Each reaction is an independent HTTP workflow with its own session, run through a `ThreadPoolExecutor` instead of a fleet of browser processes:

```text
                 ┌── Worker 1 ── GET → resolve → POST
                 │
Main thread ─────┼── Worker 2 ── GET → resolve → POST
                 │
                 ├── Worker 3 ── GET → resolve → POST
                 │
                 └── Worker N ── GET → resolve → POST
```

Because the work is network-bound rather than CPU-bound, threads are the right tool here — the GIL is released while each request is in flight. Every worker is isolated so one failure can't take down the batch:

```python
def _run_one(cfg: Config, index: int) -> bool:
    try:
        return place_reaction(cfg)
    except requests.RequestException as exc:
        print(f"[x] like {index + 1}: network error: {exc}")
    except Exception as exc:
        print(f"[x] like {index + 1}: failed: {exc}")
    return False
```

Running the same workload under Selenium would mean N Chrome processes and hundreds of megabytes of RAM.

---

## 🍪 Session handling

The first request is not optional — it's what creates the anonymous session:

```text
Request #1 ──► server sets cookies (ww_s, ww_d, …)
                     │
                     ▼
             requests.Session  ── stores them
                     │
                     ▼
Request #2 ──► sends the same cookies back
```

Without that state, the later API calls don't belong to a recognised session and get rejected.

---

## 🛡️ CSRF handling

Padlet embeds a Rails CSRF token in the page HTML. The script scrapes it and mirrors the request context the web app uses:

| Header | Value |
|---|---|
| `X-CSRF-Token` | token scraped from the page |
| `X-Requested-With` | `XMLHttpRequest` |
| `Referer` | the post URL |
| `Origin` | `https://padlet.com` |
| `Content-Type` | `application/json` |

Miss the token and the API answers `422` rather than placing a reaction.

---

## 🪟 Windows support

Legacy console encodings choke on `✓` and raise a `charmap` error mid-run. The CLI fixes stdout on startup:

```python
with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
```

So `python like_bot.py` just works, with no `chcp 65001` or `PYTHONIOENCODING` dance first.

---

## 🧯 Error handling & exit codes

Failures are reported per worker and never abort the batch:

```text
[x] like 2: network error: HTTPSConnectionPool(...)
[x] like 2: failed: Could not find CSRF token — page layout changed, or the request was blocked.
[x] reaction failed: HTTP 403 ...
```

Common causes: changed API version, changed page markup, invalid CSRF context, board permissions, sign-in requirements, rate limiting, anti-abuse protection, or plain network trouble.

| Exit code | Meaning |
|:---:|---|
| `0` | every reaction succeeded |
| `1` | one or more failed |
| `2` | misconfigured — no target, bad URL, unknown emoji |

Which makes it usable in a pipeline:

```bash
python like_bot.py 1 --dry-run && echo "smoke test passed"
```

---

## 🧪 Development

```bash
pip install -r requirements-dev.txt
ruff check . && ruff format --check .
pytest -q
```

The test suite is **fully offline** — no network, no live board. `requests.Session` is
replaced with a fake that records every call, so the tests assert on the real thing:
that the flow makes exactly three requests, that the POST body matches the captured
payload byte for byte, that the CSRF token reaches the header, that `--dry-run` never
issues a write, and that each exit code fires when it should.

CI runs lint, format check, and the suite on Python 3.9 – 3.13.

---

## 📁 Repository structure

```text
Padlet-Like-Bot
├── like_bot.py             # the implementation + CLI
├── tests/
│   └── test_like_bot.py    # offline tests, fake session
├── HOW-I-FOUND-IT.md       # the DevTools → Python walkthrough
├── .github/workflows/ci.yml
├── pyproject.toml          # ruff + pytest config
├── requirements.txt
├── requirements-dev.txt
├── LICENSE
└── README.md
```

---

## 💡 What this project teaches

The lesson generalises far beyond Padlet. A web app is a stack:

```text
User → Browser UI → JavaScript → API → Server
```

Selenium attaches at the top and drives everything below it:

```text
User → [ Browser UI ] ← Selenium → JavaScript → API → Server
```

HTTP automation attaches near the bottom and skips the rest:

```text
User → Browser UI → JavaScript → [ API ] ← requests → Server
```

Same result, without the browser and JavaScript layers to launch, render, wait for, and debug. Useful as a worked example of:

- reverse-engineering traffic with browser DevTools
- reproducing REST calls in Python
- session and cookie management
- CSRF token handling
- public-to-internal id resolution
- controlled concurrency with `ThreadPoolExecutor`
- testing a network client without touching the network
- migrating Selenium scripts to plain HTTP

---

## 🔧 API stability

This relies on implementation details observed in Padlet's web client. Endpoints like:

```text
GET   /api/9/wishes/<hashid>
POST  /api/7/reactions
PATCH /api/1/session/users/<user_id>
```

are **not** documented public APIs. The version numbers in those paths are a strong hint that they change. Padlet may alter endpoint versions, auth requirements, CSRF behaviour, session handling, JSON shapes, permissions, or reaction semantics at any time — and this proof-of-concept will need updating when they do.

---

## ⭐ The takeaway

```text
Chrome + Selenium + WebDriver + DOM search + JS rendering + ~40 s
                              ↓
              Python + requests + HTTP + ~1.5 s
```

### Don't automate the browser when the real conversation is happening over HTTP. ⚡

<p align="center">
  <sub>MIT licensed · Built with Python 🐍 · requests 🌐 · ThreadPoolExecutor 🧵 · and a lot of network inspection 🔍</sub>
</p>
