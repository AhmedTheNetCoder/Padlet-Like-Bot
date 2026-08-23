<h1 align="center">⚡ Padlet Reaction Engine</h1>

<p align="center">
  <b>From ~40 seconds of browser automation to ~1.5 seconds of pure HTTP.</b>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.9%2B-blue?logo=python&logoColor=white">
  <img alt="Requests" src="https://img.shields.io/badge/HTTP-requests-success">
  <img alt="Browser" src="https://img.shields.io/badge/Browser-not%20required-orange">
  <img alt="Concurrency" src="https://img.shields.io/badge/Automation-threaded-purple">
  <img alt="Use" src="https://img.shields.io/badge/Use-authorized%20testing%20only-red">
</p>

<p align="center">
  A Python proof-of-concept that reverse-engineers the network flow behind Padlet emoji
  reactions and reproduces it with direct HTTP requests —
  no Selenium, no Chrome, no WebDriver, no page rendering.
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

But firing that request on its own returns an error. The server expects two things the browser had already collected:

1. **A valid anonymous session** — the cookies handed out by the first page load.
2. **A matching CSRF token** — embedded in the page HTML as `<meta name="csrf-token">`.

And the payload needs the post's **internal numeric id**, while the URL only exposes a public **hashid**. That gap is what the middle step resolves.

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
s.headers["User-Agent"] = UA
```

**① Establish the session and read the CSRF token.**

```python
page = s.get(url, timeout=TIMEOUT)
page.raise_for_status()

m = re.search(r'name="csrf-token"\s+content="([^"]+)"', page.text)
if not m:
    raise RuntimeError("Could not find CSRF token — page layout changed or blocked.")
csrf = m.group(1)
```

**② Resolve the public hashid into the internal post id.**

```python
hashid = url.rstrip("/").rsplit("/", 1)[-1]

look = s.get(f"https://padlet.com/api/9/wishes/{hashid}", headers=api, timeout=TIMEOUT)
look.raise_for_status()

attrs    = look.json()["data"]["attributes"]
wish_id  = attrs["id"]
headline = (attrs.get("headline") or "").strip()
```

Reading the id at run time instead of hard-coding it means the script keeps working when the numeric id changes.

**③ Send the reaction.**

```python
resp = s.post(
    "https://padlet.com/api/7/reactions",
    headers=api,
    json={"wish_id": wish_id, "value": emoji, "reaction_type": "emoji"},
    timeout=TIMEOUT,
)
```

**④ Optionally name the anonymous reactor.**

```python
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
```

Leaving `NAME = None` skips this entirely — the server auto-creates an anonymous reactor, so the whole thing stays at three requests.

That's the conversation the browser was having underneath the interface.

---

## 📦 Installation

```bash
git clone https://github.com/AhmedTheNetCoder/Padlet-Like-Bot.git
cd Padlet-Like-Bot
pip install -r requirements.txt
```

The only dependency is [`requests`](https://pypi.org/project/requests/) — `pip install requests` works just as well. Python 3.9+.

---

## ▶️ Usage

Point the script at a post on a board **you own or are authorized to test** — either by editing `URL` at the top of `like-bot.py`, or without touching the file at all:

```bash
# macOS / Linux
PADLET_URL="https://padlet.com/<user>/<board>/wish/<hashid>" python like-bot.py

# Windows PowerShell
$env:PADLET_URL = "https://padlet.com/<user>/<board>/wish/<hashid>"; python like-bot.py
```

Then:

```bash
python like-bot.py          # uses the configured COUNT
python like-bot.py 3        # or pass the count on the command line
```

With no target set, the script exits `2` and tells you what to configure — it ships with a placeholder URL, not a real post.

Output:

```text
[✓] liked "Demo post" with 2764 (anonymous) in 1.47s (reaction id 123456)
[✓] liked "Demo post" with 2764 (anonymous) in 1.53s (reaction id 123457)
[✓] liked "Demo post" with 2764 (anonymous) in 1.61s (reaction id 123458)

Done: 3 of 3 likes placed in 1.67s total (3 at a time).
```

---

## ⚙️ Configuration

Everything tunable sits at the top of `like-bot.py`.

| Setting | Default | What it does |
|---|---|---|
| `URL` | placeholder | The `…/wish/<hashid>` link of the target post. Overridden by the `PADLET_URL` environment variable. |
| `EMOJI` | `"2764"` | Reaction, as a Unicode code point in hex. |
| `NAME` | `None` | `None` reacts anonymously (one request). A string attaches a display name (adds a PATCH). |
| `COUNT` | `3` | How many reactions to place. Overridable via `argv[1]`. |
| `CONCURRENCY` | `12` | How many run at the same time. |
| `TIMEOUT` | `20` | Per-request timeout, in seconds. |
| `UA` | Chrome 120 | User-Agent sent on every request. |

### Emoji values

| Reaction | Value |
|:---:|---|
| ❤️ | `2764` |
| 👍 | `1f44d` |
| 😂 | `1f602` |
| 🥳 | `1f973` |
| 😆 | `1f606` |

```python
EMOJI = "1f44d"   # 👍
```

### Concurrency

Keep this modest — 10–15 is the sweet spot. The run still finishes fast, but a steady stream keeps each request around 1.4 s and avoids the timeouts and connection resets you get when firing hundreds at once. The pool never exceeds the work available:

```python
workers = min(CONCURRENCY, count)
```

For debugging, drop it to `CONCURRENCY = 2` so the log stays readable.

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

Because the work is network-bound rather than CPU-bound, threads are the right tool here — the GIL is released while each request is in flight. Every worker is wrapped so one failure can't take down the rest:

```python
def _one(i):
    try:
        return like()
    except requests.RequestException as e:
        print(f"[x] like {i + 1}: network error: {e}")
    except Exception as e:
        print(f"[x] like {i + 1}: failed: {e}")
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

Miss any of these and the API answers with a rejection rather than a reaction.

---

## 🪟 Windows support

Legacy console encodings choke on `✓` and raise a `charmap` error mid-run. The script fixes stdout on startup:

```python
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
```

So `python like-bot.py` just works, with no `chcp 65001` or `PYTHONIOENCODING` dance first.

---

## 🧯 Error handling & exit codes

Failures are reported per worker and never abort the batch:

```text
[x] like 2: network error: HTTPSConnectionPool(...)
[x] like 2: failed: Could not find CSRF token — page layout changed or blocked.
[x] reaction failed: HTTP 403 ...
```

Common causes: changed API version, changed page markup, invalid CSRF context, board permissions, sign-in requirements, rate limiting, anti-abuse protection, or plain network trouble.

The process exits `0` only when every reaction succeeded, `1` when any failed, and `2` when no target post is configured — so it drops straight into a test pipeline:

```bash
python like-bot.py 1 && echo "smoke test passed"
```

---

## 📁 Repository structure

```text
Padlet-Like-Bot
├── like-bot.py         # the whole implementation
├── requirements.txt    # requests
├── .gitignore
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
- migrating Selenium scripts to plain HTTP

---

## ⚠️ Intended use

This repository exists for **education, research, and testing boards you own or have explicit permission to test.**

Do **not** use it to inflate engagement, competitions, polls, votes, analytics, or reactions belonging to anyone else. Automated interaction may also be limited by Padlet's Terms of Service and technical safeguards. Keep the request volume to the minimum your test actually needs.

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
  <sub>Built with Python 🐍 · requests 🌐 · ThreadPoolExecutor 🧵 · and a lot of network inspection 🔍</sub>
</p>
