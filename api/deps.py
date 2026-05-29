"""
ARIF API — 共通 Dependency

FastAPI Dependency として各エンドポイントに注入する。
Opus 設計諮問確定：Dependency 方式（Middleware でなく）を採用。
テスト時は app.dependency_overrides で差し替え可能。

JWT 検証：Supabase は ES256（ECDSA P-256）に移行済み。
JWKS エンドポイントから公開鍵を取得して検証する（HS256 も後方互換で維持）。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

import asyncpg
import httpx
import jwt
from fastapi import HTTPException, Request, status

_log = logging.getLogger("arif.auth")

# ===========================
# ユーザー型
# ===========================

@dataclass
class User:
    id: str           # Supabase の auth.users.id（UUID 文字列）
    email: str


# ===========================
# DB コネクションプール
# ===========================

async def get_pool(request: Request) -> asyncpg.Pool:
    """app.state.pool を返す。main.py の lifespan で設定済み。"""
    return request.app.state.pool  # type: ignore[return-value]


# ===========================
# JWKS キャッシュ（ES256 公開鍵）
# ===========================

_jwks_keys:  list[dict[str, Any]] = []
_jwks_fetched_at: datetime | None = None
_JWKS_TTL = timedelta(hours=24)


async def _get_jwks_keys() -> list[dict[str, Any]]:
    """Supabase JWKS から公開鍵リストを取得（24時間キャッシュ）。"""
    global _jwks_keys, _jwks_fetched_at
    now = datetime.now(UTC)
    if _jwks_fetched_at is not None and now - _jwks_fetched_at < _JWKS_TTL and _jwks_keys:
        return _jwks_keys

    supabase_url = os.environ.get("SUPABASE_URL", "")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{supabase_url}/auth/v1/.well-known/jwks.json")
            resp.raise_for_status()
            _jwks_keys = resp.json().get("keys", [])
            _jwks_fetched_at = now
            _log.info("JWKS updated: %d keys", len(_jwks_keys))
    except Exception as exc:  # ネットワーク障害時はキャッシュを維持
        _log.warning("JWKS fetch failed: %s  (using cached %d keys)", exc, len(_jwks_keys))

    return _jwks_keys


def _public_key_from_jwk(key_data: dict[str, Any]) -> Any:
    """JWK dict から PyJWT が使える公開鍵オブジェクトを返す。"""
    from jwt.algorithms import ECAlgorithm, RSAAlgorithm
    kty = key_data.get("kty", "EC")
    if kty == "EC":
        return ECAlgorithm.from_jwk(json.dumps(key_data))
    return RSAAlgorithm.from_jwk(json.dumps(key_data))


# ===========================
# 認証 Dependency（仕様書 S-004）
# ===========================

async def get_current_user(request: Request) -> User:
    """
    Cookie sb-access-token から Supabase JWT を検証してユーザーを返す。

    ES256（新 Supabase JWKS）と HS256（旧 SUPABASE_JWT_SECRET）の両方をサポート。

    Raises:
        HTTPException(401): トークンなし・不正・期限切れ
    """
    token = request.cookies.get("sb-access-token")
    if not token:
        _log.warning("AUTH: sb-access-token not found. cookies=%s", list(request.cookies.keys()))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="認証が必要でございます。",
            headers={"Location": "/login"},
        )

    # JWT ヘッダーからアルゴリズムを確認（検証前に読み取る）
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        _log.warning("AUTH: invalid JWT header: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="認証トークンが無効です。")

    alg = header.get("alg", "HS256")
    kid = header.get("kid")

    try:
        if alg == "HS256":
            # 旧 Supabase：対称鍵（SUPABASE_JWT_SECRET）で検証
            jwt_secret = os.environ.get("SUPABASE_JWT_SECRET", "")
            if not jwt_secret:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="SUPABASE_JWT_SECRET が設定されておりません。",
                )
            payload = jwt.decode(
                token, jwt_secret, algorithms=["HS256"], audience="authenticated"
            )
        else:
            # 新 Supabase（ES256 / RS256）：JWKS 公開鍵で検証
            keys = await _get_jwks_keys()
            # kid が一致するキーを探す。見つからなければ最初のキーを使う
            key_data = next((k for k in keys if k.get("kid") == kid), None)
            if key_data is None and keys:
                key_data = keys[0]
            if key_data is None:
                _log.error("AUTH: no JWKS key for kid=%s", kid)
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="認証鍵が取得できません。")

            public_key = _public_key_from_jwk(key_data)
            payload = jwt.decode(
                token, public_key, algorithms=[alg], audience="authenticated"
            )

    except jwt.ExpiredSignatureError:
        _log.warning("AUTH: token expired. prefix=%s", token[:20])
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="セッションの有効期限が切れました。")
    except jwt.PyJWTError as exc:
        _log.warning("AUTH: JWT validation failed: %s  alg=%s  prefix=%s", type(exc).__name__, alg, token[:20])
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="認証トークンが無効です。")

    return User(id=payload["sub"], email=payload.get("email", ""))


async def get_current_user_optional(request: Request) -> Optional[User]:
    """
    認証オプション版。未認証の場合は None を返す（HTML ページのリダイレクト用）。
    """
    try:
        return await get_current_user(request)
    except HTTPException:
        return None
