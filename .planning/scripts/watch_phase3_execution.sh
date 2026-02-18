#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${1:-$(pwd)}"
cd "$REPO_ROOT"

PHASE_DIR="$(ls -d .planning/phases/03-* 2>/dev/null | head -n 1 || true)"
if [[ -z "$PHASE_DIR" ]]; then
  echo "Phase 3 directory not found under .planning/phases/."
  exit 1
fi

PLAN_01="${PHASE_DIR}/03-01-PLAN.md"
PLAN_02="${PHASE_DIR}/03-02-PLAN.md"
if [[ ! -f "$PLAN_01" || ! -f "$PLAN_02" ]]; then
  echo "Phase 3 plan files are required: ${PLAN_01}, ${PLAN_02}"
  exit 1
fi

LOG_DIR=".planning/logs"
mkdir -p "$LOG_DIR"

LOG_FILE="${LOG_DIR}/phase3-execution-watch.log"
REPORT_FILE="${PHASE_DIR}/03-EXECUTION-WATCH.md"
STOP_FILE="${LOG_DIR}/phase3-execution-watch.stop"

WATCH_START_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

log() {
  printf '[%s] %s\n' "$(timestamp)" "$*" | tee -a "$LOG_FILE" >/dev/null
}

extract_files_from_plan() {
  local plan_path="$1"
  awk '
    /^files_modified:/ { in_files = 1; next }
    in_files && /^autonomous:/ { in_files = 0; next }
    in_files && /^[[:space:]]*-[[:space:]]+/ {
      line=$0
      sub(/^[[:space:]]*-[[:space:]]+/, "", line)
      gsub(/\r/, "", line)
      print line
    }
  ' "$plan_path"
}

hash_file() {
  local file="$1"
  if [[ -f "$file" ]]; then
    sha1sum "$file" | awk '{print $1}'
  else
    echo "MISSING"
  fi
}

run_cmd_capture() {
  local cmd="$1"
  local timeout_seconds="$2"
  local out
  set +e
  out="$(timeout "${timeout_seconds}" bash -lc "$cmd" 2>&1)"
  local rc=$?
  set -e
  printf '%s\n' "$out"
  return $rc
}

milestone_smoke_01_status="pending"
milestone_smoke_02_status="pending"

run_milestone_smoke_01() {
  if [[ "$milestone_smoke_01_status" != "pending" ]]; then
    return 0
  fi
  milestone_smoke_01_status="running"
  log "03-01 summary detected. Running focused smoke tests."
  local output
  if output="$(run_cmd_capture "cd tools/gsd-review-broker && uv run pytest tests/test_messages.py tests/test_priority.py tests/test_notifications.py -q" 900)"; then
    milestone_smoke_01_status="pass"
    log "03-01 smoke tests passed."
  else
    milestone_smoke_01_status="fail"
    log "03-01 smoke tests failed."
    printf '%s\n' "$output" | sed 's/^/[03-01-smoke] /' >>"$LOG_FILE"
  fi
}

run_milestone_smoke_02() {
  if [[ "$milestone_smoke_02_status" != "pending" ]]; then
    return 0
  fi
  milestone_smoke_02_status="running"
  log "03-02 summary detected. Running focused smoke tests."
  local output
  if output="$(run_cmd_capture "cd tools/gsd-review-broker && uv run pytest tests/test_counter_patch.py -q" 900)"; then
    milestone_smoke_02_status="pass"
    log "03-02 smoke tests passed."
  else
    milestone_smoke_02_status="fail"
    log "03-02 smoke tests failed."
    printf '%s\n' "$output" | sed 's/^/[03-02-smoke] /' >>"$LOG_FILE"
  fi
}

write_report() {
  local last_check_utc="$1"
  local observed_count="$2"
  local out_of_scope_count="$3"
  local token_warning_count="$4"
  local summary_01_present="$5"
  local summary_02_present="$6"
  local quiet_cycles="$7"

  {
    echo "# Phase 3 Execution Watch"
    echo
    echo "- Started: ${WATCH_START_UTC}"
    echo "- Last check: ${last_check_utc}"
    echo "- Phase directory: \`${PHASE_DIR}\`"
    echo
    echo "## Scope"
    echo
    echo "Watched expected files from plans:"
    local f
    for f in "${EXPECTED_FILES[@]}"; do
      echo "- \`${f}\`"
    done
    echo
    echo "Plus broker-source guard rails under:"
    echo "- \`tools/gsd-review-broker/src/gsd_review_broker/*.py\`"
    echo "- \`tools/gsd-review-broker/tests/test_*.py\`"
    echo
    echo "## Status"
    echo
    echo "- Observed file-change events: ${observed_count}"
    echo "- Out-of-scope change warnings: ${out_of_scope_count}"
    echo "- Verdict-token warnings (\`request_changes\`): ${token_warning_count}"
    echo "- 03-01 summary present: ${summary_01_present} (smoke: ${milestone_smoke_01_status})"
    echo "- 03-02 summary present: ${summary_02_present} (smoke: ${milestone_smoke_02_status})"
    echo "- Quiet cycles after both summaries: ${quiet_cycles}"
    echo
    echo "## Recent Events"
    echo
    tail -n 30 "$LOG_FILE" | sed 's/^/- /'
  } >"$REPORT_FILE"
}

declare -a EXPECTED_FILES=()
mapfile -t expected_01 < <(extract_files_from_plan "$PLAN_01")
mapfile -t expected_02 < <(extract_files_from_plan "$PLAN_02")

declare -A expected_set=()
for f in "${expected_01[@]}" "${expected_02[@]}"; do
  expected_set["$f"]=1
done

for f in "${!expected_set[@]}"; do
  EXPECTED_FILES+=("$f")
done
IFS=$'\n' EXPECTED_FILES=($(sort <<<"${EXPECTED_FILES[*]}"))
unset IFS

if (( ${#EXPECTED_FILES[@]} == 0 )); then
  echo "No files_modified entries found in Phase 3 plans."
  exit 1
fi

declare -a WATCH_FILES=()
for f in "${EXPECTED_FILES[@]}"; do
  WATCH_FILES+=("$f")
done

while IFS= read -r f; do
  WATCH_FILES+=("$f")
done < <(find tools/gsd-review-broker/src/gsd_review_broker -maxdepth 1 -type f -name '*.py' | sort)

while IFS= read -r f; do
  WATCH_FILES+=("$f")
done < <(find tools/gsd-review-broker/tests -maxdepth 1 -type f -name 'test_*.py' | sort)

declare -A watch_set=()
for f in "${WATCH_FILES[@]}"; do
  watch_set["$f"]=1
done

WATCH_FILES=()
for f in "${!watch_set[@]}"; do
  WATCH_FILES+=("$f")
done
IFS=$'\n' WATCH_FILES=($(sort <<<"${WATCH_FILES[*]}"))
unset IFS

declare -A LAST_HASH=()
for f in "${WATCH_FILES[@]}"; do
  LAST_HASH["$f"]="$(hash_file "$f")"
done

: >"$LOG_FILE"
log "Phase 3 execution watch started."
log "Monitoring ${#WATCH_FILES[@]} files (${#EXPECTED_FILES[@]} in expected plan scope)."

observed_events=0
out_of_scope_warnings=0
token_warnings=0
quiet_cycles_after_summaries=0

while true; do
  now_utc="$(timestamp)"

  if [[ -f "$STOP_FILE" ]]; then
    log "Stop file detected (${STOP_FILE}). Exiting watcher."
    rm -f "$STOP_FILE"
    write_report "$now_utc" "$observed_events" "$out_of_scope_warnings" "$token_warnings" "$( [[ -f "${PHASE_DIR}/03-01-SUMMARY.md" ]] && echo yes || echo no )" "$( [[ -f "${PHASE_DIR}/03-02-SUMMARY.md" ]] && echo yes || echo no )" "$quiet_cycles_after_summaries"
    exit 0
  fi

  changes_this_cycle=0

  for f in "${WATCH_FILES[@]}"; do
    current_hash="$(hash_file "$f")"
    if [[ "${LAST_HASH[$f]}" != "$current_hash" ]]; then
      LAST_HASH["$f"]="$current_hash"
      changes_this_cycle=$((changes_this_cycle + 1))
      observed_events=$((observed_events + 1))
      log "File changed: ${f}"

      if [[ -z "${expected_set[$f]:-}" ]]; then
        out_of_scope_warnings=$((out_of_scope_warnings + 1))
        log "Warning: out-of-scope change (not listed in files_modified): ${f}"
      fi

      if [[ -f "$f" ]] && [[ "$f" == *.py ]] && rg -q "request_changes" "$f"; then
        token_warnings=$((token_warnings + 1))
        log "Warning: found token 'request_changes' in ${f} (expected 'changes_requested')."
      fi
    fi
  done

  if [[ -f "${PHASE_DIR}/03-01-SUMMARY.md" ]]; then
    run_milestone_smoke_01
  fi

  if [[ -f "${PHASE_DIR}/03-02-SUMMARY.md" ]]; then
    run_milestone_smoke_02
  fi

  summary_01_present="no"
  summary_02_present="no"
  [[ -f "${PHASE_DIR}/03-01-SUMMARY.md" ]] && summary_01_present="yes"
  [[ -f "${PHASE_DIR}/03-02-SUMMARY.md" ]] && summary_02_present="yes"

  if [[ "$summary_01_present" == "yes" && "$summary_02_present" == "yes" && "$changes_this_cycle" -eq 0 ]]; then
    quiet_cycles_after_summaries=$((quiet_cycles_after_summaries + 1))
  else
    quiet_cycles_after_summaries=0
  fi

  write_report "$now_utc" "$observed_events" "$out_of_scope_warnings" "$token_warnings" "$summary_01_present" "$summary_02_present" "$quiet_cycles_after_summaries"

  # Stop after both summaries exist and the watched files have stayed quiet for 3 cycles (~45s).
  if [[ "$summary_01_present" == "yes" && "$summary_02_present" == "yes" && "$quiet_cycles_after_summaries" -ge 3 ]]; then
    log "Both summaries detected and watcher quiet period reached. Exiting watcher."
    exit 0
  fi

  sleep 15
done
