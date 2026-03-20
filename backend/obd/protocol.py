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
# Confirmed by Ekaitza_Itzali working sequence and DiscoTD5 source.
ECU_ADDR    = 0x13   # Lucas/MEMS ECU node address
TESTER_ADDR = 0xF7   # External diagnostic tester (us)

# ── Timing constants (all in milliseconds unless noted) ────────────────────────
BAUD_RATE          = 10400
FAST_INIT_LOW_MS   = 25    # K-Line held low  — ISO 9141-2 fast-init pulse
FAST_INIT_HIGH_MS  = 25    # K-Line idle high — before first byte
SETTLE_MS          = 50    # Post-mode-switch settle time
P2_RESPONSE_MS     = 50    # Max wait for first ECU response byte (per KWP2000)
P3_INTER_MSG_MS    = 55    # Min gap between end of response and next request
P4_INTER_BYTE_MS   = 5     # Gap between bytes sent from tester

# ── KWP2000 service IDs ────────────────────────────────────────────────────────
SVC_START_COMMUNICATION = 0x81   # StartCommunication — must be first, before StartDiagnosticSession
SVC_START_DIAG          = 0x10   # StartDiagnosticSession
SVC_STOP_DIAG           = 0x20   # StopDiagnosticSession
SVC_ECU_RESET           = 0x11   # ECUReset
SVC_SECURITY_ACCESS     = 0x27   # SecurityAccess (seed-key handshake)
SVC_READ_LOCAL_ID       = 0x21   # ReadDataByLocalIdentifier (live data)

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
    """8-bit additive checksum: sum of all bytes modulo 256.
    Retained for reference / testing; not used in TD5 short-format frames."""
    return sum(data) & 0xFF


def build_frame(service: int, *payload: int) -> bytes:
    """
    Build a KWP2000 short-format request frame for the TD5.

    Confirmed working format (Ekaitza_Itzali / DiscoTD5):

        [LEN]   number of data bytes that follow  (= 1 + len(payload))
        [svc]   service ID
        [...]   optional payload bytes

    No address bytes, no header byte 0x80, no checksum.
    This format is used for all services AFTER StartCommunication has
    established the session — see build_start_comm() for the exception.
    """
    data = bytes([service] + list(payload))
    return bytes([len(data)]) + data


def build_start_comm() -> bytes:
    """
    Build the StartCommunication frame.

    This is the only frame that uses physical addressing (address bytes
    present).  Confirmed bytes from Ekaitza_Itzali: 81 13 F7 81.

        [0x81]  format byte — bit 7 set (address bytes present), length = 1
        [ECU]   target address (0x13)
        [TST]   source address (0xF7)
        [0x81]  StartCommunication service ID
    """
    return bytes([0x81, ECU_ADDR, TESTER_ADDR, SVC_START_COMMUNICATION])


# ── Seed-key algorithm ─────────────────────────────────────────────────────────

def td5_seed_to_key(seed: int) -> int:
    """
    Derive the KWP2000 security access key from the ECU-supplied seed.

    The TD5 uses a variable-iteration LFSR — NOT a fixed-polynomial Galois LFSR.
    The iteration count (1–16) is derived from four specific bits of the seed itself.

    Verified against two independent primary sources:
      github.com/pajacobson/td5keygen  (keygen.c / keytool.py)
      github.com/hairyone/pyTD5Tester  (TD5Tester.py calculate_key())

    Canonical test vector from td5keygen README: 0x34A5 → 0x54D3

    Algorithm:
      1. Extract bits 0, 3 (shifted to bit 1), 5 (to bit 2), 12 (to bit 3)
         of the seed to form a 4-bit iteration count in the range 1–16.
      2. Each LFSR step: compute tap from bits 1, 2, 8, 9; right-shift by 1
         with tap fed into bit 15; then force LSB to 0 if bits 3 AND 13 are
         both set, otherwise force LSB to 1.
    """
    seed &= 0xFFFF

    # Iteration count: extract 4 bits from seed, add 1 → range 1–16
    count = (
        (seed >> 0xC & 0x8) |
        (seed >> 0x5 & 0x4) |
        (seed >> 0x3 & 0x2) |
        (seed       & 0x1)
    ) + 1

    for _ in range(count):
        tap  = ((seed >> 1) ^ (seed >> 2) ^ (seed >> 8) ^ (seed >> 9)) & 1
        tmp  = (seed >> 1) | (tap << 0xF)
        if (seed >> 0x3 & 1) and (seed >> 0xD & 1):
            seed = tmp & 0xFFFE   # force LSB 0
        else:
            seed = tmp | 0x0001   # force LSB 1

    return seed & 0xFFFF
