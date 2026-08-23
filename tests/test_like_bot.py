"""Offline tests — no network, no live Padlet board required."""

import pytest

import like_bot
from like_bot import (
    Config,
    ConfigError,
    CsrfTokenNotFound,
    build_api_headers,
    build_reaction_payload,
    extract_csrf,
    extract_hashid,
    main,
    place_reaction,
    resolve_emoji,
    validate_url,
    worker_count,
)

VALID_URL = "https://padlet.com/someone/a-board-xyz123/wish/AbCdEf123"


# --- URL handling ------------------------------------------------------------


def test_validate_url_accepts_a_real_post_link():
    assert validate_url(VALID_URL) == VALID_URL


@pytest.mark.parametrize(
    "url",
    [
        "",
        like_bot.PLACEHOLDER_URL,
        "https://padlet.com/<user>/<board>/wish/<hashid>",
        "ftp://padlet.com/a/b/wish/xyz",
        "https://example.com/a/b/wish/xyz",
        "https://padlet.com/someone/a-board",  # board link, not a post link
    ],
)
def test_validate_url_rejects_bad_input(url):
    with pytest.raises(ConfigError):
        validate_url(url)


def test_extract_hashid_reads_the_last_path_segment():
    assert extract_hashid(VALID_URL) == "AbCdEf123"


def test_extract_hashid_ignores_trailing_slash_and_query():
    assert extract_hashid(VALID_URL + "/") == "AbCdEf123"
    assert extract_hashid(VALID_URL + "?utm_source=x") == "AbCdEf123"


# --- CSRF --------------------------------------------------------------------


def test_extract_csrf_finds_the_meta_tag():
    html = '<head><meta name="csrf-token" content="tok-123==" /></head>'
    assert extract_csrf(html) == "tok-123=="


def test_extract_csrf_raises_when_the_tag_is_missing():
    with pytest.raises(CsrfTokenNotFound):
        extract_csrf("<head><title>no token here</title></head>")


def test_api_headers_mirror_the_web_app():
    headers = build_api_headers("tok", referer=VALID_URL)
    assert headers["X-CSRF-Token"] == "tok"
    assert headers["X-Requested-With"] == "XMLHttpRequest"
    assert headers["Origin"] == "https://padlet.com"
    assert headers["Referer"] == VALID_URL


# --- emoji + payload ---------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [("heart", "2764"), ("THUMBSUP", "1f44d"), ("1f602", "1f602"), ("2764", "2764")],
)
def test_resolve_emoji_accepts_aliases_and_code_points(given, expected):
    assert resolve_emoji(given) == expected


@pytest.mark.parametrize("given", ["", "smile", "zzz", "1f6021f602ff"])
def test_resolve_emoji_rejects_unknown_values(given):
    with pytest.raises(ConfigError):
        resolve_emoji(given)


def test_reaction_payload_matches_the_captured_request():
    assert build_reaction_payload(123456, "2764") == {
        "wish_id": 123456,
        "value": "2764",
        "reaction_type": "emoji",
    }


@pytest.mark.parametrize(
    ("concurrency", "count", "expected"),
    [(12, 3, 3), (12, 100, 12), (12, 0, 1), (1, 50, 1)],
)
def test_worker_count_never_exceeds_the_work(concurrency, count, expected):
    assert worker_count(concurrency, count) == expected


# --- flow, against a fake session -------------------------------------------


class FakeResponse:
    def __init__(self, *, text="", json_body=None, status_code=200):
        self.text = text
        self.status_code = status_code
        self._json = json_body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected HTTP {self.status_code}")

    def json(self):
        return self._json


class FakeSession:
    """Stands in for requests.Session and records every call made."""

    PAGE_HTML = '<meta name="csrf-token" content="tok-abc" />'

    def __init__(self):
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if "/api/9/wishes/" in url:
            return FakeResponse(
                json_body={
                    "data": {"attributes": {"id": 123456, "headline": "Demo post", "wall_id": 99}}
                }
            )
        return FakeResponse(text=self.PAGE_HTML)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return FakeResponse(
            status_code=201,
            json_body={"data": {"attributes": {"id": 555, "user_id": 77}}},
        )

    def patch(self, url, **kwargs):
        self.calls.append(("PATCH", url, kwargs))
        return FakeResponse(json_body={})

    def methods(self):
        return [method for method, _, _ in self.calls]


def test_full_flow_sends_the_three_expected_requests():
    session = FakeSession()
    assert place_reaction(Config(url=VALID_URL), session=session) is True
    assert session.methods() == ["GET", "GET", "POST"]

    _, post_url, post_kwargs = session.calls[-1]
    assert post_url == like_bot.REACTIONS_ENDPOINT
    assert post_kwargs["json"] == {"wish_id": 123456, "value": "2764", "reaction_type": "emoji"}
    assert post_kwargs["headers"]["X-CSRF-Token"] == "tok-abc"
    assert session.headers["User-Agent"].startswith("Mozilla/5.0")


def test_hashid_is_resolved_before_the_reaction_is_sent():
    session = FakeSession()
    place_reaction(Config(url=VALID_URL), session=session)
    assert session.calls[1][1].endswith("/api/9/wishes/AbCdEf123")


def test_dry_run_reads_everything_but_never_writes(capsys):
    session = FakeSession()
    assert place_reaction(Config(url=VALID_URL, dry_run=True), session=session) is True
    assert session.methods() == ["GET", "GET"]  # no POST
    assert "dry-run" in capsys.readouterr().out


def test_named_reactor_patches_the_session_user():
    session = FakeSession()
    place_reaction(Config(url=VALID_URL, name="Tester"), session=session)
    assert session.methods() == ["GET", "GET", "POST", "PATCH"]

    _, patch_url, patch_kwargs = session.calls[-1]
    assert patch_url.endswith("/api/1/session/users/77")
    assert patch_kwargs["json"]["data"]["attributes"] == {"name": "Tester", "wallId": 99}


def test_anonymous_run_skips_the_patch():
    session = FakeSession()
    place_reaction(Config(url=VALID_URL, name=None), session=session)
    assert "PATCH" not in session.methods()


def test_rejected_reaction_returns_false(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(
        session, "post", lambda url, **kw: FakeResponse(status_code=403, text="forbidden")
    )
    assert place_reaction(Config(url=VALID_URL), session=session) is False


# --- CLI exit codes ----------------------------------------------------------


def test_missing_target_exits_2(capsys):
    assert main(["--url", ""]) == 2
    assert "No target post configured" in capsys.readouterr().out


def test_placeholder_url_exits_2():
    assert main(["--url", like_bot.PLACEHOLDER_URL]) == 2


def test_bad_emoji_exits_2():
    assert main(["--url", VALID_URL, "--emoji", "nope"]) == 2


def test_zero_count_exits_2():
    assert main(["0", "--url", VALID_URL]) == 2


def test_list_emoji_exits_0(capsys):
    assert main(["--list-emoji"]) == 0
    assert "1f44d" in capsys.readouterr().out


def test_exit_1_when_some_reactions_fail(monkeypatch):
    monkeypatch.setattr(like_bot, "run", lambda cfg, count, concurrency: count - 1)
    assert main(["3", "--url", VALID_URL]) == 1


def test_exit_0_when_all_reactions_succeed(monkeypatch):
    monkeypatch.setattr(like_bot, "run", lambda cfg, count, concurrency: count)
    assert main(["3", "--url", VALID_URL]) == 0
