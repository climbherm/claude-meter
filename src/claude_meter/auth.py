"""
Mint a dedicated OAuth token for claude-meter.

We deliberately do NOT reuse the Claude Code CLI's token from
"Claude Code-credentials" Keychain. That token is shared with every
`claude` CLI inference call, so polling /api/oauth/usage on top of it
trips per-token rate limits (429 with long Retry-After).

Instead, like the claude-widget app, we run the same OAuth
authorization_code + PKCE flow that the Claude desktop app uses, using
the desktop app's session cookie. That mints a token whose only usage
is claude-meter's polling, so it has its own rate-limit budget.

Token is cached to ~/.claude-meter/token.json. On expiry we refresh; on
refresh failure or rate-limit recovery we re-run the full flow.

Linux (no desktop session cookie available): falls back to the legacy
behavior of reading ~/.claude/.credentials.json so the daemon still
works on headless boxes — but that path is rate-limit-exposed by design.
"""
from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import platform
import re
import secrets
import sqlite3
import subprocess
import sys
import time
import urllib.request

CLIENT_ID    = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
TOKEN_URL    = "https://api.anthropic.com/v1/oauth/token"
AUTHORIZE_URL_TMPL = "https://api.anthropic.com/v1/oauth/{org_id}/authorize"
REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
SCOPE        = "user:inference user:file_upload user:profile"

COOKIES_DB   = pathlib.Path.home() / "Library/Application Support/Claude/Cookies"
CACHE_DIR    = pathlib.Path.home() / ".claude-meter"
KEY_CACHE    = CACHE_DIR / "aes.key"
TOKEN_CACHE  = CACHE_DIR / "token.json"

_LINUX_PATH  = pathlib.Path.home() / ".claude" / ".credentials.json"


class AuthError(RuntimeError):
    pass


def _is_macos() -> bool:
    return platform.system() == "Darwin"


# ---------------------------------------------------------------------------
# Token cache
# ---------------------------------------------------------------------------

def _load_cached_token() -> dict | None:
    if not TOKEN_CACHE.exists():
        return None
    try:
        return json.loads(TOKEN_CACHE.read_text())
    except Exception:
        return None


def _save_token(result: dict) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    data = {
        "access_token":  result["access_token"],
        "refresh_token": result.get("refresh_token", ""),
        "expires_at":    time.time() + int(result.get("expires_in") or 28800),
        "organization_uuid": result.get("organization_uuid", ""),
    }
    TOKEN_CACHE.write_text(json.dumps(data))
    TOKEN_CACHE.chmod(0o600)


def invalidate_cached_token() -> None:
    """Drop the cached token so the next call re-mints via OAuth."""
    TOKEN_CACHE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Claude desktop cookie store (macOS only)
# ---------------------------------------------------------------------------

def _get_aes_key() -> bytes:
    if KEY_CACHE.exists():
        key = KEY_CACHE.read_bytes()
        if len(key) == 16:
            return key
    for acct in ("Claude", "Claude Key"):
        try:
            pw = subprocess.check_output(
                ["security", "find-generic-password", "-w",
                 "-s", "Claude Safe Storage", "-a", acct],
                text=True, stderr=subprocess.DEVNULL,
            ).strip()
        except subprocess.CalledProcessError:
            continue
        key = hashlib.pbkdf2_hmac("sha1", pw.encode(), b"saltysalt", 1003, dklen=16)
        CACHE_DIR.mkdir(exist_ok=True)
        KEY_CACHE.write_bytes(key)
        KEY_CACHE.chmod(0o600)
        return key
    raise AuthError(
        "Could not read 'Claude Safe Storage' from Keychain. "
        "Open the Claude desktop app and sign in first."
    )


def _decrypt_cookie(enc: bytes, key: bytes) -> str:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    dec = (
        Cipher(algorithms.AES(key), modes.CBC(b" " * 16), backend=default_backend())
        .decryptor()
        .update(enc[3:])
    )
    raw = dec[:-dec[-1]]
    m = re.search(rb"[\x20-\x7e]{8,}", raw)
    return m.group(0).decode("ascii", "ignore").strip().lstrip("`") if m else ""


def _get_cookies() -> dict:
    if not COOKIES_DB.exists():
        raise AuthError(
            f"{COOKIES_DB} not found. Open the Claude desktop app and sign in first."
        )
    key = _get_aes_key()
    conn = sqlite3.connect(str(COOKIES_DB))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name, encrypted_value FROM cookies "
            "WHERE host_key LIKE '%claude%'"
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    out: dict[str, str] = {}
    for name, enc in rows:
        val = _decrypt_cookie(bytes(enc), key)
        if val:
            out[name] = val
    return out


# ---------------------------------------------------------------------------
# OAuth flow
# ---------------------------------------------------------------------------

def _post(url: str, body: dict, session_key: str = "") -> dict:
    headers = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    if session_key:
        headers["Authorization"] = f"Bearer {session_key}"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method="POST",
    )
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def _pkce() -> tuple[str, str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    state = "".join(alphabet[b % len(alphabet)] for b in secrets.token_bytes(32))
    return verifier, challenge, state


def _fresh_oauth_token(session_key: str, org_id: str) -> dict:
    """Full PKCE authorization_code flow → fresh, dedicated token."""
    if not session_key or not org_id:
        raise AuthError(
            "Claude desktop session cookies missing (sessionKey / lastActiveOrg). "
            "Open the Claude desktop app and sign in first."
        )
    verifier, challenge, state = _pkce()

    resp = _post(
        AUTHORIZE_URL_TMPL.format(org_id=org_id),
        {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "organization_uuid": org_id,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        session_key=session_key,
    )
    redirect = resp.get("redirect_uri", "") or ""
    code_match = re.search(r"code=([^&]+)", redirect)
    if not code_match:
        raise AuthError(f"authorize response missing code: {resp}")
    code = code_match.group(1)
    state_match = re.search(r"state=([^&]+)", redirect)
    returned_state = state_match.group(1) if state_match else state

    result = _post(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "state": returned_state,
            "code_verifier": verifier,
            "expires_in": 28800,
        },
        session_key=session_key,
    )
    result.setdefault("organization_uuid", org_id)
    return result


def _refresh_oauth_token(refresh_token: str, session_key: str) -> dict:
    return _post(
        TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": refresh_token,
            "scope": SCOPE,
            "expires_in": 28800,
        },
        session_key=session_key,
    )


# ---------------------------------------------------------------------------
# Linux fallback (no desktop cookie store)
# ---------------------------------------------------------------------------

def _linux_token() -> tuple[str, str]:
    if not _LINUX_PATH.exists():
        raise AuthError(
            f"{_LINUX_PATH} not found. Install Claude Code and run `claude` to sign in, "
            "or mount your host's ~/.claude into this container."
        )
    creds = json.loads(_LINUX_PATH.read_text())
    oauth = creds.get("claudeAiOauth") or {}
    token = oauth.get("accessToken") or ""
    org   = creds.get("organizationUuid") or ""
    expires_at_ms = int(oauth.get("expiresAt") or 0)
    if token and int(time.time() * 1000) < expires_at_ms - 60_000:
        return token, org

    refresh = oauth.get("refreshToken") or ""
    if not refresh:
        raise AuthError(
            "access token expired and no refresh token found. "
            "Run `claude` and sign in again."
        )
    result = _refresh_oauth_token(refresh, session_key="")
    new_access = result.get("access_token") or ""
    if not new_access:
        raise AuthError(f"refresh response missing access_token: {result}")
    expires_in = int(result.get("expires_in") or 28800)
    creds.setdefault("claudeAiOauth", {}).update({
        "accessToken":  new_access,
        "refreshToken": result.get("refresh_token") or refresh,
        "expiresAt":    (int(time.time()) + expires_in) * 1000,
    })
    try:
        _LINUX_PATH.write_text(json.dumps(creds))
        _LINUX_PATH.chmod(0o600)
    except Exception as e:
        print(f"[warn] could not persist refreshed token: {e}", file=sys.stderr)
    return new_access, org


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_access_token() -> tuple[str, str]:
    """Return (access_token, organization_uuid)."""
    if not _is_macos():
        return _linux_token()

    cached = _load_cached_token()
    if cached and time.time() < float(cached.get("expires_at", 0)) - 60:
        return cached["access_token"], cached.get("organization_uuid", "")

    cookies = _get_cookies()
    session_key = cookies.get("sessionKey", "")
    org_id      = cookies.get("lastActiveOrg", "")

    if cached and cached.get("refresh_token"):
        try:
            result = _refresh_oauth_token(cached["refresh_token"], session_key)
            result.setdefault("organization_uuid",
                              cached.get("organization_uuid") or org_id)
            _save_token(result)
            return result["access_token"], result["organization_uuid"]
        except Exception as e:
            print(f"[warn] token refresh failed, re-running OAuth flow: {e}",
                  file=sys.stderr)
            invalidate_cached_token()

    try:
        result = _fresh_oauth_token(session_key, org_id)
    except Exception as e:
        # 2026-11-13: after a Desktop sign-out/sign-in the cookie-based mint
        # answered 400 for hours and the meter sat frozen. The Claude Code CLI
        # keeps a working token in the Keychain (refreshed by the CLI's own
        # jobs); use it rather than show stale numbers. Upstream avoids this
        # path for rate-limit reasons, so it stays a fallback, not the default.
        kc = _keychain_token()
        if kc:
            print(f"[warn] desktop-cookie OAuth mint failed ({type(e).__name__}: {str(e)[:60]}); "
                  f"falling back to the Claude Code Keychain token", file=sys.stderr)
            return kc, org_id
        raise
    _save_token(result)
    return result["access_token"], result.get("organization_uuid", org_id)


def _keychain_token() -> str:
    """Access token from the 'Claude Code-credentials' login Keychain item, or ''."""
    try:
        import subprocess
        raw = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=10).stdout.strip()
        return (json.loads(raw).get("claudeAiOauth") or {}).get("accessToken", "") or ""
    except Exception:
        return ""
