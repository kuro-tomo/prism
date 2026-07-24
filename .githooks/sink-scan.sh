#!/bin/bash
# forge-gates 実弾統制・砲口走査（pre-commit用）
# ステージ済み差分の「追加行」を砲口パターンと照合する。コメント行は除外する
# （是正済みの旨をコメントで残しても偽陽性にせぬため——2026-07-23 demo_handler.pyの
# 是正コメント2行が生砲口と誤検知された実例を踏まえた設計）。
#
# 判定：
#   - テスト/スペック領域（test・spec・e2e等）：安全網であり危険源でないため走査対象外
#   - デモ/モック領域（demo・mock・sandbox等）で生砲口を追加 → ハードブロック（exit 2）
#     デモ経路はガード付き中継（notifier.notify等・合成テナントno-op）を経由すべしという
#     「砲口封印」の原則に反するため。
#   - その他のファイルで生砲口を追加 → 軟R旗（exit 1）。砲口の本体（notifier・sns_service・
#     stripe_handler等）に触れる変更はR旗相当ゆえ、トレーラーによる明示的な認識を求める。
#   - 生砲口なし → exit 0
#
# このファイルは各リポジトリの .githooks/ へ install.sh によって複製される（自己完結）。
set -uo pipefail

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
hook_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

default_sinks="$hook_dir/sinks-default.txt"
custom_sinks="$repo_root/.forge/sinks.txt"

patterns=()
for f in "$default_sinks" "$custom_sinks"; do
  [ -f "$f" ] || continue
  while IFS= read -r line; do
    case "$line" in ''|\#*) continue ;; esac
    patterns+=("$line")
  done < "$f"
done
[ "${#patterns[@]}" -eq 0 ] && exit 0
combined=$(IFS='|'; echo "${patterns[*]}")

# コメントのみの行を落とす（各言語の行頭コメント記号）。生の呼び出しはコメント単独行に
# なり得ぬため、この除外で危険を見逃すことはない。
comment_re='^[[:space:]]*(#|//|\*|/\*|<!--|--)'
demo_re='(demo|mock|sandbox|fixture|sample|seed)'
test_re='(test|spec|__tests__|/tests?/|e2e|\.stories\.)'
# 散文・データ・統制ディレクトリは砲口を実行し得ぬため対象外
skip_re='(^|/)(\.githooks|\.forge|node_modules|dist|build|coverage)/|\.(md|rst|txt|json|lock|map|svg|png|jpe?g|gif|ico|woff2?|ttf|csv|snap|ipynb)$'

staged=$(git diff --cached --name-only --diff-filter=ACM || true)
[ -z "$staged" ] && exit 0

hard=()
soft=()
while IFS= read -r f; do
  [ -z "$f" ] && continue
  echo "$f" | grep -qE "$skip_re" && continue
  added=$(git diff --cached -- "$f" 2>/dev/null \
    | grep -E '^\+' | grep -vE '^\+\+\+' | sed 's/^+//' \
    | grep -vE "$comment_re" || true)
  [ -z "$added" ] && continue
  echo "$added" | grep -qE "$combined" || continue

  low=$(echo "$f" | tr '[:upper:]' '[:lower:]')
  if echo "$low" | grep -qE "$test_re"; then
    continue
  elif echo "$low" | grep -qE "$demo_re"; then
    hard+=("$f")
  else
    soft+=("$f")
  fi
done <<< "$staged"

if [ "${#hard[@]}" -gt 0 ]; then
  echo "実弾統制・ハードブロック：デモ/モック領域のコードが外部発信の生砲口を直接参照している。"
  printf '%s\n' "${hard[@]}" | sort -u | sed 's/^/  - /'
  echo "  デモ経路は必ずガード付きの中継（notifier.notify等・合成テナントの無条件no-op）を"
  echo "  経由せよ。生砲口の直接呼び出しは砲口封印の原則に反する（2026-07-23実課金事故の教訓）。"
  exit 2
fi

if [ "${#soft[@]}" -gt 0 ]; then
  echo "実弾（外部発信の生砲口）に触れる変更を検知："
  printf '%s\n' "${soft[@]}" | sort -u | sed 's/^/  - /'
  exit 1
fi

exit 0
