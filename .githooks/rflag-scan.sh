#!/bin/bash
# forge-gates: R旗（リスクフラグ）走査 — ステージ済み差分をパターン照合する
# 呼び出し: rflag-scan.sh
# 出力: マッチした場合、理由を標準出力へ列挙し exit 1。マッチなしなら exit 0。
# このファイルは各リポジトリの .githooks/ へ install.sh によって複製される
# （中央キットへの参照ではなく自己完結——単一マシン外へ持ち出されても動く）。

set -euo pipefail

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
hook_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

default_rules="$hook_dir/rflag-rules-default.txt"
custom_rules="$repo_root/.forge/rflag-rules.txt"

rule_files=()
[ -f "$default_rules" ] && rule_files+=("$default_rules")
[ -f "$custom_rules" ] && rule_files+=("$custom_rules")

staged_files=$(git diff --cached --name-only || true)
[ -z "$staged_files" ] && exit 0

staged_added_lines=$(git diff --cached -- . 2>/dev/null | grep -E '^\+' | grep -vE '^\+\+\+' || true)

matched=()

for rf in "${rule_files[@]}"; do
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    case "$line" in
      \#*) continue ;;
      PATH:*)
        pattern="${line#PATH:}"
        hit=$(echo "$staged_files" | grep -iE "$pattern" || true)
        [ -n "$hit" ] && matched+=("PATH[$pattern] -> $(echo "$hit" | tr '\n' ' ')")
        ;;
      CONTENT:*)
        pattern="${line#CONTENT:}"
        hit=$(echo "$staged_added_lines" | grep -iE "$pattern" || true)
        [ -n "$hit" ] && matched+=("CONTENT[$pattern]")
        ;;
    esac
  done < "$rf"
done

if [ "${#matched[@]}" -gt 0 ]; then
  echo "R旗検知："
  printf '  - %s\n' "${matched[@]}"
  exit 1
fi

exit 0
