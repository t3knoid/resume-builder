"""Owner sign-in for the tracker website, and protection against forged requests (issue #30, PRD 8.8, D32).

Without a session the website is read-only, as before. A session only says *who* is calling; what
they may do is still decided by the one policy check in `invoke` (B3, B4).

- Sign-in takes an owner API token or a one-time code from `rb web-login`. Agent tokens can't sign in.
- The session cookie is `HttpOnly`, `SameSite=Strict`, scoped to `/u/<user>/`, and carries a random
  session id plus an HMAC made with a per-install secret kept in `RB_DB_ROOT` (outside the user's
  data folder). The database stores only a hash of the session id. Sessions end after 12 hours idle,
  7 days in all, or on sign-out.
- Every state-changing request is a `POST` with a CSRF token derived from the session, from the
  site's own origin (`Origin`, or `Referer`), with a local `Host` name (against DNS rebinding).

The site is served over plain HTTP on 127.0.0.1, so the cookie can't be `Secure`; it never leaves
the user's machine.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException, Request

from ..clock import now_iso
from ..space import App, UserSpace

COOKIE = "rb_session"
IDLE = timedelta(hours=12)
LIFETIME = timedelta(days=7)
TOUCH_EVERY = timedelta(minutes=1)
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def secret(app: App) -> bytes:
    """The per-install signing secret, created on first use with owner-only permissions."""
    path = Path(app.settings.db_root) / ".web-secret"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w") as fh:
                fh.write(secrets.token_hex(32))
    return path.read_text().strip().encode()


def _sign(key: bytes, value: str) -> str:
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- one-time sign-in codes (made by `web_login_link`)

def use_login_code(space: UserSpace, code: str) -> bool:
    with space.repo.transaction():
        return space.repo.use_login_code(_hash(code), now_iso())


# ---------------------------------------------------------------- sessions

def start_session(app: App, space: UserSpace) -> str:
    """Create an owner session; returns the cookie value."""
    sid = secrets.token_urlsafe(32)
    with space.repo.transaction():
        space.repo.add_web_session(_hash(sid), "owner", _iso(_now() + LIFETIME))
    return f"{sid}.{_sign(secret(app), sid)}"


def session_id(app: App, space: UserSpace, cookie: str | None) -> str | None:
    """The session id behind a cookie, if the cookie is genuine and the session still valid."""
    if not cookie or "." not in cookie:
        return None
    sid, sig = cookie.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(secret(app), sid)):
        return None
    row = space.repo.web_session(_hash(sid))
    if row is None or row["revoked"]:
        return None
    now = _now()
    if now >= datetime.fromisoformat(row["expires_at"]):
        return None
    last = datetime.fromisoformat(row["last_seen_at"])
    if now - last >= IDLE:
        return None
    if now - last >= TOUCH_EVERY:
        with space.repo.transaction():
            space.repo.touch_web_session(_hash(sid), _iso(now))
    return sid


def end_session(space: UserSpace, sid: str) -> None:
    with space.repo.transaction():
        space.repo.revoke_web_session(_hash(sid))


def csrf_token(app: App, user: str, sid: str) -> str:
    return _sign(secret(app), f"csrf|{user}|{sid}")


def check_csrf(app: App, user: str, sid: str, token: str | None) -> None:
    if not token or not hmac.compare_digest(token, csrf_token(app, user, sid)):
        raise HTTPException(403, "This form has expired or didn't come from this site. Reload the page and try again.")


# ---------------------------------------------------------------- where a request comes from

def allowed_hosts(app: App | None = None) -> set[str]:
    """The host names the site answers to: the App's own list if it was given one (`web_hosts`),
    otherwise the local names plus any in RB_WEB_HOSTS (comma-separated, names that resolve to this
    machine only)."""
    if app is not None and getattr(app, "web_hosts", None) is not None:
        return set(app.web_hosts)
    extra = {h.strip().lower() for h in os.environ.get("RB_WEB_HOSTS", "").split(",") if h.strip()}
    return LOCAL_HOSTS | extra


def host_allowed(app: App | None, host_header: str | None) -> bool:
    """True when the Host header names an allowed host (any port). Every request is checked (#31)."""
    return bool(host_header and host_header.strip()) and _host_name(host_header) in allowed_hosts(app)


def _host_name(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def check_request_origin(request: Request, app: App | None = None) -> None:
    """For every website POST: an allowed Host, and an Origin (or Referer) that is this site."""
    host = request.headers.get("host", "")
    if not host_allowed(app, host):
        raise HTTPException(403, "This address isn't allowed. Open the tracker at http://localhost.")
    expected = f"{request.url.scheme}://{host}".lower()
    origin = request.headers.get("origin")
    if origin:
        if origin.lower() != expected:
            raise HTTPException(403, "The request came from another site.")
        return
    referer = (request.headers.get("referer") or "").lower()
    if not (referer == expected or referer.startswith(expected + "/")):
        raise HTTPException(403, "The request didn't say where it came from. Use the buttons on the site.")


SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
                               "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
}
