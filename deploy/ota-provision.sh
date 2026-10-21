#!/bin/sh
# TD5 Dash — apply the repo's declared system state (root).
#
#   ota-provision.sh apply
#
# Runs as root via the single sudoers rule that deploy/setup.sh installs; invoked by
# the backend (provision_service.apply) when "Apply Changes" is pressed in the
# Settings → Software → System panel. Reads the same manifests the backend diffs:
#
#   system/packages.txt   install (apt-get install is a no-op for present packages)
#   system/remove.txt     purge if installed
#   system/cleanup.sh     optional idempotent root hook, runs last
#
# Idempotent — safe to re-run. Exits non-zero only on a hard failure. This file
# and deploy/system/* are root-capable: changes to them need the owner's review
# (see .github/CODEOWNERS).
set -eu

if [ "${1:-}" != "apply" ]; then
    echo "usage: $0 apply" >&2
    exit 2
fi
if [ "$(id -u)" -ne 0 ]; then
    echo "ota-provision: must run as root (invoke via sudo)" >&2
    exit 1
fi

DIR="$(cd "$(dirname "$0")" && pwd)/system"
export DEBIAN_FRONTEND=noninteractive

# Print the valid package names from a manifest: strip comments/blanks and drop
# anything that is not a plain Debian package name (so a line can never become an
# apt option such as -oAPT::...).
manifest() {
    [ -f "$1" ] || return 0
    sed 's/#.*//' "$1" | tr -d '\r' | awk 'NF==1 && $1 ~ /^[a-z0-9][a-z0-9+.-]*$/ { print $1 }'
}

is_installed() {
    dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed"
}

# Install ONLY what is missing — exactly what the System panel listed as pending.
# Never hand already-installed packages to apt: that would upgrade them, and on a
# kiosk Pi an unannounced chromium/xorg/plymouth upgrade is a boot-risk (a plymouth
# upgrade rebuilds the initramfs, which this install copies to /boot/firmware by hand).
MISSING=""
for pkg in $(manifest "$DIR/packages.txt"); do
    is_installed "$pkg" || MISSING="$MISSING $pkg"
done

if [ -n "$MISSING" ]; then
    echo "== refresh package lists"
    # Non-fatal: the Pi may be offline or on a metered link; cached lists still work.
    apt-get update -qq || echo "warning: apt-get update failed — continuing with cached lists"
    echo "== install:$MISSING"
    # --no-upgrade is a second guard: never upgrade a package that is already present.
    # shellcheck disable=SC2086
    apt-get install -y --no-install-recommends --no-upgrade $MISSING
else
    echo "== install: nothing missing"
fi

for pkg in $(manifest "$DIR/remove.txt"); do
    if is_installed "$pkg"; then
        echo "== purge: $pkg"
        apt-get purge -y "$pkg"
    fi
done

if [ -x "$DIR/cleanup.sh" ]; then
    echo "== cleanup.sh"
    "$DIR/cleanup.sh"
fi

echo "== done"
