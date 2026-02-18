#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${1:-$(pwd)}"
cd "$REPO_ROOT"

PHASE_DIR="$(ls -d .planning/phases/03-* 2>/dev/null | head -n 1 || true)"
if [[ -z "$PHASE_DIR" ]]; then
  echo "Phase 3 directory not found under .planning/phases/."
  exit 1
fi

LOG_DIR=".planning/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/phase3-plan-watch.log"
PID_FILE="$LOG_DIR/phase3-plan-watch.pid"
REVIEW_FILE="$PHASE_DIR/03-PLAN-REVIEW.md"

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

log() {
  printf '[%s] %s\n' "$(timestamp)" "$*" >>"$LOG_FILE"
}

# Avoid duplicate watchers.
if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "Watcher already running with PID $old_pid."
    exit 0
  fi
fi
echo "$$" >"$PID_FILE"

cleanup() {
  rm -f "$PID_FILE"
}
trap cleanup EXIT

extract_expected_plans() {
  awk '
    /^### Phase 3:/ { in_phase=1; next }
    /^### Phase / && in_phase { exit }
    in_phase { print }
  ' .planning/ROADMAP.md 2>/dev/null \
    | grep -oE '03-[0-9]{2}(-PLAN\.md)?' \
    | sed -E 's/$/-PLAN.md/; s/-PLAN\.md-PLAN\.md/-PLAN.md/' \
    | sort -u
}

line_or_default() {
  local pattern="$1"
  local file="$2"
  local default_line="${3:-1}"
  local line
  line="$(grep -n -m1 -E "$pattern" "$file" 2>/dev/null | cut -d: -f1 || true)"
  if [[ -n "$line" ]]; then
    echo "$line"
  else
    echo "$default_line"
  fi
}

file_has_required_sections() {
  local file="$1"
  [[ -f "$file" ]] || return 1

  for key in phase plan type; do
    grep -q "^${key}:" "$file" || return 1
  done

  for section in objective context tasks verification success_criteria output; do
    grep -q "<${section}>" "$file" || return 1
    grep -q "</${section}>" "$file" || return 1
  done

  grep -q "<task type=" "$file" || return 1
}

snapshot_mtimes() {
  local file
  local snapshot=""
  for file in "$@"; do
    [[ -f "$file" ]] || return 1
    # GNU stat first, BSD stat fallback.
    local mtime
    mtime="$(stat -c '%Y' "$file" 2>/dev/null || stat -f '%m' "$file")"
    snapshot+="${file}:${mtime};"
  done
  echo "$snapshot"
}

declare -a PLAN_FILES=()

wait_for_plans_complete() {
  local start_ts now_ts
  start_ts="$(date +%s)"
  local timeout_seconds=43200 # 12h
  local sleep_seconds=30
  local stable_required=3
  local stable_hits=0
  local last_snapshot=""
  local loops=0

  while true; do
    now_ts="$(date +%s)"
    if (( now_ts - start_ts > timeout_seconds )); then
      log "Timeout reached while waiting for Phase 3 plans."
      return 1
    fi

    mapfile -t expected < <(extract_expected_plans)

    if (( ${#expected[@]} == 0 )); then
      if (( loops % 6 == 0 )); then
        log "No Phase 3 plan IDs found in ROADMAP yet; still waiting."
      fi
      stable_hits=0
      last_snapshot=""
      loops=$((loops + 1))
      sleep "$sleep_seconds"
      continue
    fi

    local ready=true
    local files=()
    local plan_name plan_path
    for plan_name in "${expected[@]}"; do
      plan_path="${PHASE_DIR}/${plan_name}"
      files+=("$plan_path")
      if ! file_has_required_sections "$plan_path"; then
        ready=false
        break
      fi
    done

    if [[ "$ready" == "true" ]]; then
      local snapshot
      snapshot="$(snapshot_mtimes "${files[@]}" || true)"
      if [[ -n "$snapshot" ]] && [[ "$snapshot" == "$last_snapshot" ]]; then
        stable_hits=$((stable_hits + 1))
      else
        stable_hits=1
        last_snapshot="$snapshot"
      fi

      if (( stable_hits >= stable_required )); then
        PLAN_FILES=("${files[@]}")
        log "Phase 3 plans detected and stable: ${expected[*]}"
        return 0
      fi
    else
      stable_hits=0
      last_snapshot=""
      if (( loops % 4 == 0 )); then
        log "Plan files found but not complete yet; waiting for final writes."
      fi
    fi

    loops=$((loops + 1))
    sleep "$sleep_seconds"
  done
}

declare -a FINDINGS=()
declare -a GAPS=()

add_finding() {
  local severity="$1"
  local file="$2"
  local line="$3"
  local message="$4"
  FINDINGS+=("${severity}|${file}|${line}|${message}")
}

add_gap() {
  local file="$1"
  local line="$2"
  local message="$3"
  GAPS+=("${file}|${line}|${message}")
}

collect_vague_phrase_findings() {
  local plan="$1"
  local -a patterns=(
    "handle edge cases"
    "production-ready"
    "proper error handling"
    "best practices"
    "clean( |-)?up"
    "as needed"
  )
  local pattern line
  for pattern in "${patterns[@]}"; do
    line="$(grep -nEi "$pattern" "$plan" | head -n 1 | cut -d: -f1 || true)"
    if [[ -n "$line" ]]; then
      add_finding "Medium" "$plan" "$line" "Contains vague wording ('${pattern}') that may require interpretation during execution."
    fi
  done
}

review_plan_file() {
  local plan="$1"
  local section key

  for key in phase plan type; do
    if ! grep -q "^${key}:" "$plan"; then
      add_finding "High" "$plan" "1" "Missing required frontmatter field '${key}'."
    fi
  done

  for section in objective context tasks verification success_criteria output; do
    if ! grep -q "<${section}>" "$plan"; then
      add_finding "High" "$plan" "1" "Missing required section <${section}>."
    fi
    if ! grep -q "</${section}>" "$plan"; then
      add_finding "High" "$plan" "1" "Missing closing tag </${section}>."
    fi
  done

  local task_count
  task_count="$(grep -c "<task type=" "$plan" || true)"
  if (( task_count == 0 )); then
    add_finding "High" "$plan" "$(line_or_default '<tasks>' "$plan" 1)" "No tasks found in <tasks> section."
  elif (( task_count > 3 )); then
    add_finding "Medium" "$plan" "$(line_or_default '<tasks>' "$plan" 1)" "Plan has ${task_count} tasks; recommend 2-3 tasks for execution quality."
  elif (( task_count < 2 )); then
    add_finding "Medium" "$plan" "$(line_or_default '<tasks>' "$plan" 1)" "Plan has only ${task_count} task; verify scope is sufficient for a full plan."
  fi

  mapfile -t task_starts < <(grep -n "<task type=" "$plan" | cut -d: -f1 || true)
  local start end block task_type tag
  for start in "${task_starts[@]}"; do
    end="$(awk -v s="$start" 'NR > s && /<\/task>/{ print NR; exit }' "$plan")"
    if [[ -z "$end" ]]; then
      add_finding "High" "$plan" "$start" "Task opened at line ${start} is missing closing </task>."
      continue
    fi

    block="$(sed -n "${start},${end}p" "$plan")"
    task_type="$(printf '%s\n' "$block" | head -n 1 | sed -E 's/.*type="([^"]+)".*/\1/')"

    case "$task_type" in
    auto)
      for tag in name files action verify done; do
        if ! printf '%s\n' "$block" | grep -q "<${tag}>"; then
          add_finding "High" "$plan" "$start" "Auto task at line ${start} is missing <${tag}>."
        fi
      done

      local files_block
      files_block="$(printf '%s\n' "$block" | sed -n '/<files>/,/<\/files>/p')"
      if [[ -n "$files_block" ]] && printf '%s\n' "$files_block" | grep -Eiq '\?\?\?|TBD|relevant|various|etc'; then
        add_finding "Medium" "$plan" "$start" "Auto task at line ${start} has vague file targets in <files>."
      fi
      ;;

    checkpoint:human-verify)
      for tag in what-built how-to-verify resume-signal; do
        if ! printf '%s\n' "$block" | grep -q "<${tag}>"; then
          add_finding "High" "$plan" "$start" "checkpoint:human-verify task at line ${start} is missing <${tag}>."
        fi
      done
      ;;

    checkpoint:decision)
      for tag in decision context options resume-signal; do
        if ! printf '%s\n' "$block" | grep -q "<${tag}>"; then
          add_finding "High" "$plan" "$start" "checkpoint:decision task at line ${start} is missing <${tag}>."
        fi
      done
      ;;

    checkpoint:human-action)
      for tag in action instructions verification resume-signal; do
        if ! printf '%s\n' "$block" | grep -q "<${tag}>"; then
          add_finding "High" "$plan" "$start" "checkpoint:human-action task at line ${start} is missing <${tag}>."
        fi
      done
      if printf '%s\n' "$block" | grep -Eiq 'vercel|stripe|supabase|upstash|railway|fly|github|cli|api'; then
        add_finding "High" "$plan" "$start" "checkpoint:human-action may be gating work that is likely automatable via CLI/API."
      fi
      ;;

    *)
      add_finding "High" "$plan" "$start" "Unknown task type '${task_type}' at line ${start}."
      ;;
    esac
  done

  collect_vague_phrase_findings "$plan"

  local verification_block
  verification_block="$(sed -n '/<verification>/,/<\/verification>/p' "$plan")"
  if [[ -n "$verification_block" ]] && ! printf '%s\n' "$verification_block" | grep -Eiq '(`[^`]+`|pytest|npm|pnpm|yarn|uv run|python -m|python -c|curl|git |ruff|mypy|tsc|go test|cargo test|xcodebuild)'; then
    add_gap "$plan" "$(line_or_default '<verification>' "$plan" 1)" "Verification section lacks executable command-based checks."
  fi

  if ! grep -Eiq 'pytest|npm test|uv run pytest|go test|cargo test|xcodebuild test|\btests?\b' "$plan"; then
    add_gap "$plan" "1" "No explicit automated test step detected."
  fi

  local plan_base expected_summary
  plan_base="$(basename "$plan" -PLAN.md)"
  expected_summary="${plan_base}-SUMMARY.md"
  if ! grep -q "$expected_summary" "$plan"; then
    add_finding "Medium" "$plan" "$(line_or_default '<output>' "$plan" 1)" "Output section does not reference expected summary file '${expected_summary}'."
  fi
}

review_phase_coverage() {
  local combined
  local roadmap_line
  roadmap_line="$(line_or_default '^### Phase 3:' '.planning/ROADMAP.md' 1)"
  combined="$(cat "${PLAN_FILES[@]}" | tr '[:upper:]' '[:lower:]')"

  if ! grep -Eq 'thread|multi-round|conversation|message' <<<"$combined"; then
    add_finding "High" ".planning/ROADMAP.md" "$roadmap_line" "Phase 3 plan set does not explicitly cover threaded multi-round discussion behavior."
  fi
  if ! grep -Eq 'counter.?patch|alternative diff|patch' <<<"$combined"; then
    add_finding "High" ".planning/ROADMAP.md" "$roadmap_line" "Phase 3 plan set does not explicitly cover counter-patch support."
  fi
  if ! grep -Eq 'priority|critical|normal|low' <<<"$combined"; then
    add_finding "High" ".planning/ROADMAP.md" "$roadmap_line" "Phase 3 plan set does not explicitly cover review priority behavior."
  fi
  if ! grep -Eq 'push|notification|notify' <<<"$combined"; then
    add_finding "High" ".planning/ROADMAP.md" "$roadmap_line" "Phase 3 plan set does not explicitly cover push notification behavior."
  fi
}

format_findings_for_severity() {
  local severity="$1"
  local found=0
  local item sev file line message
  for item in "${FINDINGS[@]}"; do
    IFS='|' read -r sev file line message <<<"$item"
    if [[ "$sev" == "$severity" ]]; then
      found=1
      printf -- "- %s (\`%s:%s\`)\n" "$message" "$file" "$line"
    fi
  done
  if (( found == 0 )); then
    echo "- None."
  fi
}

write_review_report() {
  local verdict="$1"
  local first_plan=""
  if (( ${#PLAN_FILES[@]} > 0 )); then
    first_plan="${PLAN_FILES[0]}"
  fi

  {
    echo "# Phase 3 Plan Review"
    echo
    echo "- Generated: $(timestamp)"
    echo "- Phase directory: \`${PHASE_DIR}\`"
    echo "- Plans reviewed:"
    local plan
    for plan in "${PLAN_FILES[@]}"; do
      echo "  - \`${plan}\`"
    done
    echo
    echo "## Verdict"
    echo
    echo "${verdict}"
    echo
    echo "## Findings"
    echo
    echo "### Critical"
    format_findings_for_severity "Critical"
    echo
    echo "### High"
    format_findings_for_severity "High"
    echo
    echo "### Medium"
    format_findings_for_severity "Medium"
    echo
    echo "### Low"
    format_findings_for_severity "Low"
    echo
    echo "## Open Questions/Assumptions"
    echo
    echo "- Review is static and plan-focused; implementation correctness is out of scope until execution."
    echo
    echo "## Verification/Test Gaps"
    echo
    if (( ${#GAPS[@]} == 0 )); then
      echo "- None identified."
    else
      local gap file line message
      for gap in "${GAPS[@]}"; do
        IFS='|' read -r file line message <<<"$gap"
        printf -- "- %s (\`%s:%s\`)\n" "$message" "$file" "$line"
      done
    fi
    echo
    if [[ "$verdict" == "READY" ]]; then
      echo "## Next Command"
      echo
      if [[ -n "$first_plan" ]]; then
        echo "\`\$gsd execute-plan ${first_plan}\`"
      else
        echo "- No plan path available."
      fi
    else
      echo "## Concrete Edits Required For READY"
      echo
      local idx=1
      local item sev2 file2 line2 message2
      for item in "${FINDINGS[@]}"; do
        IFS='|' read -r sev2 file2 line2 message2 <<<"$item"
        if [[ "$sev2" == "Critical" || "$sev2" == "High" ]]; then
          printf -- "%d. %s (\`%s:%s\`)\n" "$idx" "$message2" "$file2" "$line2"
          idx=$((idx + 1))
        fi
      done
      if (( idx == 1 )); then
        echo "1. Resolve all Medium/Low findings where relevant."
      fi
    fi
  } >"$REVIEW_FILE"
}

main() {
  log "Watcher started for Phase 3 planning completion."
  if ! wait_for_plans_complete; then
    log "Watcher exiting without review due to timeout."
    exit 1
  fi

  local plan
  for plan in "${PLAN_FILES[@]}"; do
    review_plan_file "$plan"
  done
  review_phase_coverage

  local verdict="READY"
  local item sev
  for item in "${FINDINGS[@]}"; do
    IFS='|' read -r sev _ <<<"$item"
    if [[ "$sev" == "Critical" || "$sev" == "High" ]]; then
      verdict="NEEDS_CHANGES"
      break
    fi
  done

  write_review_report "$verdict"
  log "Review completed with verdict ${verdict}. Report: ${REVIEW_FILE}"
}

main "$@"
