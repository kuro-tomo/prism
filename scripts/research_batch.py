#!/usr/bin/env python3
"""
scripts/research_batch.py — CFIM 論文評価バッチ実行（仕様書 F-022・設計書 §6.6）

使用方法:
    # Step 1: シナリオを DB に投入（初回のみ）
    python scripts/research_batch.py --seed

    # Step 2: 評価バッチ実行
    python scripts/research_batch.py --run                          # 両条件
    python scripts/research_batch.py --run --condition prism        # PRISM のみ
    python scripts/research_batch.py --run --condition single_opus  # 比較条件のみ
    python scripts/research_batch.py --run --limit 2                # 先頭2シナリオのみ（パイロット）
    python scripts/research_batch.py --run --dry-run                # 未実行件数確認のみ

    # 進捗確認
    python scripts/research_batch.py --status

環境変数:
    SUPABASE_DB_URL   — asyncpg 接続文字列
    ANTHROPIC_API_KEY — Claude API キー
    VOYAGE_API_KEY    — Voyage AI キー（embedding 生成用・省略可）

評価フロー:
    バッチ完了後、人間評価者が eval_ratings に g_orig を記録する。
    FES 計算（compute_fes）は記録後に別途 --compute-fes で実行する。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
import time
from pathlib import Path
from uuid import UUID

import anthropic
import asyncpg
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.debate import ARIF_DISCLAIMER, DebateResult, ThirdSolution, run_debate
from engine.memory import (
    STATUS_FAILED,
    create_research_session,
    get_db_pool,
    save_debate_result,
    update_session_status,
)
from engine.utils import compute_fes, compute_novelty_flag

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)

# ──────────────────────────────────────────────────────────────
# 定数
# ──────────────────────────────────────────────────────────────
SCENARIO_MD = Path(__file__).parent.parent / "docs" / "標準化シナリオ集.md"
DEBATE_MODE = "standard"
RATE_LIMIT_SLEEP = 8.0   # 実行間インターバル（秒）。API レート制限回避
MAX_RETRIES = 2          # 一時エラー時のリトライ回数

CATEGORY_MAP: dict[str, str] = {
    "A": "strategy",
    "B": "financial",
    "C": "org",
    "D": "market",
    "E": "risk",
    "F": "hr",
    "G": "digital",
}

SINGLE_OPUS_MODEL = "claude-opus-4-6"
SINGLE_OPUS_SYSTEM = (
    "あなたは経験豊富な経営コンサルタントです。"
    "社長から経営課題を提示されました。"
    "背景を十分に踏まえた上で、具体的かつ実行可能な戦略的提言を行ってください。"
    "提言には：①結論（1文・断定形）、②主な根拠（3点）、"
    "③短期アクション（3ヶ月以内・3〜5項目）、④中期ロードマップ（1〜3年）を含めること。"
)


# ──────────────────────────────────────────────────────────────
# シナリオ MD パーサー
# ──────────────────────────────────────────────────────────────

def _letter_to_category(letter: str) -> str:
    return CATEGORY_MAP.get(letter.upper(), "other")


def parse_scenarios(md_path: Path) -> list[dict]:
    """
    標準化シナリオ集.md を解析してシナリオリストを返す。

    Returns:
        list of {"title": str, "background": str, "question": str, "category": str}
    """
    text = md_path.read_text(encoding="utf-8")
    scenarios = []

    # ### A-01　タイトル のブロックを正規表現で切り出す
    blocks = re.split(r"\n(?=### [A-Z]-\d{2})", text)
    for block in blocks:
        header = re.match(r"### ([A-Z])-(\d{2})　(.+)", block)
        if not header:
            continue
        letter, _, title = header.group(1), header.group(2), header.group(3).strip()
        category = _letter_to_category(letter)

        bg_match = re.search(
            r"\*\*【背景】\*\*\n(.+?)(?=\n\*\*【問い】\*\*)", block, re.DOTALL
        )
        q_match = re.search(r"\*\*【問い】\*\*\n(.+?)(?=\n---|\Z)", block, re.DOTALL)

        if not bg_match or not q_match:
            logger.warning("パース失敗: %s", title)
            continue

        scenarios.append(
            {
                "title": title,
                "background": bg_match.group(1).strip(),
                "question": q_match.group(1).strip(),
                "category": category,
            }
        )

    return scenarios


# ──────────────────────────────────────────────────────────────
# DB ヘルパー
# ──────────────────────────────────────────────────────────────

async def seed_scenarios(pool: asyncpg.Pool, scenarios: list[dict]) -> int:
    """eval_scenarios にシナリオを投入する。title が既存なら SKIP（冪等）。"""
    inserted = 0
    async with pool.acquire() as conn:
        for sc in scenarios:
            existing = await conn.fetchval(
                "SELECT scenario_id FROM arif.eval_scenarios WHERE title = $1",
                sc["title"],
            )
            if existing:
                continue
            await conn.execute(
                """
                INSERT INTO arif.eval_scenarios (title, background, question, category)
                VALUES ($1, $2, $3, $4)
                """,
                sc["title"],
                sc["background"],
                sc["question"],
                sc["category"],
            )
            inserted += 1
    return inserted


async def load_scenarios(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT scenario_id, title, background, question, category "
            "FROM arif.eval_scenarios WHERE is_active = true ORDER BY title"
        )


async def run_already_done(
    pool: asyncpg.Pool,
    scenario_id: UUID,
    condition: str,
    run_no: int,
) -> bool:
    """UNIQUE 制約に基づき実行済み判定。"""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT run_id FROM arif.eval_runs "
            "WHERE scenario_id=$1 AND condition=$2 AND run_no=$3",
            scenario_id,
            condition,
            run_no,
        )
    return row is not None


async def insert_eval_run(
    pool: asyncpg.Pool,
    *,
    scenario_id: UUID,
    session_id: UUID,
    condition: str,
    run_no: int,
    phi_r1: float | None,
    phi_r2: float | None,
    novelty_flag: bool | None,
    g_orig: int | None = None,
    fes_score: float | None = None,
) -> None:
    """eval_runs に実行ログを挿入する。

    prism 条件では guilford_scores["originality"] から g_orig/fes_score を
    熟議直後に算出して保存する（案A・白書定義9.1の機械算出を実現）。
    single_opus 条件・guilford_scores 欠落時は NULL のまま残し
    compute_fes_batch（--compute-fes）で人間評価後に補完する。
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO arif.eval_runs
                (scenario_id, session_id, condition, run_no,
                 phi_r1, phi_r2, novelty_flag, g_orig, fes_score)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (scenario_id, condition, run_no) DO NOTHING
            """,
            scenario_id,
            session_id,
            condition,
            run_no,
            phi_r1,
            phi_r2,
            novelty_flag,
            g_orig,
            fes_score,
        )


async def print_status(pool: asyncpg.Pool) -> None:
    """実行進捗を集計して表示する。"""
    async with pool.acquire() as conn:
        total_scenarios = await conn.fetchval(
            "SELECT COUNT(*) FROM arif.eval_scenarios WHERE is_active = true"
        )
        rows = await conn.fetch(
            """
            SELECT condition, COUNT(*) AS done
            FROM arif.eval_runs
            GROUP BY condition
            """
        )
    target = (total_scenarios or 0) * 3
    done_by_condition = {r["condition"]: r["done"] for r in rows}
    print(f"\n=== CFIM 評価バッチ進捗 ===")
    print(f"シナリオ数: {total_scenarios} 件 / 目標実行数: {target} 件/条件")
    for cond in ("prism", "single_opus"):
        done = done_by_condition.get(cond, 0)
        pct = done / target * 100 if target else 0
        print(f"  {cond:12s}: {done:3d}/{target} ({pct:.1f}%)")
    print()


# ──────────────────────────────────────────────────────────────
# single_opus 条件実行
# ──────────────────────────────────────────────────────────────

async def run_single_opus(
    question: str,
    background: str,
    client: anthropic.AsyncAnthropic,
) -> DebateResult:
    """
    単一 Opus エージェントに質問し DebateResult として返す。

    比較条件として multi-agent PRISM と対照するため、
    同等の情報量（背景＋問い）を与える。
    phi_r1/r2 は N/A（単一エージェントゆえ多様性スコア不定義）→ None で保存。
    """
    t0 = time.monotonic()
    prompt = f"【経営背景】\n{background}\n\n【経営課題】\n{question}"

    message = await client.messages.create(
        model=SINGLE_OPUS_MODEL,
        max_tokens=2000,
        system=SINGLE_OPUS_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    response_text = message.content[0].text if message.content else ""
    duration = time.monotonic() - t0

    input_tokens = message.usage.input_tokens
    output_tokens = message.usage.output_tokens
    cost = input_tokens * (5.0 / 1_000_000) + output_tokens * (25.0 / 1_000_000)

    synthesis = ThirdSolution(
        conclusion=response_text[:500] if response_text else "(応答なし)",
        rationale=[{"agent": "single_opus", "point": response_text}],
        actions_short_term=[],
        actions_mid_term=[],
        assumptions=[],
        disclaimer=ARIF_DISCLAIMER,
    )

    return DebateResult(
        question=question,
        memory_context="",
        mode=DEBATE_MODE,
        synthesis=synthesis,
        diversity_score_r1=0.0,
        diversity_score_r2=0.0,
        total_cost_usd=cost,
        duration_seconds=duration,
    )


# ──────────────────────────────────────────────────────────────
# バッチ実行コア
# ──────────────────────────────────────────────────────────────

async def run_one(
    pool: asyncpg.Pool,
    client: anthropic.AsyncAnthropic,
    *,
    scenario_id: UUID,
    title: str,
    background: str,
    question: str,
    condition: str,
    run_no: int,
    dry_run: bool,
) -> bool:
    """1 件の評価ランを実行する。成功なら True を返す。"""
    label = f"[{title[:20]} | {condition} | run{run_no}]"

    if await run_already_done(pool, scenario_id, condition, run_no):
        logger.info("%s スキップ（実行済み）", label)
        return True

    if dry_run:
        logger.info("%s 未実行（dry-run）", label)
        return True

    logger.info("%s 開始", label)
    t0 = time.monotonic()
    session_id: UUID | None = None

    for attempt in range(1, MAX_RETRIES + 2):
        try:
            session_id = await create_research_session(
                pool,
                question=f"{background}\n\n{question}",
                mode=DEBATE_MODE,
                title=title,
            )

            if condition == "prism":
                result = await run_debate(
                    question=f"{background}\n\n{question}",
                    mode=DEBATE_MODE,
                    client=client,
                )
                phi_r1: float | None = result.diversity_score_r1
                phi_r2: float | None = result.diversity_score_r2
                novelty: bool | None = compute_novelty_flag(phi_r1, phi_r2)

                # 案A: guilford_scores["originality"] から g_orig を機械算出（白書定義9.1）
                g_orig: int | None = None
                fes_score: float | None = None
                if result.synthesis and result.synthesis.guilford_scores:
                    orig = result.synthesis.guilford_scores.get("originality")
                    if orig is not None and phi_r2 is not None:
                        g_orig = int(orig)
                        fes_score = compute_fes(phi_r2, g_orig, bool(novelty))
            else:
                result = await run_single_opus(question, background, client)
                phi_r1 = None
                phi_r2 = None
                novelty = None
                g_orig = None
                fes_score = None

            await save_debate_result(pool, session_id, result)

            await insert_eval_run(
                pool,
                scenario_id=scenario_id,
                session_id=session_id,
                condition=condition,
                run_no=run_no,
                phi_r1=phi_r1,
                phi_r2=phi_r2,
                novelty_flag=novelty,
                g_orig=g_orig,
                fes_score=fes_score,
            )

            elapsed = time.monotonic() - t0
            logger.info(
                "%s 完了 — %.1fs | cost=$%.4f | phi_r1=%s phi_r2=%s | g_orig=%s fes=%s",
                label,
                elapsed,
                result.total_cost_usd,
                f"{phi_r1:.3f}" if phi_r1 is not None else "N/A",
                f"{phi_r2:.3f}" if phi_r2 is not None else "N/A",
                str(g_orig) if g_orig is not None else "N/A",
                f"{fes_score:.4f}" if fes_score is not None else "N/A",
            )
            return True

        except Exception as exc:
            if session_id is not None:
                try:
                    await update_session_status(
                        pool, session_id, STATUS_FAILED,
                        error_message=str(exc)[:500],
                    )
                except Exception:
                    pass
                session_id = None  # 次の attempt で新規作成
            if attempt <= MAX_RETRIES:
                logger.warning("%s エラー（attempt %d）: %s — リトライ", label, attempt, exc)
                await asyncio.sleep(10.0)
            else:
                logger.error("%s 失敗（全リトライ消化）: %s", label, exc)
                return False

    return False


async def run_batch(
    pool: asyncpg.Pool,
    *,
    conditions: list[str],
    dry_run: bool,
    limit: int | None = None,
) -> None:
    """全シナリオ × 条件 × 反復 の評価バッチを実行する。
    limit が指定された場合は先頭 N シナリオのみ実行（パイロット用）。
    """
    scenarios = await load_scenarios(pool)
    if not scenarios:
        logger.error("eval_scenarios にシナリオがありません。先に --seed を実行してください。")
        return

    if limit is not None:
        scenarios = scenarios[:limit]
        logger.info("--limit %d 指定: %d シナリオに絞って実行", limit, len(scenarios))

    total = len(scenarios) * len(conditions) * 3
    done = 0
    failed = 0
    skipped = 0

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY が未設定です。")
        return
    client = anthropic.AsyncAnthropic(api_key=api_key)

    logger.info(
        "バッチ開始 — シナリオ %d 件 × 条件 %s × 3反復 = 計 %d 件",
        len(scenarios), conditions, total,
    )

    pending = 0  # dry_run 時の「未実行」件数（done/skipped と分離）

    for sc in scenarios:
        scenario_id = UUID(str(sc["scenario_id"]))
        for condition in conditions:
            for run_no in range(1, 4):
                success = await run_one(
                    pool,
                    client,
                    scenario_id=scenario_id,
                    title=sc["title"],
                    background=sc["background"],
                    question=sc["question"],
                    condition=condition,
                    run_no=run_no,
                    dry_run=dry_run,
                )
                if dry_run:
                    pending += 1
                elif success:
                    done += 1
                    await asyncio.sleep(RATE_LIMIT_SLEEP)
                else:
                    failed += 1
                    await asyncio.sleep(15.0)

    if dry_run:
        logger.info("dry-run 完了 — 未実行（実行予定）:%d / 合計:%d", pending, total)
    else:
        logger.info(
            "バッチ終了 — 実行:%d / スキップ:%d / 失敗:%d / 合計:%d",
            done, skipped, failed, total,
        )


# ──────────────────────────────────────────────────────────────
# FES 計算バッチ（人間評価後に実行）
# ──────────────────────────────────────────────────────────────

async def compute_fes_batch(pool: asyncpg.Pool) -> None:
    """
    g_orig が NULL の eval_runs（single_opus 条件・guilford_scores 欠落時の fallback）に対し、
    eval_ratings の 4 次元平均を g_orig 代替として FES を補完する。

    通常の prism 条件では guilford_scores["originality"] が熟議直後に記録済みゆえ
    このバッチの処理対象外となる（WHERE g_orig IS NULL が空を返す）。

    前提: 少なくとも 2 名の評価者が全 4 次元を評価済みであること。
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                er.run_id,
                er.phi_r1,
                er.phi_r2,
                ROUND(AVG(rat.score))::int AS g_orig_avg
            FROM arif.eval_runs er
            JOIN arif.eval_ratings rat ON rat.run_id = er.run_id
            WHERE er.fes_score IS NULL
              AND er.g_orig IS NULL
            GROUP BY er.run_id, er.phi_r1, er.phi_r2
            HAVING COUNT(DISTINCT rat.evaluator_id) >= 2
            """
        )

    updated = 0
    async with pool.acquire() as conn:
        for row in rows:
            phi_r2 = row["phi_r2"]
            g_orig = row["g_orig_avg"]
            if phi_r2 is None or g_orig is None:
                continue
            phi_r1 = row["phi_r1"] or 0.0
            novelty = compute_novelty_flag(phi_r1, phi_r2)
            fes = compute_fes(phi_r2, g_orig, novelty)
            await conn.execute(
                """
                UPDATE arif.eval_runs
                SET g_orig=($1)::int, novelty_flag=$2, fes_score=$3
                WHERE run_id=$4
                """,
                g_orig,
                novelty,
                fes,
                row["run_id"],
            )
            updated += 1

    logger.info("FES 更新完了: %d 件", updated)


# ──────────────────────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="CFIM 研究評価バッチスクリプト（F-022）")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--seed", action="store_true", help="標準化シナリオ集.md を DB に投入")
    group.add_argument("--run", action="store_true", help="評価バッチを実行")
    group.add_argument("--status", action="store_true", help="実行進捗を表示")
    group.add_argument("--compute-fes", action="store_true", help="人間評価後の FES 計算バッチ")
    p.add_argument(
        "--condition",
        choices=["prism", "single_opus", "both"],
        default="both",
        help="実行する条件（デフォルト: both）",
    )
    p.add_argument("--dry-run", action="store_true", help="実行せず未完了件数のみ確認")
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="先頭 N シナリオのみ実行（パイロット検証用。省略時は全件）",
    )
    return p


async def main() -> None:
    args = build_parser().parse_args()

    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        logger.error("SUPABASE_DB_URL が未設定です。")
        sys.exit(1)

    pool = await get_db_pool(db_url)

    try:
        if args.seed:
            if not SCENARIO_MD.exists():
                logger.error("シナリオ集が見当たりません: %s", SCENARIO_MD)
                sys.exit(1)
            scenarios = parse_scenarios(SCENARIO_MD)
            logger.info("パース完了: %d 件", len(scenarios))
            inserted = await seed_scenarios(pool, scenarios)
            logger.info("DB 投入完了: %d 件追加（重複スキップ含む）", inserted)

        elif args.run:
            conditions = (
                ["prism", "single_opus"] if args.condition == "both" else [args.condition]
            )
            await run_batch(
                pool,
                conditions=conditions,
                dry_run=args.dry_run,
                limit=args.limit,
            )

        elif args.status:
            await print_status(pool)

        elif args.compute_fes:
            await compute_fes_batch(pool)

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
