#!/bin/sh
# Witty Pi 5 pre-shutdown hook.
#
# wp5d calls this script before asking the Pi to halt.
# It gives the TD5 Dash backend up to 10 seconds to perform cleanup
# (unfreeze Chromium, flush DB, log shutdown).
#
# Exit codes:
#   0  — proceed with shutdown
#   1  — abort shutdown (e.g. override_mode is active in the backend)
#
# Installation:
#   This script must be placed on the Witty Pi 5's emulated USB flash drive,
#   NOT in ~/wittypi/ (that was the path for older Witty Pi models).
#   The emulated drive is presented by the Witty Pi 5 hardware as a USB mass
#   storage device. Mount it, copy this file to the scripts directory, and
#   set it executable. Exact mount path and script directory — hw-verify with
#   physical hardware and the wp5 manual.
#
# wp5d daemon log: /var/log/wp5d.log
# hw-verify: confirm this hook is invoked by wp5d and that the script path
#            on the emulated USB flash drive is correct per the wp5 manual.

result=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://localhost:8000/system/shutdown-prepare \
  --max-time 10 2>/dev/null)

if [ "$result" = "409" ]; then
    # Override mode active — abort shutdown
    logger -t td5-dash "Shutdown aborted: override_mode active (Witty Pi hook)"
    exit 1
fi

# 200 or any other response — proceed with halt
exit 0
