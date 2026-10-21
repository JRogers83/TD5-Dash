"""
OTA system provisioning — reconcile the live OS to the repo's declared desired state.

Kept separate from update_service.py (code update/rollback) on purpose: code
changes are reversible, system changes (installing/purging packages, cleanup
steps) are forward-only, so they are a distinct, user-initiated action. See
docs/superpowers/specs/2026-08-01-ota-system-provisioning-design.md.

Desired state lives in version control under deploy/system/:
    packages.txt   packages that must be present
    remove.txt     packages that must be absent
    cleanup.sh     optional idempotent root hook (highest-risk surface)

get_status() diffs desired vs actual with no root. apply() runs
deploy/ota-provision.sh via sudo (the one sudoers rule setup.sh installs).
Nothing here imports FastAPI so it is unit-testable.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
from pathlib import Path

log = logging.getLogger(__name__)

REPO_DIR = Path(__file__).resolve().parent.parent
SYSTEM_DIR = REPO_DIR / "deploy" / "system"
PACKAGES_FILE = SYSTEM_DIR / "packages.txt"
REMOVE_FILE = SYSTEM_DIR / "remove.txt"
CLEANUP_FILE = SYSTEM_DIR / "cleanup.sh"
PROVISION_SCRIPT = REPO_DIR / "deploy" / "ota-provision.sh"

APPLY_TIMEOUT_S = 1800   # apt on a Pi over Starlink can be slow
_OUTPUT_TAIL = 4000

# Debian package-name syntax. Also keeps a manifest line from smuggling an option
# (e.g. "-oAPT::...") into apt-get/dpkg-query.
_PKG_RE = re.compile(r"^[a-z0-9][a-z0-9+.\-]*$")

_apply_lock = threading.Lock()


def read_manifest(path: Path) -> list[str]:
    """Parse a manifest: one package per line, '#' comments and blank lines ignored.

    A missing file is an empty manifest. Lines that are not valid package names
    are skipped (and logged) rather than passed to apt.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    packages: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if not _PKG_RE.match(line):
            log.warning("Ignoring invalid package name in %s: %r", path, line)
            continue
        if line not in packages:
            packages.append(line)
    return packages


def _dpkg_available() -> bool:
    return shutil.which("dpkg-query") is not None


def _is_installed(pkg: str) -> bool:
    """True if dpkg reports the package as installed. Needs no root."""
    try:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", pkg],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and "install ok installed" in result.stdout


def get_status() -> dict:
    """Diff the desired state against the live system. Never raises, needs no root.

    `available` is False on hosts without dpkg (Windows/dev) — the UI shows the
    panel as unavailable rather than listing every package as pending.
    """
    cleanup_present = CLEANUP_FILE.is_file()
    if not _dpkg_available():
        return {"available": False, "up_to_date": True, "pending_install": [],
                "pending_remove": [], "cleanup_present": cleanup_present}
    pending_install = [p for p in read_manifest(PACKAGES_FILE) if not _is_installed(p)]
    pending_remove = [p for p in read_manifest(REMOVE_FILE) if _is_installed(p)]
    return {
        "available": True,
        "up_to_date": not pending_install and not pending_remove,
        "pending_install": pending_install,
        "pending_remove": pending_remove,
        "cleanup_present": cleanup_present,
    }


def apply() -> dict:
    """Run the root provisioner. Best-effort: never raises.

    Returns {"ok": bool, "output": str, "needs_setup": bool}. `needs_setup` is
    True when sudo refused because the one-time `sudo ./deploy/setup.sh` re-run
    (which installs the sudoers rule) has not happened on this install yet.
    """
    if not _apply_lock.acquire(blocking=False):
        return {"ok": False, "output": "A provisioning run is already in progress.",
                "needs_setup": False}
    try:
        try:
            result = subprocess.run(
                ["sudo", "-n", str(PROVISION_SCRIPT), "apply"],
                capture_output=True, text=True, timeout=APPLY_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "output": f"Timed out after {APPLY_TIMEOUT_S}s.",
                    "needs_setup": False}
        except OSError as exc:
            return {"ok": False, "output": f"Could not run provisioner: {exc}",
                    "needs_setup": False}
        output = ((result.stdout or "") + (result.stderr or "")).strip() or "No output"
        needs_setup = result.returncode != 0 and any(
            m in output for m in ("password is required", "not allowed to execute",
                                  "may not run sudo", "a terminal is required")
        )
        if result.returncode != 0:
            log.warning("OTA provisioning failed (rc=%d): %s", result.returncode, output[-300:])
        return {"ok": result.returncode == 0, "output": output[-_OUTPUT_TAIL:],
                "needs_setup": needs_setup}
    finally:
        _apply_lock.release()
