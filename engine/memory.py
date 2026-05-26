"""
記憶層モジュール — Supabase連携

責務:
  - 過去の議論ログを保存・取得
  - 会社コンテキスト（基本情報・課題感）の管理
  - 次回議論へ注入するメモリ文字列の生成

TODO（/advisor 諮問後に実装）:
  - Supabaseテーブル定義の確定
  - build_memory_context() の本実装
  - save_debate() の本実装
"""


def build_memory_context(limit: int = 5) -> str:
    """
    直近 limit 件の議論と会社基本情報を
    エージェントへ注入するテキストとして返す。
    """
    raise NotImplementedError("設計諮問（/advisor）後に実装")


def save_debate(result) -> None:
    """
    議論結果をSupabaseに永続化する。
    """
    raise NotImplementedError("設計諮問（/advisor）後に実装")
