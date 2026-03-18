"""
TD5 K-Line protocol constants and frame utilities.

Protocol: KWP2000 (ISO 14230) over ISO 9141-2 K-Line at 10,400 baud.
ECU:      Lucas/MEMS TD5 — uses proprietary extensions to KWP2000.

Primary references:
  github.com/hairyone/pyTD5Tester
  github.com/EA2EGA/Ekaitza_Itzali
  github.com/pajacobson/td5keygen
"""

# ── Node addresses ─────────────────────────────────────────────────────────────
ECU_ADDR    = 0x10   # Lucas/MEMS ECU node address
TESTER_ADDR = 0xF1   # External diagnostic tester (us)

# ── Timing constants (all in milliseconds unless noted) ────────────────────────
BAUD_RATE          = 10400
FAST_INIT_LOW_MS   = 25    # K-Line held low  — ISO 9141-2 fast-init pulse
FAST_INIT_HIGH_MS  = 25    # K-Line idle high — before first byte
SETTLE_MS          = 50    # Post-mode-switch settle time
P2_RESPONSE_MS     = 50    # Max wait for first ECU response byte (per KWP2000)
P3_INTER_MSG_MS    = 55    # Min gap between end of response and next request
P4_INTER_BYTE_MS   = 5     # Gap between bytes sent from tester

# ── KWP2000 service IDs ────────────────────────────────────────────────────────
SVC_START_DIAG      = 0x10   # StartDiagnosticSession
SVC_STOP_DIAG       = 0x20   # StopDiagnosticSession
SVC_ECU_RESET       = 0x11   # ECUReset
SVC_SECURITY_ACCESS = 0x27   # SecurityAccess (seed-key handshake)
SVC_READ_LOCAL_ID   = 0x21   # ReadDataByLocalIdentifier (live data)

# Positive response = service_id | 0x40
POSITIVE_RESPONSE_OFFSET = 0x40

# ── SecurityAccess subfunctions ────────────────────────────────────────────────
SA_REQUEST_SEED = 0x01
SA_SEND_KEY     = 0x02

# ── ReadDataByLocalIdentifier sub-identifiers ──────────────────────────────────
# The TD5 ECU does NOT return all data in a single frame.  Each parameter group
# requires a separate ReadDataByLocalIdentifier (0x21) request with its own
# sub-identifier.  Confirmed from Ekaitza_Itzali, pyTD5Tester, LRDuinoTD5.
#
# Response payload layout (bytes after stripping the 6-byte KWP2000 header and
# trailing checksum, i.e. what session.read_local_id() returns):
#
#   PID_RPM          0x09 — 2 bytes  [RPM_H, RPM_L]            16-bit, raw = RPM
#   PID_TEMPS        0x1A — 14+ bytes per temp: 4-byte stride
#                           [0:2]=coolant, [4:6]=air, [8:10]=external, [12:14]=fuel
#                           formula: int16/10.0 - 273.2 = °C  (Kelvin×10 encoding)
#   PID_MAP_MAF      0x1C — 4+ bytes: [0:2]=MAP1, [2:4]=MAP2
#                           formula: int16/10000.0 = bar absolute
#   PID_BATTERY      0x10 — 2+ bytes: [0:2]=voltage
#                           formula: int16/1000.0 = volts
#   PID_SPEED        0x0D — 1 byte:  [0]=speed
#                           formula: raw = kph (integer, no scaling)
#   PID_THROTTLE     0x1B — 10 bytes: [0:2]=P1, [2:4]=P2, [4:6]=P3, [6:8]=P4
#                                     [8:10]=supply voltage
#                           P1,P2,supply: int16/1000.0 = volts
#                           P3,P4:        int16/100.0  = volts
#                           pct = (P1 / supply) * 100  (requires live calibration)

PID_RPM      = 0x09
PID_TEMPS    = 0x1A
PID_MAP_MAF  = 0x1C
PID_BATTERY  = 0x10
PID_SPEED    = 0x0D
PID_THROTTLE = 0x1B


# ── Frame helpers ──────────────────────────────────────────────────────────────

def checksum(data: bytes) -> int:
    """8-bit additive checksum: sum of all bytes modulo 256."""
    return sum(data) & 0xFF


def build_frame(service: int, *payload: int) -> bytes:
    """
    Build a KWP2000 physical-addressing frame for the TD5.

    Structure:
        [0x80]  header byte — KWP2000 physical addressing, length in next byte
        [len]   number of bytes that follow (excl. header and checksum)
        [ECU]   target address
        [TST]   source address (tester)
        [svc]   service ID
        [...]   optional payload bytes
        [csum]  additive checksum of all preceding bytes

    Note: some TD5 implementations use a different header byte (0xC1 for
    functional addressing). If the ECU does not respond, try 0xC1 here.
    """
    body  = bytes([ECU_ADDR, TESTER_ADDR, service] + list(payload))
    frame = bytes([0x80, len(body)]) + body
    return frame + bytes([checksum(frame)])


# ── Seed-key algorithm ─────────────────────────────────────────────────────────

def td5_seed_to_key(seed: int) -> int:
    """
    Derive the KWP2000 security access key from the ECU-supplied seed.

    The TD5 ECU uses an LFSR (linear feedback shift register) with a
    proprietary polynomial. The 16-bit seed is received from the ECU during
    the SecurityAccess handshake; this function returns the matching key.

    Reference: github.com/pajacobson/td5keygen

    !! IMPORTANT — verify the polynomial constant 0x8F1D against the
       td5keygen source before trusting this implementation. An incorrect
       key will cause the ECU to reject authentication and may lock the
       diagnostic session. !!
    """
    for _ in range(16):
        lsb   = seed & 0x0001
        seed >>= 1
        if lsb:
            seed ^= 0x8F1D   # TODO: confirm polynomial against td5keygen
    return seed & 0xFFFF
