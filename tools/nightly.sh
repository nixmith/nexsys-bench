#!/usr/bin/env bash
# nightly.sh — the B3 nightly entrypoint (DP-2): quiesce → verify-quiesced →
# suite auto → restore → verify-restored → digest append.
#
# THE INVARIANT IS RESTORE, NOT A STEP: it runs under a trap on EXIT/INT/TERM
# — every exit path restores, including a mid-suite crash or a scheduler
# stop. A night that cannot say RESTORED ✓ writes RESTORE-FAILED ⛔ and exits
# nonzero (a bench left quiesced silently starves the organic corpus C1
# rides on). The suite runs as a BACKGROUND job under `wait` because bash
# defers signal traps until the foreground command completes — a TERM during
# a 45-minute foreground suite would postpone restore past the scheduler's
# kill window; `wait` is interruptible, so the trap fires immediately.
#
# All bench operations go through tools/bench.sh (playbook law) — this
# wrapper adds no second path to the app. Config rides scenarios/
# constants.yaml quiesce:/nightly: (⛔PIN slots minted at the Pi stage from
# the P-block pastes); the wrapper REFUSES on any PLACEHOLDER, before any
# side effect, and a refused night writes NO digest line — a missing line
# by morning is itself the red (runner README, the nightly section).
#
# Quiescence evidence (amendment (ii) — positive evidence, never absence):
# after the quiesce restart the automations list is READ and bench-hero
# recorded ABSENT; after the restore restart, PRESENT. The PRESENT read is
# the anti-vacuous pair proving the instrument works — both-reads-empty is
# an instrument failure, never a quiet success. Both reads land in
# ~/hs-bench/nightly-logs/<date>-quiesce-evidence.txt, copied into every
# failure bundle the night produces.
#
# Exit codes: 0 = suite green (digest may still flag UNQUIESCED — read it);
# 1 = suite FAIL (or crashed pre-suite); 2 = config refusal / suite lint;
# 3 = RESTORE-FAILED (the loud one); 124/137 ride through from timeout.
# The LAST line of every completed run is the closing line (B3.1 A-4):
#   [--] nightly <date> closing: exit path <normal|trap-INT|trap-TERM|
#   restore-failed|pre-suite-crash>, exit code <N>
# — a morning reader adjudicates from the digest + this line, never from
# the unit's Result= (exit 1 = suite FAIL is a DOCUMENTED outcome, not a
# wrapper crash; the night-2 journal is the exhibit of why this must be
# legible). A config refusal (exit 2) exits BEFORE the trap arms and
# lawfully has no closing line — its red is the MISSING digest line.
#
# No `set -e` (the wrapper must complete and report — every failure is
# handled); NEVER `set -x` (the api token must not reach a log — L3).
set -u

SELF="$(readlink -f "$0")"
TOOLS_DIR="$(dirname "$SELF")"
BENCH_SH="$TOOLS_DIR/bench.sh"
CONSTANTS="$(dirname "$TOOLS_DIR")/scenarios/constants.yaml"
DIGEST_PY="$TOOLS_DIR/runner/nightly_digest.py"

while [ $# -gt 0 ]; do
  case "$1" in
    --bench-sh)  BENCH_SH="$2"; shift 2 ;;
    --constants) CONSTANTS="$2"; shift 2 ;;
    *) echo "[!!] unknown argument: $1 (usage: nightly.sh [--bench-sh <path>] [--constants <path>])"; exit 2 ;;
  esac
done

# ---- config gate: BEFORE any side effect. PLACEHOLDER ⇒ refuse loudly.
CONFIG="$(python3 -B "$DIGEST_PY" config-env --constants "$CONSTANTS")" || {
  echo "[!!] nightly config REFUSED (unminted ⛔PIN slots or unreadable constants — see above); nothing was touched, no digest line is written"
  exit 2
}
eval "$CONFIG"
# Sets: NB_QUIESCE_BRANCH NB_QUIESCE_CARRIER NB_QUIESCE_HOLD_DIR
#       NB_QUIESCE_HEROLESS NB_QUIESCE_LIVE_BASIS NB_QUIESCE_ROUTE
#       NB_HERO_NAME NB_NIGHTLY_TIMEOUT NB_NIGHTLY_LOGS_DIR
#       NB_NIGHTLY_DIGESTS_DIR NB_API_BASE   (paths pre-expanded)

DATE="$(date +%F)"    # ISO, local Pi time (DP-4)
mkdir -p "$NB_NIGHTLY_LOGS_DIR" "$NB_NIGHTLY_DIGESTS_DIR" "$NB_QUIESCE_HOLD_DIR"
NIGHTLY_LOG="$NB_NIGHTLY_LOGS_DIR/nightly-$DATE.log"
EVIDENCE="$NB_NIGHTLY_LOGS_DIR/$DATE-quiesce-evidence.txt"
SUITE_OUT="$NB_NIGHTLY_LOGS_DIR/$DATE-suite-output.txt"
DIGEST_LOG="$NB_NIGHTLY_DIGESTS_DIR/nightly.log"
LATENCY_LOG="$NB_NIGHTLY_DIGESTS_DIR/on-latency.log"

# Both destinations on purpose: the file for the morning glance, stdout for
# the scheduler's journal (DP-5 journal capture). B3.1 A-3 (night-1 G-1):
# the wrapper's own stream is TIMESTAMPED and LINE-BUFFERED here — bash +
# coreutils only (no ts/moreutils, no gawk-strftime): a read loop stamps
# each line and writes it twice, one write per line to stdout (the journal
# stays LIVE, never a block-buffered lump at exit) and one append per line
# to the log file (the durable record carries wrapper-side event times).
# Order is preserved (single reader, one pipe); the unterminated-tail guard
# keeps a final partial line. The digest/evidence/latency files are
# appended DIRECTLY by their writers and never pass through this stream —
# the digest grammar stays byte-exact (selftest-pinned). L3 holds: the
# stamper transforms lines, it never adds content — the token still rides
# command substitution only and appears in no stream.
exec > >(while IFS= read -r _nb_line || [ -n "$_nb_line" ]; do
           _nb_stamped="[$(date '+%F %T')] $_nb_line"
           printf '%s\n' "$_nb_stamped"
           printf '%s\n' "$_nb_stamped" >> "$NIGHTLY_LOG"
         done) 2>&1
STAMPER_PID=$!
SIGNAL_PATH=""        # trap-INT | trap-TERM when a signal drove the exit (A-4)
echo "[--] nightly $DATE starting: $SELF (branch $NB_QUIESCE_BRANCH; constants $CONSTANTS)"

EVIDENCE_CLASS=""     # quiesced | UNQUIESCED(...) | QUIESCE-UNVERIFIED(...)
SWAPPED=0
SUITE_EXIT=-1
SUITE_PID=""
SUITE_CATTED=0
RESTORE_WORD=""

read_automations() {
  # The ⛔PIN-2 instrument: ONE authed read of the automations list. The
  # token rides command substitution only — never echoed, never logged (L3).
  curl -s -m 15 -w $'\n%{http_code}' \
    -H "Authorization: Bearer $("$BENCH_SH" api_token)" \
    "${NB_API_BASE}${NB_QUIESCE_ROUTE}" 2>&1
}

record_read() {
  # record_read <label> — appends the labeled full read to the evidence
  # file; echoes "CODE<tab>BODY-grep-verdict" is left to callers via globals
  # READ_CODE / READ_BODY.
  local label="$1" raw
  raw="$(read_automations)"
  READ_CODE="${raw##*$'\n'}"
  READ_BODY="${raw%$'\n'*}"
  {
    echo "=== $label — $(date -Is) — GET ${NB_API_BASE}${NB_QUIESCE_ROUTE} — HTTP $READ_CODE ==="
    printf '%s\n' "$READ_BODY"
  } >> "$EVIDENCE"
}

quiesce() {
  # Returns 0 when a swap happened (verify_quiesced adjudicates), 1 when
  # the night runs UN-quiesced (EVIDENCE_CLASS already set).
  case "$NB_QUIESCE_BRANCH" in
    A)
      if [ ! -f "$NB_QUIESCE_CARRIER" ]; then
        echo "[!!] quiesce: carrier absent: $NB_QUIESCE_CARRIER — running UN-quiesced (nothing swapped; was last night's restore incomplete?)"
        EVIDENCE_CLASS="QUIESCE-UNVERIFIED(carrier-absent)"
        return 1
      fi
      if ! mv "$NB_QUIESCE_CARRIER" "$NB_QUIESCE_HOLD_DIR/"; then
        echo "[!!] quiesce: mv to hold failed — running UN-quiesced"
        EVIDENCE_CLASS="QUIESCE-UNVERIFIED(swap-failed)"
        return 1
      fi
      SWAPPED=1
      ;;
    B)
      # DP-3 branch B drift guard: a stale hero-less variant must never
      # overwrite Nick's config edits — honesty over mechanism.
      if ! cmp -s "$NB_QUIESCE_CARRIER" "$NB_QUIESCE_LIVE_BASIS"; then
        echo "[--] CONFIG-DRIFT: live config differs from live-basis — the generated hero-less variant is STALE. Running UN-quiesced; regenerate the variant + basis (install notes / operator block), then the next night quiesces again."
        EVIDENCE_CLASS="UNQUIESCED(CONFIG-DRIFT)"
        return 1
      fi
      # B3.1 A-5 (the vacuous-verify pairing rule): success must speak —
      # a silent pass here was only IMPLIED by the swap line that followed.
      echo "[OK] drift check: live == live-basis"
      if ! cp -p "$NB_QUIESCE_CARRIER" "$NB_QUIESCE_HOLD_DIR/$(basename "$NB_QUIESCE_CARRIER").night-held"; then
        echo "[!!] quiesce: night-held copy failed — running UN-quiesced (carrier untouched)"
        EVIDENCE_CLASS="QUIESCE-UNVERIFIED(swap-failed)"
        return 1
      fi
      if ! cp "$NB_QUIESCE_HEROLESS" "$NB_QUIESCE_CARRIER.tmp.$$" \
         || ! mv "$NB_QUIESCE_CARRIER.tmp.$$" "$NB_QUIESCE_CARRIER"; then
        rm -f "$NB_QUIESCE_CARRIER.tmp.$$"
        echo "[!!] quiesce: hero-less swap-in failed — running UN-quiesced (carrier untouched)"
        EVIDENCE_CLASS="QUIESCE-UNVERIFIED(swap-failed)"
        return 1
      fi
      SWAPPED=1
      ;;
  esac
  echo "[--] quiesce swap done (branch $NB_QUIESCE_BRANCH) — restarting for the hero-less boot"
  "$BENCH_SH" restart || echo "[!!] quiesce restart reported FAILED — the verify read adjudicates"
  return 0
}

verify_quiesced() {
  # Amendment (ii): the quiesced state is ASSERTED with positive evidence.
  record_read "$DATE post-quiesce automations read (expect '$NB_HERO_NAME' ABSENT)"
  if [ "$READ_CODE" = "200" ] && [ -n "$READ_BODY" ] \
     && ! printf '%s' "$READ_BODY" | grep -qF "$NB_HERO_NAME"; then
    echo "[OK] quiesce ASSERTED: HTTP 200, non-empty body, '$NB_HERO_NAME' ABSENT"
    EVIDENCE_CLASS="quiesced"
  elif [ "$READ_CODE" = "200" ] \
     && printf '%s' "$READ_BODY" | grep -qF "$NB_HERO_NAME"; then
    echo "[!!] quiesce assert FAILED: '$NB_HERO_NAME' still PRESENT after the swap + restart (wrong carrier pinned?)"
    EVIDENCE_CLASS="UNQUIESCED(QUIESCE-ASSERT-FAILED)"
  else
    echo "[!!] quiesce assert UNVERIFIED: HTTP $READ_CODE / empty body — an instrument failure, never a quiet success (amendment (ii))"
    EVIDENCE_CLASS="QUIESCE-UNVERIFIED(read-failed)"
  fi
}

finish() {
  trap - EXIT INT TERM
  set +u
  if [ -n "$SUITE_PID" ] && kill -0 "$SUITE_PID" 2>/dev/null; then
    echo "[--] stopping the in-flight suite (pid $SUITE_PID) before restore"
    kill "$SUITE_PID" 2>/dev/null
    sleep 2
  fi
  if [ "$SUITE_CATTED" = "0" ] && [ -s "$SUITE_OUT" ]; then
    echo "---- suite output (partial or complete) ----"
    cat "$SUITE_OUT"
    echo "---- end suite output ----"
  fi

  # ---- RESTORE (the invariant) + verify-restored (the PRESENT half of
  # the anti-vacuous pair).
  if [ "$SWAPPED" = "1" ]; then
    local base mv_ok=1
    base="$(basename "$NB_QUIESCE_CARRIER")"
    case "$NB_QUIESCE_BRANCH" in
      A) mv "$NB_QUIESCE_HOLD_DIR/$base" "$NB_QUIESCE_CARRIER" || mv_ok=0 ;;
      B) mv "$NB_QUIESCE_HOLD_DIR/$base.night-held" "$NB_QUIESCE_CARRIER" || mv_ok=0 ;;
    esac
    if [ "$mv_ok" = "1" ]; then
      echo "[--] restore swap done — restarting for the restored boot"
      "$BENCH_SH" restart || echo "[!!] restore restart reported FAILED — the verify read adjudicates"
    else
      echo "[!!] RESTORE MV FAILED — the held config did not return to $NB_QUIESCE_CARRIER"
    fi
    record_read "$DATE post-restore automations read (expect '$NB_HERO_NAME' PRESENT)"
    if [ "$mv_ok" = "1" ] && [ "$READ_CODE" = "200" ] \
       && printf '%s' "$READ_BODY" | grep -qF "$NB_HERO_NAME"; then
      echo "[OK] restore ASSERTED: HTTP 200, '$NB_HERO_NAME' PRESENT"
      RESTORE_WORD="RESTORED"
    else
      echo "[!!] RESTORE-FAILED: mv_ok=$mv_ok HTTP $READ_CODE — say it loudly, exit nonzero (never silently exit quiesced)"
      RESTORE_WORD="RESTORE-FAILED"
    fi
  else
    # Nothing was swapped (drift / swap-failed / early exit): bench-hero
    # never left — still take the PRESENT read so the claim has evidence.
    record_read "$DATE end-of-night automations read (never swapped — expect '$NB_HERO_NAME' PRESENT)"
    if [ "$READ_CODE" = "200" ] \
       && printf '%s' "$READ_BODY" | grep -qF "$NB_HERO_NAME"; then
      RESTORE_WORD="NEVER-SWAPPED-PRESENT"
    else
      RESTORE_WORD="NEVER-SWAPPED-UNVERIFIED"
    fi
  fi

  # ---- quiesce evidence rides every failure bundle the night produced.
  if [ -s "$EVIDENCE" ]; then
    python3 -B "$DIGEST_PY" failed-bundles --suite-output "$SUITE_OUT" 2>/dev/null \
      | while IFS= read -r bundle; do
          bundle="${bundle%$'\r'}"   # CRLF-proof the consumed path (R6 class)
          if [ -d "$bundle" ]; then
            cp "$EVIDENCE" "$bundle/quiesce-evidence.txt" \
              && echo "[--] quiesce evidence copied into failure bundle: $bundle"
          else
            echo "[!!] failure bundle named but absent on disk: $bundle (evidence not copied)"
          fi
        done
  fi

  # ---- the ONE digest line (DP-4) + the latency corpus row (DP-6).
  local latency line
  latency="$(python3 -B "$DIGEST_PY" latency --suite-output "$SUITE_OUT" 2>/dev/null)" \
    || latency="n/a(latency-error)"
  [ -n "$latency" ] || latency="n/a(latency-error)"
  line="$(python3 -B "$DIGEST_PY" compose --date "$DATE" \
            --evidence-class "${EVIDENCE_CLASS:-QUIESCE-UNVERIFIED(early-exit)}" \
            --suite-output "$SUITE_OUT" --restore "$RESTORE_WORD" \
            --latency "$latency" 2>/dev/null)" \
    || line="$DATE ${EVIDENCE_CLASS:-QUIESCE-UNVERIFIED(early-exit)} AUTO floor: (composer failed — treat as red) · bench-hero $RESTORE_WORD · ON-latency $latency"
  printf '%s\n' "$line" >> "$DIGEST_LOG" \
    || echo "[!!] DIGEST APPEND FAILED to $DIGEST_LOG — the morning read will treat the missing line as red"
  printf '%s %s\n' "$DATE" "$latency" >> "$LATENCY_LOG" 2>/dev/null
  echo "[--] digest: $line"

  # ---- exit code: RESTORE-FAILED is the loud one. (Contract unchanged by
  # B3.1 — the closing line below is observability, never control flow.)
  local exit_code exit_path
  if [ "$RESTORE_WORD" = "RESTORE-FAILED" ]; then
    exit_code=3
  elif [ "$SUITE_EXIT" -ge 0 ]; then
    exit_code="$SUITE_EXIT"
  else
    exit_code=1   # crashed before the suite reported — red
  fi

  # ---- the A-4 closing line: the LAST act of finish(). Names the exit
  # path + code so a morning reader (and the journal) can adjudicate the
  # night without decoding systemd's Result= (the night-2 exhibit: exit 1
  # = suite FAIL is the DOCUMENTED contract, not a crash).
  if [ "$RESTORE_WORD" = "RESTORE-FAILED" ]; then
    exit_path="restore-failed"
  elif [ -n "$SIGNAL_PATH" ]; then
    exit_path="$SIGNAL_PATH"
  elif [ "$SUITE_EXIT" -lt 0 ]; then
    exit_path="pre-suite-crash"
  else
    exit_path="normal"
  fi
  echo "[--] nightly $DATE closing: exit path $exit_path, exit code $exit_code"

  # ---- drain the A-3 stamper before exiting: close the stream and reap
  # the reader so the tail lines land in BOTH sinks on every exit path
  # (the G-1 lost-lump class; wait-on-procsub is bash >= 4.4, Pi has 5.2).
  exec 1>&- 2>&-
  [ -n "${STAMPER_PID:-}" ] && wait "$STAMPER_PID" 2>/dev/null
  exit "$exit_code"
}

# A-4: INT/TERM record their path BEFORE the shared finish runs; a plain
# EXIT (fell off the end, or a bash abort) classifies inside finish().
trap 'SIGNAL_PATH="trap-INT"; finish' INT
trap 'SIGNAL_PATH="trap-TERM"; finish' TERM
trap finish EXIT

if quiesce; then
  verify_quiesced
fi
echo "[--] evidence class: $EVIDENCE_CLASS (recorded: $EVIDENCE)"

echo "[--] running: $BENCH_SH suite auto (ceiling $NB_NIGHTLY_TIMEOUT; the engine restarts the app itself for fresh-boot legs — LAWFUL under quiescence, the hero-less config persists on disk)"
timeout --kill-after=60 "$NB_NIGHTLY_TIMEOUT" "$BENCH_SH" suite auto \
  > "$SUITE_OUT" 2>&1 &
SUITE_PID=$!
wait "$SUITE_PID"
SUITE_EXIT=$?
SUITE_PID=""
if [ "$SUITE_EXIT" -eq 124 ] || [ "$SUITE_EXIT" -eq 137 ]; then
  echo "[!!] suite hit the $NB_NIGHTLY_TIMEOUT ceiling (exit $SUITE_EXIT) — partial output below; restore proceeds"
fi
echo "---- suite output ----"
cat "$SUITE_OUT"
echo "---- end suite output ----"
SUITE_CATTED=1
# Fall through to the EXIT trap: restore → verify-restored → digest.
