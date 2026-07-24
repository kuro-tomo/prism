#!/bin/bash
# forge-gates 実弾統制・CI掃討（no-live-fire）
# リポジトリの追跡ファイル全体を砲口パターンで走査し、許可表(.forge/sinks-allow.txt)に
# 無いファイルが生砲口を含めば失敗する。差分審査（flow）が捕捉できぬ、統制施行以前から
# main に眠る実弾経路（stock）を掃くための検査。CIの必須チェックへ組み込む。
#
# 砲口を一切持たぬリポジトリでは「武装済みの空の罠」として機能し、将来誰かが実弾
# （Twilio・Stripe等）を持ち込んだ瞬間に機械が気づく。
#
# コメント行は除外する（是正済みの旨の注釈を偽陽性にせぬため）。
set -uo pipefail

repo_root=$(git rev-parse --show-toplevel)
hook_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

default_sinks="$hook_dir/sinks-default.txt"
custom_sinks="$repo_root/.forge/sinks.txt"
allow="$repo_root/.forge/sinks-allow.txt"

patterns=()
for f in "$default_sinks" "$custom_sinks"; do
  [ -f "$f" ] || continue
  while IFS= read -r line; do
    case "$line" in ''|\#*) continue ;; esac
    patterns+=("$line")
  done < "$f"
done
if [ "${#patterns[@]}" -eq 0 ]; then
  echo "実弾統制・CI掃討：砲口パターン未定義（sinks-default.txt欠落）。検査をskipする。"
  exit 0
fi
combined=$(IFS='|'; echo "${patterns[*]}")
comment_re='^[[:space:]]*(#|//|\*|/\*|<!--|--)'
# 散文・データ・統制ディレクトリは砲口を「実行」し得ぬため走査対象外
# （.md散文中の砲口名・package-lock.json等の依存名・sinks-default.txt自身の誤検知を防ぐ）。
skip_re='(^|/)(\.githooks|\.forge|node_modules|dist|build|coverage)/|\.(md|rst|txt|json|lock|map|svg|png|jpe?g|gif|ico|woff2?|ttf|csv|snap|ipynb)$'

allow_pats=()
if [ -f "$allow" ]; then
  while IFS= read -r line; do
    case "$line" in ''|\#*) continue ;; esac
    allow_pats+=("$line")
  done < "$allow"
fi

violations=()
while IFS= read -r f; do
  [ -z "$f" ] && continue
  echo "$f" | grep -qE "$skip_re" && continue
  # -I でバイナリを不一致扱い。まず素の一致を見てから、コメント行を除いた実一致を確認。
  grep -IqE "$combined" -- "$repo_root/$f" 2>/dev/null || continue
  real=$(grep -IE "$combined" -- "$repo_root/$f" 2>/dev/null | grep -vE "$comment_re" || true)
  [ -z "$real" ] && continue   # コメント内のみ＝実弾ではない

  allowed=0
  for ap in "${allow_pats[@]:-}"; do
    [ -z "$ap" ] && continue
    if echo "$f" | grep -qE "$ap"; then allowed=1; break; fi
  done
  [ "$allowed" -eq 1 ] && continue
  violations+=("$f")
done < <(git ls-files)

if [ "${#violations[@]}" -gt 0 ]; then
  echo "実弾統制・CI掃討：許可表(.forge/sinks-allow.txt)に無いファイルが外部発信の生砲口を含む："
  printf '%s\n' "${violations[@]}" | sed 's/^/  - /'
  echo ""
  echo "対処のいずれか："
  echo "  (a) 正当な砲口保持（notifier・各service等）→ .forge/sinks-allow.txt へ登録"
  echo "  (b) デモ/テスト経路からの到達 → 砲口封印（ガード付き中継＋合成テナントno-op）"
  echo "  (c) 死物（未使用）→ 削除"
  echo "  (d) 意図的な実弾デモ → LIVE_FIRE.md へ登録の上、許可表にも載せる"
  exit 1
fi

echo "実弾統制・CI掃討：許可表外の生砲口なし（合格）。"
exit 0
