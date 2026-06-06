#!/bin/sh
# Witty Pi 5 pre-shutdown hook.
#
# The UUGear Witty Pi daemon calls this script before asking the Pi to halt.
# It gives the TD5 Dash backend up to 10 seconds to perform cleanup
# (unfreeze Chromium, flush DB, log shutdown).
#
# Exit codes:
#   0  — proceed with shutdown
#   1  — abort shutdown (e.g. override_mode is active in the backend)
#
# Installation: copy this file to ~/wittypi/beforeShutdown.sh after running
# the UUGear install script. See documentation/pi-setup.md for details.
#
# hw-verify: confirm hook is called by running `sudo shutdown -h now` with
# Witty Pi installed and observing the journal for cleanup log messages.

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
