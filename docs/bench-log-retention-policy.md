<!--
file: docs/bench-log-retention-policy.md
purpose: The retention policy for Pi bench logs (bench-*.log) + incident journals — copy-off before prune; evidence outranks disk. Chartered at the v27 hygiene sweep (2026-07-10).
audience: Nick (executes); the PM hub (cites).
state-type: policy (standing).
status: RATIFIED 2026-07-10.
-->

# Bench Log Retention Policy

**Principle: logs are evidence.** The bench record QUOTES logs; open investigation rows CITE them. Nothing is pruned until it is either copied off or provably uncited. The event store (`homesynapse-events.db`) is NOT covered by this policy — it is the product's log and is never pruned by hand.

## 1. What accumulates

Each `bench.sh start`/`restart` writes a fresh `~/hs-bench/bench-<stamp>.log` (nohup+tee). ~15 have accumulated through the acceptance arc. journalctl holds kernel-side evidence (USB events, port claims) that ages out on its own vacuum schedule — incident windows must be EXPORTED to survive.

## 2. The policy

1. **Never touch:** the ACTIVE boot's log (currently the soak log `bench-2026-07-10-075035.log`) and the immediately previous boot's log.
2. **Never prune while cited:** any log cited by an OPEN investigation row (currently: the ch22/0xFC4D evidence logs — the six PIE boots + the bellows info capture window — stay until INV-CH22 closes).
3. **Copy-off cadence:** at every arc boundary (soak exit, acceptance close-out, iteration series end), from the desktop:
   ```
   mkdir -p ~/Desktop/Code/ClaudeFolder/_archive/bench-logs/2026-07/
   scp 'pi@<pi-host>:~/hs-bench/bench-*.log' ~/Desktop/Code/ClaudeFolder/_archive/bench-logs/2026-07/
   ```
   Destination is `ClaudeFolder/_archive/` (a repo SIBLING — never committed; the record carries quoted excerpts, git carries the record).
4. **Incident journal export (at incident time, not later):**
   ```
   journalctl --since "<window-start>" --until "<window-end>" > ~/hs-bench/journal-<incident>-<date>.txt
   ```
   and copy it off with the logs (the reboot lesson: the un-exported window is the un-witnessed window).
5. **Prune (only after 3):** on the Pi, delete `bench-*.log` older than the two protected boots and not cited by an open row. One glance before delete: `ls -lt ~/hs-bench/bench-*.log`.
6. **B3 note:** when bench-CI lands, scenario bundles adopt this policy wholesale (bundles copy off nightly with the digest; Pi keeps a 7-day window).

## 3. Current standing instance (2026-07-10)

Execute step 3 for the ~15 accumulated logs at the SOAK EXIT close-out (not before — zero Pi touches mid-soak), then step 5 with the ch22 citations honored. The soak log itself is protected as active until the exit entry is written, then copied off as the certification's primary evidence artifact.
