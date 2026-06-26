"""
PRISM API — 認証ルート（Magic Link / Supabase Auth）

仕様書 S-004・Opus 設計確定：Supabase Auth + Magic Link（Email 送信）。
PKCE フロー（S256）：code_verifier をサーバー側変数 + Cookie + DB で三重保持し、
/auth/callback でサーバーサイドトークン交換を行う。JS によるフラグメント読取不要。
verifier 取得優先順：① Cookie ② サーバー変数 ③ DB（Render スピンダウン後の復元）
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import logging
import os
import secrets

import asyncpg
from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(prefix="/auth", tags=["auth"])

_log = logging.getLogger("arif.auth")

# カンマ区切りで複数アドレスを許可（例: "a@x.com,b@y.com"）
_ALLOWED_EMAILS: set[str] = {
    e.strip().lower()
    for e in os.environ.get("ARIF_ALLOWED_EMAIL", "").split(",")
    if e.strip()
}
_PKCE_COOKIE   = "arif-cv"   # code_verifier 一時保管 Cookie 名
_PKCE_MAX_AGE  = 1800        # 30 分で失効（メール確認の猶予）

# サーバー側 verifier キャッシュ（単一ユーザー用・Cookie の取れない環境からの送信に対応）
_server_verifier: str = ""


async def _store_verifier(verifier: str) -> None:
    """verifier を arif.pending_verifiers に保存（Render スピンダウン対策）。失敗しても続行。"""
    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        return
    try:
        conn = await asyncpg.connect(db_url)
        try:
            await conn.execute("DELETE FROM arif.pending_verifiers WHERE expires_at <= now()")
            await conn.execute(
                "INSERT INTO arif.pending_verifiers(verifier) VALUES($1) ON CONFLICT DO NOTHING",
                verifier,
            )
        finally:
            await conn.close()
    except Exception as exc:
        _log.warning("STORE_VERIFIER: DB error (non-fatal): %s", exc)


async def _fetch_and_consume_verifier() -> str:
    """DB から未失効 verifier を取得・削除して返す。なければ空文字。"""
    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        return ""
    try:
        conn = await asyncpg.connect(db_url)
        try:
            row = await conn.fetchrow(
                "DELETE FROM arif.pending_verifiers "
                "WHERE verifier IN ("
                "  SELECT verifier FROM arif.pending_verifiers "
                "  WHERE expires_at > now() LIMIT 1"
                ") RETURNING verifier"
            )
            return row["verifier"] if row else ""
        finally:
            await conn.close()
    except Exception as exc:
        _log.warning("FETCH_VERIFIER: DB error (non-fatal): %s", exc)
        return ""


def _make_pkce() -> tuple[str, str]:
    """PKCE S256: code_verifier と code_challenge を生成して返す。"""
    verifier  = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


@router.post("/magic-link")
async def send_magic_link(email: str = Form(...)) -> HTMLResponse:
    """
    Supabase Auth REST API を呼んで Magic Link メールを送信する。
    HTMX フォーム（application/x-www-form-urlencoded）を受け付ける。
    成功・失敗ともに HTMX が swap できる HTML フラグメントを返す。
    許可 Email 以外は拒否（仕様書 S-004）。

    PKCE フロー：
      - code_challenge を OTP リクエストに付与
      - code_verifier を Cookie（ブラウザ送信時）＋サーバー変数（curl 送信時）で二重保持
      - /auth/callback でサーバーサイドトークン交換
    """
    global _server_verifier

    if _ALLOWED_EMAILS and email.lower() not in _ALLOWED_EMAILS:
        return HTMLResponse(
            '<p style="color:#f87171">このメールアドレスは許可されておりません。</p>'
        )

    import httpx

    supabase_url = os.environ["SUPABASE_URL"]
    supabase_key = os.environ["SUPABASE_KEY"]
    base_url     = os.environ.get("ARIF_BASE_URL", "http://localhost:8001")
    redirect_to  = f"{base_url}/auth/callback"

    verifier, challenge = _make_pkce()
    _server_verifier = verifier  # サーバー側に保持（同一プロセス継続中の高速パス）
    asyncio.ensure_future(_store_verifier(verifier))  # DB 永続化（スピンダウン対策・非同期）

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{supabase_url}/auth/v1/otp",
            headers={"apikey": supabase_key, "Content-Type": "application/json"},
            params={"redirect_to": redirect_to},
            json={
                "email": email,
                "code_challenge":        challenge,
                "code_challenge_method": "s256",
            },
        )

    if resp.status_code == 429:
        return HTMLResponse(
            '<p style="color:#fb923c">送信が集中しております。数分後に再試行ください。</p>'
        )
    if resp.status_code not in (200, 201):
        return HTMLResponse(
            f'<p style="color:#f87171">送信に失敗しました（{resp.status_code}）。しばらくお待ちの上、再試行ください。</p>'
        )

    _log.info("MAGIC_LINK: sent to=%s verifier_stored=True", email)

    # Cookie にも保管（ブラウザから送信した場合の主経路）
    response = HTMLResponse(
        '<p style="color:#4ade80">✓ 認証リンクを送信しました。メールをご確認ください。</p>'
    )
    response.set_cookie(
        _PKCE_COOKIE,
        verifier,
        httponly=True,
        samesite="lax",
        max_age=_PKCE_MAX_AGE,
        secure=True,
    )
    return response


@router.get("/callback", response_model=None)
async def auth_callback(
    request: Request,
    response: Response,
    code: str | None = None,
    error: str | None = None,
) -> HTMLResponse | RedirectResponse:
    """
    Magic Link クリック後のリダイレクト先。
    PKCE フロー：code + code_verifier でサーバーサイドトークン交換。
    HttpOnly Cookie をセットして / へリダイレクト（JS 不要）。
    verifier 取得優先順：① Cookie ② サーバー変数 ③ DB（コールドスタート復元）
    """
    global _server_verifier

    if error:
        return HTMLResponse(
            f'<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">'
            f'<title>PRISM — 認証エラー</title></head><body>'
            f'<p style="font-family:sans-serif">認証エラー: {html.escape(error)}'
            f'<br><a href="/login">ログインに戻る</a></p></body></html>',
            status_code=400,
        )

    if not code:
        return HTMLResponse(
            '<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">'
            '<title>PRISM — 認証エラー</title></head><body>'
            '<p style="font-family:sans-serif">認証コードが見つかりません。'
            '<a href="/login">再度ログインしてください</a></p></body></html>',
            status_code=400,
        )

    # verifier 取得：① Cookie → ② サーバー変数 → ③ DB（コールドスタート復元）
    cookie_verifier = request.cookies.get(_PKCE_COOKIE, "")
    if cookie_verifier:
        verifier = cookie_verifier
        source   = "cookie"
    elif _server_verifier:
        verifier = _server_verifier
        source   = "server_var"
    else:
        verifier = await _fetch_and_consume_verifier()
        source   = "db" if verifier else "none"

    _log.info("CALLBACK: code=%s... verifier_found=%s source=%s", code[:8], bool(verifier), source)

    # 消費したらサーバー変数をクリア
    if verifier and verifier == _server_verifier:
        _server_verifier = ""

    import httpx

    supabase_url = os.environ["SUPABASE_URL"]
    supabase_key = os.environ["SUPABASE_KEY"]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{supabase_url}/auth/v1/token?grant_type=pkce",
            headers={"apikey": supabase_key, "Content-Type": "application/json"},
            json={"auth_code": code, "code_verifier": verifier},
        )
        if resp.status_code != 200:
            try:
                err_body = resp.json()
                err_code = html.escape(err_body.get("error_code", err_body.get("error", str(resp.status_code))))
                err_msg  = html.escape(err_body.get("message", err_body.get("error_description", "不明")))
            except Exception:
                err_code, err_msg = str(resp.status_code), "レスポンス解析失敗"
            _log.warning("CALLBACK: failed status=%d source=%s err=%s msg=%s",
                         resp.status_code, source, err_code, err_msg)
            return HTMLResponse(
                f'<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">'
                f'<title>PRISM — 認証エラー</title></head><body style="font-family:sans-serif;padding:2rem">'
                f'<p>認証に失敗しました。</p>'
                f'<p style="color:#f87171;font-size:.9rem">コード: {err_code}<br>詳細: {err_msg}'
                f'<br>verifier 取得元: {source}</p>'
                f'<a href="/login">再度ログインしてください</a>'
                f'</body></html>',
                status_code=401,
            )

        data          = resp.json()
        access_token  = data.get("access_token",  "")
        refresh_token = data.get("refresh_token", "")
        _log.info("CALLBACK: ok source=%s at_len=%d", source, len(access_token))

    redirect = RedirectResponse(url="/", status_code=303)
    redirect.delete_cookie(_PKCE_COOKIE)
    redirect.set_cookie("sb-access-token",  access_token,  httponly=True, samesite="lax", secure=True)
    redirect.set_cookie("sb-refresh-token", refresh_token, httponly=True, samesite="lax", secure=True)
    return redirect


@router.post("/logout")
async def logout(response: Response) -> RedirectResponse:
    """Cookie を削除してログアウト。"""
    redirect = RedirectResponse(url="/login", status_code=303)
    redirect.delete_cookie("sb-access-token")
    redirect.delete_cookie("sb-refresh-token")
    return redirect
