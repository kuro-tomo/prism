#!/bin/bash
# forge-deploy: 煙試験実行器
# Usage: smoke.sh <base_url> <smoke_file>
#   smoke_file format (1 line per check): PATH EXPECTED_CODE [SUBSTRING...]
#     - PATH starts with /
#     - SUBSTRING may contain spaces; everything after EXPECTED_CODE is treated as one substring
#     - lines starting with # are comments; blank lines skipped
#
# Env:
#   VERCEL_AUTOMATION_BYPASS_SECRET  optional; sent as x-vercel-protection-bypass header
#
# Exit code: 0 if all checks pass, 1 otherwise. Prints a summary table to stdout
# and (if GITHUB_STEP_SUMMARY is set) appends a markdown table there too.

set -u

BASE_URL="${1:?usage: smoke.sh <base_url> <smoke_file>}"
SMOKE_FILE="${2:?usage: smoke.sh <base_url> <smoke_file>}"
BASE_URL="${BASE_URL%/}"

if [ ! -f "$SMOKE_FILE" ]; then
  echo "smoke.sh: no smoke file at $SMOKE_FILE — nothing to check (treated as pass)"
  exit 0
fi

TIMEOUT=15
RETRIES=3
FAIL=0
declare -a ROWS=()

check_one() {
  local path="$1" expected_code="$2" expected_sub="$3"
  local url="${BASE_URL}${path}"
  local attempt body code

  for attempt in 1 2 3; do
    if [ -n "${VERCEL_AUTOMATION_BYPASS_SECRET:-}" ]; then
      body=$(curl -sS -o /tmp/smoke_body.$$ -w "%{http_code}" --max-time "$TIMEOUT" \
        -H "x-vercel-protection-bypass: $VERCEL_AUTOMATION_BYPASS_SECRET" \
        "$url" 2>/tmp/smoke_err.$$) || true
    else
      body=$(curl -sS -o /tmp/smoke_body.$$ -w "%{http_code}" --max-time "$TIMEOUT" "$url" 2>/tmp/smoke_err.$$) || true
    fi
    code="$body"
    if [ -n "$code" ] && [ "$code" != "000" ]; then
      break
    fi
    sleep $((attempt * 3))
  done

  code="${code:-000}"
  local ok=1
  local reason=""

  if [ "$code" != "$expected_code" ]; then
    ok=0
    reason="HTTP $code (期待 $expected_code)"
  elif [ -n "$expected_sub" ]; then
    if ! grep -qF -- "$expected_sub" /tmp/smoke_body.$$ 2>/dev/null; then
      ok=0
      reason="HTTP $code だが本文に「$expected_sub」なし"
    fi
  fi

  if [ "$ok" = "1" ]; then
    ROWS+=("PASS|$path|$code|-")
  else
    ROWS+=("FAIL|$path|$code|$reason")
    FAIL=1
  fi
  rm -f /tmp/smoke_body.$$ /tmp/smoke_err.$$
}

echo "=== forge-deploy smoke test: $BASE_URL ==="

while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    ''|'#'*) continue ;;
  esac
  path=$(echo "$line" | awk '{print $1}')
  code=$(echo "$line" | awk '{print $2}')
  sub=$(echo "$line" | cut -d' ' -f3- )
  if [ "$sub" = "$code" ]; then sub=""; fi
  check_one "$path" "$code" "$sub"
done < "$SMOKE_FILE"

echo ""
printf "%-6s %-40s %-8s %s\n" "RESULT" "PATH" "HTTP" "詳細"
for row in "${ROWS[@]}"; do
  IFS='|' read -r result path code reason <<< "$row"
  printf "%-6s %-40s %-8s %s\n" "$result" "$path" "$code" "$reason"
done

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "### 🔥 煙試験: $BASE_URL"
    echo ""
    echo "| 結果 | パス | HTTP | 詳細 |"
    echo "|---|---|---|---|"
    for row in "${ROWS[@]}"; do
      IFS='|' read -r result path code reason <<< "$row"
      icon="✅"
      [ "$result" = "FAIL" ] && icon="❌"
      echo "| $icon | \`$path\` | $code | $reason |"
    done
  } >> "$GITHUB_STEP_SUMMARY"
fi

if [ "$FAIL" = "1" ]; then
  echo ""
  echo "smoke.sh: FAILED"
  exit 1
fi

echo ""
echo "smoke.sh: all checks passed"
exit 0
