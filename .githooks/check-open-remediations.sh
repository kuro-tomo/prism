#!/bin/bash
# forge-gates: 未解決の教訓（過去のインシデント対応チェックリスト）との照合
# 呼び出し: check-open-remediations.sh
# 出力: ステージ済み変更のキーワードが記憶帳(memory/)の未解決項目("- [ ] ")と
#       重なる場合、一覧を標準出力へ列挙し exit 1。マッチなしなら exit 0。
#
# 背景（2026-07-22 standbycrew DB暗号化案件）：過去のセキュリティレビューが
# project_incidents.md へ「正しい対応は5項目」と記録していたが、実装担当は
# project_infra.md の「詳細は◯◯参照」という一行要約のみを読み、参照先の
# 全文（未実装のまま残る項目を含む）を辿らずに実装へ進んだ。この種の見落としは
# 「次こそ気をつける」という精神論では再発を防げぬため、機械照合へ格上げする。
#
# 運用規約：memory/*.md 内の未解決項目は "- [ ] " で始める（解決済みは "- [x] "
# へ書き換えるか、行自体を削除する）。この規約に従っておらぬ既存の記憶帳は
# 本チェックの対象外となる（誤検知よりは検知漏れを許容する設計）。
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0

# worktreeからでも本体リポジトリの記憶帳を引けるよう、git-common-dir経由で
# 主リポジトリのパスを解決する（worktreeは独自の.gitを持たず共有する）。
common_dir=$(git rev-parse --git-common-dir 2>/dev/null) || exit 0
case "$common_dir" in
  /*) : ;;
  *) common_dir="$repo_root/$common_dir" ;;
esac
main_repo_root=$(cd "$(dirname "$common_dir")" && pwd)

sanitized=$(echo "$main_repo_root" | sed 's|/|-|g')
memory_dir="$HOME/.claude/projects/${sanitized}/memory"

[ -d "$memory_dir" ] || exit 0

staged_files=$(git diff --cached --name-only || true)
[ -z "$staged_files" ] && exit 0

# ステージ済みファイルのbasename（拡張子除く・小文字化・重複排除・4文字未満は除外して誤検知を抑制）
keywords=$(echo "$staged_files" \
  | xargs -I{} basename {} \
  | sed -E 's/\.[^.]+$//' \
  | tr '[:upper:]' '[:lower:]' \
  | awk 'length($0) >= 4' \
  | sort -u)

[ -z "$keywords" ] && exit 0

matched=()
while IFS= read -r kw; do
  [ -z "$kw" ] && continue
  hits=$(grep -inE "^- \[ \]" "$memory_dir"/*.md 2>/dev/null | grep -i -- "$kw" || true)
  [ -n "$hits" ] && matched+=("$hits")
done <<< "$keywords"

if [ "${#matched[@]}" -gt 0 ]; then
  echo "未解決の教訓（記憶帳の \"- [ ] \" 項目）と、今回の変更が関連する可能性がある："
  printf '%s\n' "${matched[@]}" | sort -u | sed 's/^/  - /'
  echo ""
  echo "実装がこれらの未解決項目を包含済みか、意図的に対象外とするかを確認せよ。"
  exit 1
fi

exit 0
