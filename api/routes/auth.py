"""
ARIF API — 認証ルート（Magic Link / Supabase Auth）

仕様書 S-004・Opus 設計確定：Supabase Auth + Magic Link（Email 送信）。
PKCE フロー（S256）：code_verifier を HttpOnly Cookie に保管し、
/auth/callback でサーバーサイドトークン交換を行う。JS によるフラグメント読取不要。
"""
from __future__ import annotations

import base64
import hashlib
import html
import os
import secrets

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(prefix="/auth", tags=["auth"])

# カンマ区切りで複数アドレスを許可（例: "a@x.com,b@y.com"）
_ALLOWED_EMAILS: set[str] = {
    e.strip().lower()
    for e in os.environ.get("ARIF_ALLOWED_EMAIL", "").split(",")
    if e.strip()
}
_PKCE_COOKIE   = "arif-cv"   # code_verifier 一時保管 Cookie 名
_PKCE_MAX_AGE  = 1800        # 30 分で失効（メール確認の猶予）


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
      - code_verifier を HttpOnly Cookie として保管
      - /auth/callback でサーバーサイドトークン交換
    """
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
    # verifier を redirect_to に埋め込む（ブラウザ外からの送信でも Cookie 不要になる）
    redirect_to_with_cv = f"{redirect_to}?cv={verifier}"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{supabase_url}/auth/v1/otp",
            headers={"apikey": supabase_key, "Content-Type": "application/json"},
            # redirect_to は URL クエリパラメータで渡す（GoTrue は body の options を読まない）
            params={"redirect_to": redirect_to_with_cv},
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

    # code_verifier を HttpOnly Cookie に保管（callback まで使用）
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
    """
    if error:
        return HTMLResponse(
            f'<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">'
            f'<title>PRISM — 認証エラー</title></head><body>'
            f'<p style="font-family:sans-serif">認証エラー: {html.escape(error)}'
            f'<br><a href="/login">ログインに戻る</a></p></body></html>',
            status_code=400,
        )

    if not code:
        # PKCE フロー必須。code なしは正常ではないため拒否する
        return HTMLResponse(
            '<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">'
            '<title>PRISM — 認証エラー</title></head><body>'
            '<p style="font-family:sans-serif">認証コードが見つかりません。'
            '<a href="/login">再度ログインしてください</a></p></body></html>',
            status_code=400,
        )

    # PKCE フロー：code_verifier を Cookie から取得
    import logging
    _log = logging.getLogger("arif.auth")

    # Cookie 優先、なければ URL パラメータの cv にフォールバック
    verifier = request.cookies.get(_PKCE_COOKIE, "") or request.query_params.get("cv", "")
    _log.info("CALLBACK: code=%s... verifier_found=%s source=%s cookies=%s",
              code[:8], bool(verifier),
              "cookie" if request.cookies.get(_PKCE_COOKIE) else "url_param",
              list(request.cookies.keys()))

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
            _log.warning("CALLBACK: token_exchange failed status=%d verifier_found=%s code=%s msg=%s",
                         resp.status_code, bool(verifier), err_code, err_msg)
            return HTMLResponse(
                f'<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">'
                f'<title>PRISM — 認証エラー</title></head><body style="font-family:sans-serif;padding:2rem">'
                f'<p>認証に失敗しました。</p>'
                f'<p style="color:#f87171;font-size:.9rem">コード: {err_code}<br>詳細: {err_msg}'
                f'<br>Cookie: {"あり" if verifier else "<b>なし（PKCEセッション切れ）</b>"}</p>'
                f'<a href="/login">再度ログインしてください</a>'
                f'</body></html>',
                status_code=401,
            )

        data          = resp.json()
        access_token  = data.get("access_token",  "")
        refresh_token = data.get("refresh_token", "")
        _log.info("CALLBACK: token_exchange ok, at_len=%d rt_len=%d at_prefix=%s",
                  len(access_token), len(refresh_token), access_token[:20])

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


