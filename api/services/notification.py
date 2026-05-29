"""
api/services/notification.py — 熟議完了通知（Phase 3.5・N-002 実装）

仕様書 N-001〜N-003 のうち Phase 3.5 で実装するのは N-002（メール通知）のみ。
N-001（Web Push）は Phase 5 へ分離（Opus 設計諮問確定）。

環境変数（すべて任意。未設定時は通知をスキップして継続）:
  ARIF_SMTP_HOST  — SMTPサーバーホスト（例: smtp.gmail.com）
  ARIF_SMTP_PORT  — SMTPポート（既定: 587）
  ARIF_SMTP_USER  — SMTP認証ユーザー名
  ARIF_SMTP_PASS  — SMTP認証パスワード
  ARIF_SMTP_FROM  — 送信元アドレス（例: noreply@arif.example.com）
  ARIF_BASE_URL   — アプリのベースURL（例: https://arif.techno-chubu.co.jp）
"""
from __future__ import annotations

import logging
import os
from email.message import EmailMessage
from uuid import UUID

logger = logging.getLogger(__name__)


async def send_completion_email(
    *,
    to_email: str,
    session_id: UUID,
    title: str,
) -> bool:
    """
    熟議完了をメールで通知する（N-002）。

    SMTP 設定が不完全な場合・送信失敗の場合は警告ログを出して False を返す。
    呼び出し元のセッション処理を停止させない設計（フォールバック完全実装）。

    Args:
        to_email:   送信先メールアドレス（ユーザーの認証メール）
        session_id: 熟議セッションID
        title:      熟議タイトル

    Returns:
        bool: 送信成功なら True、スキップ/失敗なら False
    """
    smtp_host = os.environ.get("ARIF_SMTP_HOST", "")
    smtp_user = os.environ.get("ARIF_SMTP_USER", "")
    smtp_pass = os.environ.get("ARIF_SMTP_PASS", "")
    smtp_from = os.environ.get("ARIF_SMTP_FROM", "")
    base_url = os.environ.get("ARIF_BASE_URL", "http://localhost:8000")

    if not all([smtp_host, smtp_user, smtp_pass, smtp_from]):
        logger.info(
            "SMTP 設定が未完了のため通知をスキップ session_id=%s "
            "（ARIF_SMTP_HOST / USER / PASS / FROM を設定するとメール通知が有効になります）",
            session_id,
        )
        return False

    smtp_port = int(os.environ.get("ARIF_SMTP_PORT", "587"))
    result_url = f"{base_url}/deliberations/{session_id}"

    msg = EmailMessage()
    msg["From"] = smtp_from
    msg["To"] = to_email
    msg["Subject"] = f"【ARIF】熟議完了：{title}"
    msg.set_content(
        f"熟議が完了いたしました。\n\n"
        f"テーマ：{title}\n\n"
        f"結果をご確認ください：\n{result_url}\n\n"
        f"─\n"
        f"ARIF — 経営参謀AIシステム\n"
        f"本メールは自動送信されています。返信はできません。"
    )

    try:
        import aiosmtplib  # 実行時インポート（SMTP 未使用環境でもモジュールレベルで失敗しない）
        # port 465（SMTPS・implicit TLS）→ use_tls=True / start_tls=False
        # port 587（submission・STARTTLS）→ use_tls=False / start_tls=True
        _use_tls = (smtp_port == 465)
        _start_tls = not _use_tls
        await aiosmtplib.send(
            msg,
            hostname=smtp_host,
            port=smtp_port,
            username=smtp_user,
            password=smtp_pass,
            use_tls=_use_tls,
            start_tls=_start_tls,
        )
        logger.info("熟議完了通知送信成功 session_id=%s to=%s", session_id, to_email)
        return True
    except ImportError:
        logger.warning(
            "aiosmtplib がインストールされていません。"
            "`pip install aiosmtplib` でインストールしてください。"
        )
        return False
    except Exception:
        logger.exception("メール送信失敗 session_id=%s to=%s", session_id, to_email)
        return False


async def notify_completion(
    session_id: UUID,
    user_email: str,
    title: str,
    notify_email: bool = True,
) -> None:
    """
    熟議完了通知のエントリポイント（後方互換 wrapper）。

    debate_service.run_in_background() から呼ばれる。
    notify_email=True（既定）の場合に send_completion_email を呼ぶ。

    Args:
        session_id:   完了したセッションの ID
        user_email:   送信先メールアドレス
        title:        セッションのタイトル
        notify_email: メール通知を送るか（N-002）
    """
    if notify_email and user_email:
        await send_completion_email(
            to_email=user_email,
            session_id=session_id,
            title=title,
        )
