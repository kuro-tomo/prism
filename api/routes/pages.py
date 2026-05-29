"""
ARIF API — HTMX HTML ページルート

Jinja2 テンプレートを返すエンドポイント群。
SSE ストリーミングは deliberation.html + htmx-ext-sse が担う。
"""
from __future__ import annotations

import urllib.parse
from pathlib import Path
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import Response
from fastapi.templating import Jinja2Templates

from api.deps import User, get_current_user, get_current_user_optional, get_pool
from api.schemas import CompanyProfileResponse
from engine.memory import get_company_profile

router = APIRouter(tags=["pages"])

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@router.get("/", response_class=HTMLResponse, response_model=None)
async def index(
    request: Request,
    pool: asyncpg.Pool = Depends(get_pool),
    user: Optional[User] = Depends(get_current_user_optional),
) -> Response:
    """
    トップページ。未認証の場合は 302 ではなく JS リダイレクトの HTML を返す。
    302 を返すと iOS Safari 等がフラグメント（#access_token=...）を破棄するため、
    Supabase implicit flow のコールバックが機能しなくなる（仕様書 S-004）。
    会社プロフィール未設定のログイン済みユーザーは /profile へリダイレクトする。
    """
    if user is None:
        return HTMLResponse(_auth_fragment_html())

    # プロフィール未設定なら初回セットアップへ転送
    try:
        async with pool.acquire() as conn:
            profile_row = await conn.fetchrow(
                "SELECT id FROM company_profile WHERE user_id = $1", user.id
            )
        if not profile_row:
            return RedirectResponse(url="/profile?first=1", status_code=302)
    except Exception:
        pass  # DB 障害時はダッシュボードをそのまま表示

    return templates.TemplateResponse(
        request,
        "index.html",
        {"user": user},
    )


def _auth_fragment_html() -> str:
    """
    / にフラグメント付きでリダイレクトされた場合に Cookie をセットして再訪問する。
    フラグメントがなければ /login へ遷移。
    """
    return """<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"><title>ARIF</title></head>
<body>
<script>
(function(){
  var h = window.location.hash.substring(1);
  var p = new URLSearchParams(h);
  var at = p.get('access_token');
  var rt = p.get('refresh_token');
  if (at) {
    document.cookie = 'sb-access-token=' + at + '; path=/; SameSite=Lax';
    if (rt) { document.cookie = 'sb-refresh-token=' + rt + '; path=/; SameSite=Lax'; }
    window.location.replace('/');
  } else {
    window.location.replace('/login');
  }
})();
</script>
</body>
</html>"""


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    """ログインページ（Magic Link 送信フォーム）。認証不要。"""
    return templates.TemplateResponse(request, "login.html", {})


@router.get("/deliberations/list", response_class=HTMLResponse)
async def deliberations_list_html(
    request: Request,
    user: User = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> HTMLResponse:
    """セッション一覧 HTML フラグメント（index.html の HTMX hx-get 用）。"""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, title, mode, status, created_at
            FROM deliberation_sessions
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT 30
            """,
            user.id,
        )
    if not rows:
        return HTMLResponse('<p class="no-sessions">まだ熟議の記録がございません。</p>')

    parts: list[str] = []
    for r in rows:
        sid      = str(r["id"])
        title_s  = _esc(r["title"] or "")
        mode_s   = _esc(r["mode"] or "")
        st       = r["status"] or "pending"
        dt       = r["created_at"].strftime("%Y/%m/%d %H:%M") if r["created_at"] else ""
        label    = {"completed": "完了", "failed": "失敗"}.get(st, st)
        parts.append(
            f'<a href="/deliberations/{sid}" class="session-card">'
            f'<div class="session-card-title">{title_s}</div>'
            f'<div class="session-card-meta">'
            f'<span class="pill pill-mode">{mode_s}</span>'
            f'<span class="pill pill-{_esc(st)}">{_esc(label)}</span>'
            f'<span class="session-date">{_esc(dt)}</span>'
            f'</div></a>'
        )
    return HTMLResponse("".join(parts))


def _esc(s: str) -> str:
    """HTML エスケープ（XSS 防止）。"""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
         .replace("'", "&#39;")
    )


@router.get("/deliberations/{session_id}", response_class=HTMLResponse)
async def deliberation_page(
    session_id: UUID,
    request: Request,
    question: str = "",
    title: str = "",
    mode: str = "standard",
    user: User = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> HTMLResponse:
    """
    熟議進行画面。

    新規実行時は question/title/mode クエリパラメータを持つ URL で遷移。
    完了済みセッション（status='completed'/'failed'）は静的レンダリング画面を返す。
    進行中セッションのみ native EventSource で /deliberations/{id}/stream に接続する。
    （Critical-1 修正：完了済み閲覧による熟議再実行を防止・Opus指摘対応）
    """
    import json as _json

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT title, question, mode, status, third_solution,
                   total_cost_usd, duration_seconds, created_at
            FROM deliberation_sessions
            WHERE id=$1 AND user_id=$2
            """,
            session_id, user.id,
        )

    if row:
        title    = row["title"]
        question = row["question"]
        mode     = row["mode"]
        status   = row["status"]
    elif not question:
        raise HTTPException(status_code=404, detail="セッションが見つかりません。")
    else:
        # 新規セッション（まだ DB 行なし・pending 状態から SSE 接続）
        status = "pending"

    # ── 完了済み・失敗済み → 静的レンダリング（EventSource 接続なし）─────
    if status in ("completed", "failed"):
        third_solution = None
        if row and row["third_solution"]:
            raw = row["third_solution"]
            third_solution = _json.loads(raw) if isinstance(raw, str) else raw

        cost = float(row["total_cost_usd"]) if row and row["total_cost_usd"] else 0.0
        duration = int(row["duration_seconds"]) if row and row["duration_seconds"] else 0
        created_at = row["created_at"].strftime("%Y/%m/%d %H:%M") if row and row["created_at"] else ""

        return templates.TemplateResponse(
            request,
            "deliberation_result.html",
            {
                "user": user,
                "session_id": str(session_id),
                "title": title,
                "question": question,
                "mode": mode,
                "status": status,
                "third_solution": third_solution,
                "total_cost_usd": cost,
                "duration_seconds": duration,
                "created_at": created_at,
            },
        )

    # ── 進行中 → ストリーミング画面（EventSource 接続あり）─────────────────
    stream_url = (
        f"/deliberations/{session_id}/stream?"
        + urllib.parse.urlencode({"question": question, "title": title, "mode": mode})
    )

    return templates.TemplateResponse(
        request,
        "deliberation.html",
        {
            "user": user,
            "session_id": str(session_id),
            "title": title,
            "question": question,
            "mode": mode,
            "stream_url": stream_url,
        },
    )


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    pool: asyncpg.Pool = Depends(get_pool),
    user: User = Depends(get_current_user),
) -> HTMLResponse:
    """会社プロフィール登録・編集ページ（Phase 5T）。"""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT website_url, industry, scale, main_products, main_customers,
                   strengths, avoid_directions, free_context, updated_at
            FROM company_profile WHERE user_id = $1
            """,
            user.id,
        )
    profile = CompanyProfileResponse(
        website_url=row["website_url"] if row else "",
        industry=row["industry"] if row else "",
        scale=row["scale"] if row else "",
        main_products=row["main_products"] if row else "",
        main_customers=row["main_customers"] if row else "",
        strengths=row["strengths"] if row else "",
        avoid_directions=row["avoid_directions"] if row else "",
        free_context=row["free_context"] if row else "",
        updated_at=row["updated_at"] if row else None,
    )
    return templates.TemplateResponse(
        request,
        "profile.html",
        {"user": user, "profile": profile},
    )
