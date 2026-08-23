# How I found it — replacing Selenium with three HTTP requests

This is the walkthrough: how you go from *"I automate this by clicking it in Chrome"* to
*"I reproduce it with `requests` in 1.5 seconds."* The subject here is Padlet's reaction
button, but nothing about the method is Padlet-specific. The same six steps work on most
server-rendered apps with a JSON API behind them.

---

## Step 0 — the starting point

The first version drove a real browser:

```python
driver = webdriver.Chrome()
driver.get(POST_URL)
WebDriverWait(driver, 30).until(...)  # wait for the SPA
driver.find_element(By.CSS_SELECTOR, "[data-testid='reaction-button']").click()
```

It worked, and it took about 40 seconds — nearly all of it spent launching Chrome and
rendering a single-page app, just to trigger one click. That ratio is the tell. **When
the setup cost dwarfs the action, you're automating the wrong layer.**

---

## Step 1 — watch what the click actually does

Open the post in Chrome → DevTools (`F12`) → **Network** tab → filter to **Fetch/XHR** →
clear the log → click the reaction once.

The whole interaction was a single row:

```text
POST  https://padlet.com/api/7/reactions     201    142 ms
```

142 ms. The other ~39.8 seconds were pure overhead — starting a browser to make one
HTTP request.

> **Filter to Fetch/XHR first.** Unfiltered, one click on a modern app buries the
> interesting call under a hundred images, fonts, and analytics beacons.

---

## Step 2 — read the request

Right-click the row → **Copy → Copy as cURL**. That gives you everything the browser
sent: method, URL, headers, cookies, body. Paste it into a scratch file and read it.

The body was small enough to understand at a glance:

```json
{ "wish_id": 123456, "value": "2764", "reaction_type": "emoji" }
```

Three fields, and two questions:

- **`value: "2764"`** — that's ❤️ as a Unicode code point in hex (U+2764). Clicking the
  other reactions once each gave the rest: 👍 `1f44d`, 😂 `1f602`, 🥳 `1f973`, 😆 `1f606`.
- **`wish_id: 123456`** — a numeric id that appears *nowhere* in the URL. The post URL
  ends in `/wish/AbCdEf123`, a public hashid. Something maps one to the other.

A "wish", incidentally, is Padlet's internal name for a post. Internal vocabulary leaking
into an API is normal and worth noting — it tells you the API predates the current UI
naming.

---

## Step 3 — replay it, and watch it fail

The naive translation:

```python
requests.post(
    "https://padlet.com/api/7/reactions",
    json={"wish_id": 123456, "value": "2764", "reaction_type": "emoji"},
)
```

```text
HTTP 422 Unprocessable Entity
```

**This failure is the interesting part of the whole exercise.** The request body was
byte-identical to the browser's. What the browser had and this didn't was *context*:

1. **Cookies.** The browser had loaded the page first, and the server had handed it an
   anonymous session (`ww_s`, `ww_d`). `requests` sent none.
2. **A CSRF token.** The cURL export included an `X-CSRF-Token` header holding a value
   that appears nowhere in the JSON — it comes from the HTML.

This is the general lesson: **a captured request is rarely self-contained.** It's the last
step of a conversation, and replaying it alone drops everything the earlier steps
established.

---

## Step 4 — find where the token comes from

View source on the post page (`Ctrl+U`), search for `csrf`:

```html
<meta name="csrf-token" content="hK3n...==" />
```

Standard Rails. The server embeds a token in the HTML, JavaScript reads it, and every
subsequent write request echoes it back in a header. So the first page load isn't
optional — it's what mints both the session cookies *and* the token:

```python
page = s.get(url, timeout=TIMEOUT)
csrf = re.search(r'name="csrf-token"\s+content="([^"]+)"', page.text).group(1)
```

Using one `requests.Session()` for everything means the cookies from that first response
are automatically attached to every call afterward. That single object is what makes the
whole thing work.

---

## Step 5 — close the hashid → id gap

The payload needs `wish_id`, the URL only gives a hashid. You could hardcode the number
you saw in DevTools — and it would work until it didn't.

Back in the Network tab, this time watching the requests fired on **page load** rather
than on click, one row answered it:

```text
GET  https://padlet.com/api/9/wishes/AbCdEf123
```

```json
{ "data": { "attributes": { "id": 123456, "headline": "…", "wall_id": 99 } } }
```

There's the mapping. Resolving it at run time instead of hardcoding costs one cheap GET
and makes the script survive ids changing:

```python
hashid = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
attrs = s.get(WISH_ENDPOINT.format(hashid=hashid), headers=headers).json()["data"]["attributes"]
wish_id = attrs["id"]
```

> **Look at page-load traffic, not just click traffic.** The data your action needs was
> usually fetched long before you clicked anything.

---

## Step 6 — rebuild the context, not just the request

The final headers mirror what the browser sends, because several of them are load-bearing:

```python
{
    "Accept": "application/json",
    "Content-Type": "application/json",
    "X-CSRF-Token": csrf,  # required — 422 without it
    "X-Requested-With": "XMLHttpRequest",  # marks it as an AJAX call
    "Referer": url,  # checked by some endpoints
    "Origin": "https://padlet.com",  # checked by CORS/CSRF middleware
}
```

Plus a browser `User-Agent` on the session. You don't always need every one of these —
but adding them costs nothing, and stripping them one at a time to find the minimum is a
good way to learn which defenses an app actually enforces.

Result: three requests, ~1.5 seconds, no browser.

```text
GET  the post page        → cookies + CSRF token
GET  /api/9/wishes/<id>   → hashid ➜ numeric id
POST /api/7/reactions     → the actual action
```

---

## The method, generalized

1. **Open DevTools → Network → filter Fetch/XHR.** Clear, then perform the action once.
2. **Copy as cURL.** That's the ground truth of what the browser sent.
3. **Replay it in Python and expect it to fail.** The failure tells you what context you're missing.
4. **Trace each missing piece to its source** — HTML meta tags, cookies, an earlier API call.
5. **Resolve ids at run time** rather than hardcoding what you saw once.
6. **Reproduce the request context**, not just the request.

## When this *doesn't* work

Be honest about the limits — browser automation still wins when:

- the token is computed by obfuscated JavaScript rather than embedded in HTML
- there's a device-fingerprint or proof-of-work challenge in front of the endpoint
- the site is behind a bot-management product that TLS-fingerprints your client
  (`requests` looks nothing like Chrome at the TLS layer — `curl_cffi` is the usual next step)
- the flow genuinely depends on rendered state, like a canvas or a WebGL surface

The check is Step 3: replay the captured request. If it succeeds once you've supplied the
session and token, you can drop the browser. If it fails in a way you can't trace to a
missing header or cookie, that's your signal to keep it.

---

The interesting part was never the reaction count. It was that a 40-second browser
dependency turned out to be three HTTP requests in a trench coat.
