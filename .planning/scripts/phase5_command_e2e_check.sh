#!/usr/bin/env bash
#
# phase5_command_e2e_check.sh -- Command-level evidence checker for Phase 5 validation
#
# Verifies that the GSD broker database contains evidence of command-level
# mediation by gsd-planner, gsd-executor, and gsd-verifier agents.
#
# Usage:
#   bash phase5_command_e2e_check.sh [--db <path>]
#
# Options:
#   --db <path>   Path to broker SQLite database
#                 Default: .planning/codex_review_broker.sqlite3
#
# Exit codes:
#   0  All checks passed
#   1  One or more checks failed
#   2  Database not found or sqlite3 not available

set -euo pipefail

# ---- Configuration ----

DB_PATH=".planning/codex_review_broker.sqlite3"
PASS_COUNT=0
FAIL_COUNT=0
TOTAL_CHECKS=0

# ---- Parse arguments ----

while [[ $# -gt 0 ]]; do
    case "$1" in
        --db)
            DB_PATH="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [--db <path>]"
            echo ""
            echo "Check broker DB for command-level GSD workflow evidence."
            echo ""
            echo "Options:"
            echo "  --db <path>  Path to broker SQLite database"
            echo "               Default: .planning/codex_review_broker.sqlite3"
            exit 0
            ;;
        *)
            echo "ERROR: Unknown option: $1"
            exit 2
            ;;
    esac
done

# ---- Preflight checks ----

if ! command -v sqlite3 &>/dev/null; then
    echo "ERROR: sqlite3 command not found. Install SQLite CLI tools."
    exit 2
fi

if [[ ! -f "$DB_PATH" ]]; then
    echo "ERROR: Database not found: $DB_PATH"
    echo ""
    echo "Expected the broker database at the given path."
    echo "Run the command-level tandem workflow first to populate it."
    exit 2
fi

echo "================================================================"
echo "  Phase 5: Command-Level Evidence Checker"
echo "================================================================"
echo ""
echo "Database: $DB_PATH"
echo "Timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo ""

# ---- Helper functions ----

check_pass() {
    local label="$1"
    PASS_COUNT=$((PASS_COUNT + 1))
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    echo "  [PASS] $label"
}

check_fail() {
    local label="$1"
    local detail="${2:-}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    echo "  [FAIL] $label"
    if [[ -n "$detail" ]]; then
        echo "         $detail"
    fi
}

run_count_check() {
    local label="$1"
    local query="$2"
    local min_count="${3:-1}"

    local count
    count=$(sqlite3 "$DB_PATH" "$query" 2>/dev/null || echo "0")

    if [[ "$count" -ge "$min_count" ]]; then
        check_pass "$label (count: $count)"
    else
        check_fail "$label (count: $count, expected >= $min_count)"
    fi
}

# ---- Check 1: Schema verification ----

echo "--- Schema Checks ---"
echo ""

REVIEWS_EXISTS=$(sqlite3 "$DB_PATH" "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='reviews';" 2>/dev/null || echo "0")
if [[ "$REVIEWS_EXISTS" == "1" ]]; then
    check_pass "reviews table exists"
else
    check_fail "reviews table exists" "Table 'reviews' not found in database"
fi

AUDIT_EXISTS=$(sqlite3 "$DB_PATH" "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='audit_events';" 2>/dev/null || echo "0")
if [[ "$AUDIT_EXISTS" == "1" ]]; then
    check_pass "audit_events table exists"
else
    check_fail "audit_events table exists" "Table 'audit_events' not found in database"
fi

echo ""

# ---- Check 2: Planner evidence ----

echo "--- Planner Evidence (gsd-planner + plan_review) ---"
echo ""

run_count_check \
    "Reviews with agent_type='gsd-planner' AND category='plan_review'" \
    "SELECT COUNT(*) FROM reviews WHERE agent_type='gsd-planner' AND category='plan_review';"

run_count_check \
    "Audit events for planner reviews" \
    "SELECT COUNT(*) FROM audit_events WHERE review_id IN (SELECT id FROM reviews WHERE agent_type='gsd-planner' AND category='plan_review');"

echo ""

# ---- Check 3: Executor evidence ----

echo "--- Executor Evidence (gsd-executor + code_change) ---"
echo ""

run_count_check \
    "Reviews with agent_type='gsd-executor' AND category='code_change'" \
    "SELECT COUNT(*) FROM reviews WHERE agent_type='gsd-executor' AND category='code_change';"

run_count_check \
    "Audit events for executor reviews" \
    "SELECT COUNT(*) FROM audit_events WHERE review_id IN (SELECT id FROM reviews WHERE agent_type='gsd-executor' AND category='code_change');"

echo ""

# ---- Check 4: Verifier evidence ----

echo "--- Verifier Evidence (gsd-verifier + verification) ---"
echo ""

run_count_check \
    "Reviews with agent_type='gsd-verifier' AND category='verification'" \
    "SELECT COUNT(*) FROM reviews WHERE agent_type='gsd-verifier' AND category='verification';"

run_count_check \
    "Audit events for verifier reviews" \
    "SELECT COUNT(*) FROM audit_events WHERE review_id IN (SELECT id FROM reviews WHERE agent_type='gsd-verifier' AND category='verification');"

echo ""

# ---- Check 5: Overall health ----

echo "--- Overall Health ---"
echo ""

TOTAL_REVIEWS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM reviews;" 2>/dev/null || echo "0")
TOTAL_EVENTS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM audit_events;" 2>/dev/null || echo "0")
DISTINCT_AGENTS=$(sqlite3 "$DB_PATH" "SELECT COUNT(DISTINCT agent_type) FROM reviews;" 2>/dev/null || echo "0")
DISTINCT_CATEGORIES=$(sqlite3 "$DB_PATH" "SELECT COUNT(DISTINCT category) FROM reviews WHERE category IS NOT NULL;" 2>/dev/null || echo "0")

echo "  Total reviews:       $TOTAL_REVIEWS"
echo "  Total audit events:  $TOTAL_EVENTS"
echo "  Distinct agents:     $DISTINCT_AGENTS"
echo "  Distinct categories: $DISTINCT_CATEGORIES"
echo ""

run_count_check \
    "At least 3 distinct agent types present" \
    "SELECT COUNT(DISTINCT agent_type) FROM reviews;" \
    3

run_count_check \
    "At least 3 distinct categories present" \
    "SELECT COUNT(DISTINCT category) FROM reviews WHERE category IS NOT NULL;" \
    3

echo ""

# ---- Summary ----

echo "================================================================"
echo "  RESULTS"
echo "================================================================"
echo ""
echo "  Passed: $PASS_COUNT / $TOTAL_CHECKS"
echo "  Failed: $FAIL_COUNT / $TOTAL_CHECKS"
echo ""

if [[ "$FAIL_COUNT" -eq 0 ]]; then
    echo "  >>> OVERALL: PASS <<<"
    echo ""
    echo "  Command-level evidence confirms broker mediation for"
    echo "  gsd-planner, gsd-executor, and gsd-verifier workflows."
    exit 0
else
    echo "  >>> OVERALL: FAIL <<<"
    echo ""
    echo "  Missing command-level evidence. Run the tandem workflow"
    echo "  (plan-phase -> execute-phase -> verify-work) with"
    echo "  tandem_enabled=true to populate the broker database."
    exit 1
fi
