"""PRISM API — /profile（会社プロフィール管理・Phase 5T）

会社の基本前提を登録・更新・取得する。
登録された情報は build_context_prompt() によって全熟議の冒頭に固定注入される。

F-020: POST /profile/fetch — WebサイトURLから会社情報を自動抽出（保存はしない）
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import asyncpg
import httpx
from fastapi import APIRouter, Depends, Form, Query, status
from fastapi.responses import HTMLResponse, JSONResponse

from api.deps import User, get_current_user, get_pool
from api.schemas import CompanyProfileResponse, WebsiteFetchRequest, WebsiteFetchResponse
from engine.web_fetch import SSRFError, fetch_company_info

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("/json", response_model=CompanyProfileResponse)
async def get_profile_json(
    pool: asyncpg.Pool = Depends(get_pool),
    user: User = Depends(get_current_user),
) -> CompanyProfileResponse:
    """会社プロフィールをJSON取得する（GET /profile/json）。未登録の場合は空フィールドで返す。"""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT website_url, industry, scale, main_products, main_customers,
                   strengths, avoid_directions, free_context, updated_at
            FROM company_profile
            WHERE user_id = $1
            """,
            user.id,
        )
    if not row:
        return CompanyProfileResponse(
            website_url="", industry="", scale="", main_products="", main_customers="",
            strengths="", avoid_directions="", free_context="",
        )
    return CompanyProfileResponse(
        website_url=row["website_url"] or "",
        industry=row["industry"] or "",
        scale=row["scale"] or "",
        main_products=row["main_products"] or "",
        main_customers=row["main_customers"] or "",
        strengths=row["strengths"] or "",
        avoid_directions=row["avoid_directions"] or "",
        free_context=row["free_context"] or "",
        updated_at=row["updated_at"],
    )


@router.post("/fetch", response_model=WebsiteFetchResponse)
async def fetch_from_website(
    req: WebsiteFetchRequest,
    user: User = Depends(get_current_user),
) -> WebsiteFetchResponse:
    """会社WebサイトURLから会社情報を抽出する（F-020）。

    ⚠️ この endpoint は DB に保存しない。
    抽出結果はフォームに「候補」として反映され、
    社長が確認・補記してから POST /profile で保存する。
    """
    try:
        info = await fetch_company_info(req.url)
        return WebsiteFetchResponse(**info)
    except SSRFError as exc:
        logger.warning("SSRF 拒否: %s — %s", req.url, exc)
        return WebsiteFetchResponse(error=f"このURLは使用できません: {exc}")
    except ValueError as exc:
        # validate_url_safe が投げる素の ValueError（ホスト名欠落等）
        return WebsiteFetchResponse(error=f"URLの形式が正しくありません: {exc}")
    except httpx.HTTPStatusError as exc:
        return WebsiteFetchResponse(error=f"サイトの取得に失敗しました（HTTP {exc.response.status_code}）")
    except httpx.RequestError as exc:
        return WebsiteFetchResponse(error=f"サイトに接続できませんでした: {type(exc).__name__}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("fetch_from_website 予期しないエラー: %s", exc)
        return WebsiteFetchResponse(error="情報の取得中にエラーが発生しました。URLをご確認ください。")


@router.post("", response_class=HTMLResponse, status_code=status.HTTP_200_OK)
async def upsert_profile(
    pool: asyncpg.Pool = Depends(get_pool),
    user: User = Depends(get_current_user),
    first: str = Query(default=""),          # 初回セットアップフラグ
    website_url:      str = Form(default=""),
    industry:         str = Form(default=""),
    scale:            str = Form(default=""),
    main_products:    str = Form(default=""),
    main_customers:   str = Form(default=""),
    strengths:        str = Form(default=""),
    avoid_directions: str = Form(default=""),
    free_context:     str = Form(default=""),
) -> HTMLResponse:
    """会社プロフィールを登録または更新する（UPSERT・form-urlencoded 受け取り）。
    HTMX の hx-swap="outerHTML" に対応した HTML フラグメントを返す。
    """
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO company_profile
                (user_id, website_url, industry, scale, main_products, main_customers,
                 strengths, avoid_directions, free_context, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (user_id) DO UPDATE SET
                website_url      = EXCLUDED.website_url,
                industry         = EXCLUDED.industry,
                scale            = EXCLUDED.scale,
                main_products    = EXCLUDED.main_products,
                main_customers   = EXCLUDED.main_customers,
                strengths        = EXCLUDED.strengths,
                avoid_directions = EXCLUDED.avoid_directions,
                free_context     = EXCLUDED.free_context,
                updated_at       = EXCLUDED.updated_at
            """,
            user.id,
            website_url, industry, scale, main_products, main_customers,
            strengths, avoid_directions, free_context, now,
        )
    # 初回セットアップは保存後にダッシュボードへ転送（HX-Redirect）
    if first:
        return HTMLResponse(
            content='<div id="profile-saved-msg" class="saved-msg">✓ 保存しました。ダッシュボードへ移動します…</div>',
            headers={"HX-Redirect": "/"},
        )

    dt_str = now.strftime("%Y-%m-%d %H:%M")
    return HTMLResponse(
        content=(
            f'<div id="profile-saved-msg" class="saved-msg">'
            f"✓ 保存しました（{dt_str}）"
            f"</div>"
        )
    )
