#!/usr/bin/env python3
"""boot_timing.py <log> [--json] — IR-190: the boot read from the log's OWN stamps.

ONE line: `boot: launched=<HH:MM:SS.mmm> Loaded=+<s> projection_live=+<s>
network_resumed=+<s> network_up=+<s> startup_failed=+<s>|none` — each slot the FIRST
matching line's delta from the log's first stamped line (the Pi's clock as written,
never converted; a day rollover adds 86,400 s); a slot with no line prints `none`.
`--json` prints the same as one JSON object. Stdlib only; exit 0 (2: no log read)."""
import json
import re
import sys

SLOTS = {"Loaded": r"Loaded .* from checkpoint", "projection_live": r"registry\.projection_live",
         "network_resumed": r"zigbee\.network_resumed", "network_up": r"zigbee\.network_up\b",
         "startup_failed": r"\bstartup_failed\b"}
STAMP = re.compile(r"(\d\d):(\d\d):(\d\d\.\d\d\d) ")


def _utf8_stdout():                          # IR-183: UTF-8 and LF from the tool's own entry
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", newline="\n")


def boot_read(lines):
    got, base, prev = dict.fromkeys(["launched"] + list(SLOTS)), None, None
    for m in filter(None, map(STAMP.match, lines)):        # unstamped lines are skipped
        t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        while prev is not None and t < prev - 43200:      # midnight passed, not ms of jitter
            t += 86400
        if base is None:
            base, got["launched"] = t, m.group(0).strip()
        prev = t
        for name, pattern in SLOTS.items():
            if got[name] is None and re.search(pattern, m.string):
                got[name] = round(t - base, 3)
    return got


def main(argv):
    logs = [a for a in argv if a != "--json"]
    try:
        with open(logs[0] if len(logs) == 1 else "", encoding="utf-8", errors="replace") as fh:
            got = boot_read(fh)
    except OSError as exc:
        print("usage: boot_timing.py <log> [--json] — %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps(got) if "--json" in argv else "boot: " + " ".join(
        "%s=%s" % (k, "none" if v is None else v if k == "launched" else "+%.3f" % v)
        for k, v in got.items()))
    return 0


if __name__ == "__main__":
    _utf8_stdout()
    sys.exit(main(sys.argv[1:]))
