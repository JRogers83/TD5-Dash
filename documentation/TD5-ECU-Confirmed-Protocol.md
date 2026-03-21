# TD5 ECU — Confirmed Protocol & Findings

**Vehicle-verified protocol documentation for the Land Rover TD5 ECU over K-Line.**
Based on live testing sessions on 2026-03-21 using a VAG COM KKL 409.1 USB cable with genuine FTDI FT232RL chip, connected to a Land Rover Defender TD5.

This document records what was confirmed to work on the actual ECU, not what the spec says should work. Where our ECU differs from the technical reference (`TD5-ECU-Protocol-Technical-Reference.md`), the difference is noted.

---

## 1. Physical layer

| Parameter | Confirmed value |
|-----------|----------------|
| Bus | K-Line, OBD-II pin 7 (single-wire, half-duplex) |
| Baud rate | **10400 bps** |
| Data format | 8N1 (8 data bits, no parity, 1 stop bit) |
| USB interface | VAG COM KKL 409.1 cable, genuine FTDI FT232RL |
| Host library | PyFtdi 0.57.1 (requires libusb / libusbK driver on Windows via Zadig) |
| FTDI URL | `ftdi://ftdi:232/1` (default, single cable on bus) |

### K-Line echo

K-Line is half-duplex — every byte transmitted is echoed back on RX. After sending an N-byte frame, read and discard exactly N echo bytes before reading the ECU response.

---

## 2. Fast-init timing

| Parameter | Confirmed value |
|-----------|----------------|
| LOW pulse (TiniL) | **22 ms** (minimum that works; 25 ms also works) |
| HIGH idle (TiniH) | **25 ms** |
| Post-mode-switch settle | **50 ms** |
| Total wakeup | ~97 ms (22 + 25 + 50) |

### Procedure

1. Purge FTDI RX buffer
2. Enter FTDI bitbang mode (TX pin = GPIO bit 0)
3. Drive TX LOW (`0x00`) for 22 ms
4. Drive TX HIGH (`0x01`) for 25 ms
5. Return to UART mode, set baud to 10400
6. Wait 50 ms for UART to stabilise
7. Purge RX buffer (removes bitbang transition artifacts: 0xC0, 0xCC, 0xFC)

### Timing sensitivity

- **15–20 ms LOW**: ECU does not wake (silent)
- **22–30 ms LOW**: ECU wakes reliably
- 22 ms is the confirmed minimum; the ISO spec says 25 ms +/-1 ms

---

## 3. Frame format

All frames use ISO 14230 (KWP2000) framing with a **mandatory checksum byte** (sum of all preceding bytes, modulo 256).

### StartCommunication (physical addressing)

```
[FMT] [TGT] [SRC] [SVC] [CS]
 0x81  0x13  0xF7  0x81  0x0C
```

- FMT bit 7 = 1 (address bytes present), bits 5:0 = 1 (1 data byte)
- TGT = 0x13 (ECU address)
- SRC = 0xF7 (tester address)
- This is the ONLY frame that uses physical addressing

### All subsequent frames (short format)

```
[LEN] [SVC] [payload...] [CS]
```

- LEN = number of data bytes following (SVC + payload)
- CS = `(LEN + SVC + sum(payload)) & 0xFF`

### ECU response format

```
[LEN] [SVC+0x40] [payload...] [CS]
```

Positive response SVC = request SVC + 0x40. Negative response SVC = 0x7F.

### Negative response format

```
[LEN] [0x7F] [rejected_SVC] [error_code] [CS]
```

---

## 4. Session establishment — confirmed byte sequences

Every TX/RX pair below was captured from the live ECU.

### Step 1: StartCommunication

```
TX:  81 13 F7 81 0C
RX:  03 C1 57 8F AA
```

- Response: C1 = positive (0x81 + 0x40), keyword bytes 57 8F
- Checksum: (0x03 + 0xC1 + 0x57 + 0x8F) & 0xFF = 0xAA

### Step 2: StartDiagnosticSession (mode 0xA0)

```
TX:  02 10 A0 B2
RX:  01 50 51
```

- Sub-function 0xA0 = TD5 manufacturer-specific diagnostic mode
- Response: 50 = positive (0x10 + 0x40)

### Step 3: SecurityAccess — request seed

```
TX:  02 27 01 2A
RX:  04 67 01 xx xx cs
```

- Response contains 2 seed bytes (big-endian: high byte first)
- Example: seed = 0xBA08, response = `04 67 01 BA 08 2E`

### Step 4: SecurityAccess — send key

```
TX:  04 27 02 [key_hi] [key_lo] cs
RX:  02 67 02 6B
```

- Key computed from seed using the TD5 LFSR algorithm (see section 5)
- Response 67 02 = authentication accepted

---

## 5. Seed-key algorithm

Variable-iteration LFSR. Implementation in `backend/obd/protocol.py:td5_seed_to_key()`.

**Verified test vectors (confirmed against live ECU seed 0xBA08 and against td5keygen project):**

| Seed | Key | Source |
|------|-----|--------|
| 0x34A5 | 0x54D3 | td5keygen canonical |
| 0x7411 | 0x2741 | Live ECU (ignition-only session) |
| 0xBA08 | 0x70DC | Live ECU (engine running session) |
| 0xF0DD | 0x7D51 | DiscoTD5 validation |

The seed changes with each new session. The key algorithm is deterministic.

---

## 6. Timing requirements

| Parameter | Value | Notes |
|-----------|-------|-------|
| P3 inter-message gap | **55 ms minimum** | Time between end of ECU response and start of next tester request |
| P4 inter-byte (tester TX) | **5 ms** | Delay between each byte sent to ECU |
| P2 response timeout | **2000 ms** | Max wait for ECU to start responding |
| P3max session timeout | **~5 seconds** | ECU drops session if no activity |

### P3 is critical with engine running

Without the P3 gap, the ECU reliably times out on SecurityAccess (and sometimes DiagSession) when the engine is running. The MC68332 processor needs the gap to service engine management tasks between diagnostic requests. With ignition-only, the ECU tolerates zero gap.

---

## 7. Session persistence — critical behaviour

Once the ECU accepts StartCommunication, it enters a session that **persists even through new fast-init pulses**. A new fast-init does NOT reset the session.

Consequences:
- You CANNOT close the serial connection and reopen it to start a new session
- Each rejected StartCommunication attempt resets the P3max timer, creating a deadlock
- To recover from a stuck session, send **StopCommunication** (`01 82 83`) on the existing link
- After StopCommunication, wait ~1 second, then perform fresh fast-init

### StopCommunication

```
TX:  01 82 83
RX:  01 C2 C3    (positive — session ended)
  or: silence     (no session was active)
```

---

## 8. Supported PIDs (ReadDataByLocalIdentifier, SVC 0x21)

Request format: `02 21 [PID] [CS]`
Response format: `[LEN] 61 [PID] [payload...] [CS]`

### Confirmed working PIDs

| PID | Description | Payload bytes | Engine required | Confirmed response |
|-----|-------------|--------------|-----------------|-------------------|
| 0x01 | Fuelling (short) | 2 | No | Returns coolant temp only — NOT the 22-field block |
| 0x09 | RPM | 2 | **Yes** | 16-bit big-endian, raw = RPM |
| 0x0D | Speed | 1 | No | Raw byte = kph |
| 0x10 | Battery voltage | 4 | **Yes** | Two 16-bit values, each / 1000 = volts |
| 0x1A | Temperatures | 16 | No | 8 x 16-bit values (see decoding below) |
| 0x1B | Throttle | 10 | **Yes** | 5 x 16-bit values (see decoding below) |
| 0x1C | MAP/MAF | 8 | No | MAP + MAF values (see decoding below) |
| 0x20 | Current faults | 4 | No | 2 x 16-bit fault codes |

### PIDs that do NOT work

| PID | Description | Result |
|-----|-------------|--------|
| 0x08 | Input switches A | NACK 0x10 (generalReject) |

### ECU variant note

The tech reference documents PID 0x01 as returning all 22 fuelling parameters in one response. **Our ECU returns only 2 bytes for PID 0x01** (coolant temperature). The individual PIDs (0x09, 0x0D, 0x10, 0x1A, 0x1B, 0x1C) are the correct approach for this ECU variant.

PIDs 0x09, 0x10, and 0x1B only respond when the engine is running. With ignition-only power, they time out.

---

## 9. Data decoding

### PID 0x09 — RPM (2 bytes)

```
RPM = (byte[0] << 8) | byte[1]
```

Example: `03 00` = 768 RPM (idle)

### PID 0x0D — Speed (1 byte)

```
Speed_kph = byte[0]
```

### PID 0x10 — Battery voltage (4 bytes)

Two 16-bit readings, each in millivolts:

```
V1 = ((byte[0] << 8) | byte[1]) / 1000.0
V2 = ((byte[2] << 8) | byte[3]) / 1000.0
```

Example: `37 93 37 96` = 14.227V / 14.230V (alternator charging)

### PID 0x1A — Temperatures (16 bytes)

8 x 16-bit values. Primary temperatures at 4-byte stride (positions 0, 4, 8, 12). All encoded as Kelvin x 10:

```
temp_C = (raw_value - 2732) / 10.0
```

| Byte offset | Description | Example raw | Example decoded |
|-------------|-------------|-------------|-----------------|
| [0:2] | Coolant | 0x0B61 = 2913 | 18.1 C |
| [4:6] | Inlet air | 0x0B3F = 2879 | 14.7 C |
| [8:10] | External | 0x0B2C = 2860 | 12.8 C |
| [12:14] | Fuel | 0x0B39 = 2873 | 14.1 C |

The alternating positions [2:4], [6:8], [10:12], [14:16] contain related values (possibly filtered/averaged readings or secondary sensor data). Their exact meaning is unconfirmed.

### PID 0x1B — Throttle (10 bytes)

5 x 16-bit values:

| Byte offset | Description | Encoding |
|-------------|-------------|----------|
| [0:2] | Pedal track 1 (P1) | millivolts (/ 1000 = V) |
| [2:4] | Pedal track 2 (P2) | millivolts |
| [4:6] | Pedal track 3 (P3) | millivolts |
| [6:8] | Pedal track 4 (P4) | millivolts |
| [8:10] | Supply voltage | millivolts |

Throttle percentage: `(P1 / Supply) * 100`

Example at idle: P1 = 910 mV, Supply = 5016 mV → 18.1%

### PID 0x1C — MAP/MAF (8 bytes)

| Byte offset | Description | Encoding |
|-------------|-------------|----------|
| [0:2] | MAP sensor 1 | raw / 10000 = bar absolute |
| [2:4] | MAP sensor 2 | raw / 10000 = bar absolute |
| [4:6] | MAF value 1 | TBC |
| [6:8] | MAF value 2 | TBC |

Example at idle: MAP = 0x278D = 10125 → 1.0125 bar (atmospheric pressure)

Boost pressure (gauge) = MAP - 1.01325 bar

### PID 0x20 — Fault codes (4 bytes)

2 x 16-bit fault codes. Meaning of specific codes requires a TD5 DTC lookup table.

Example: `1D BB` and `0C 84` — two stored faults.

---

## 10. Error codes (negative response 0x7F)

| Code | Meaning | When seen |
|------|---------|-----------|
| 0x10 | generalReject | StartCommunication when session already active; PID 0x08 |
| 0x35 | invalidKey | Wrong seed-key computation |
| 0x36 | exceededNumberOfAttempts | 2 failed key attempts → 10s lockout |

---

## 11. Known issues and workarounds

### OBD connector contact quality

Engine vibration can cause intermittent signal loss on a marginal OBD connector. Symptom: StartCommunication succeeds but subsequent frames get no response. Fix: re-seat the OBD connector firmly.

### Leftover session after tool crash / Ctrl+C

If the tool exits without ending the session, the ECU stays in diagnostic mode for ~5 seconds. If the tool is restarted within that window and tries fast-init + StartCommunication, the ECU rejects it with `7F 81 10` and each rejection resets the timeout (deadlock). Fix: send StopCommunication (`01 82 83`) before retrying, or cycle ignition OFF for 10+ seconds.

### USB cable hot-plug sequence

The KKL cable's K-Line level shifter is powered by 12V from the OBD port. For reliable operation:
1. Plug OBD connector into vehicle first
2. Then plug USB into laptop
3. If the cable was left plugged into the OBD port between sessions, unplug and re-plug the OBD end before starting

### PIDs that require engine running

PIDs 0x09 (RPM), 0x10 (Battery), and 0x1B (Throttle) only respond when the engine is running. They time out with ignition-only. This is expected — the ECU only publishes these values when the engine management system is active.

---

## 12. Complete working session trace

Captured from the live ECU with engine running at idle, 2026-03-21:

```
-- Fast-init: 22ms LOW, 25ms HIGH, 50ms settle --

TX:  81 13 F7 81 0C                         StartCommunication
RX:  03 C1 57 8F AA                         Accepted (keyword bytes 57 8F)

TX:  02 10 A0 B2                            StartDiagnosticSession (mode 0xA0)
RX:  01 50 51                               Accepted

TX:  02 27 01 2A                            SecurityAccess — request seed
RX:  04 67 01 BA 08 2E                      Seed = 0xBA08

TX:  04 27 02 70 DC 79                      SecurityAccess — send key 0x70DC
RX:  02 67 02 6B                            Authenticated

TX:  02 21 09 2C                            ReadDataByLocalIdentifier — RPM
RX:  04 61 09 03 00 71                      RPM = 768

TX:  02 21 0D 30                            ReadDataByLocalIdentifier — Speed
RX:  03 61 0D 00 71                         Speed = 0 kph

TX:  02 21 10 33                            ReadDataByLocalIdentifier — Battery
RX:  06 61 10 37 93 37 96 0E               Battery = 14.23V

TX:  02 21 1A 3D                            ReadDataByLocalIdentifier — Temps
RX:  12 61 1A 0B 61 0B AF 0B 3F 0C 50      Coolant=18.1C Inlet=14.7C
         0B 2C 0C C0 0B 39 0C 72 1E        External=12.8C Fuel=14.1C

TX:  02 21 1C 3F                            ReadDataByLocalIdentifier — MAP/MAF
RX:  0A 61 1C 27 8D 27 CB 02 43 07 BE 37   MAP=1.013bar

TX:  01 82 83                               StopCommunication (when done)
RX:  01 C2 C3                               Session ended
```

---

## 13. Software reference

| File | Purpose |
|------|---------|
| `backend/obd/protocol.py` | Frame building (with checksums), seed-key LFSR, PID constants, timing constants |
| `backend/obd/connection.py` | PyFtdi K-Line driver, fast-init, echo consumption, frame I/O with P3 timing |
| `backend/obd/decoder.py` | Per-PID response parsers |
| `backend/obd/service.py` | Session management, poll loop, WebSocket broadcast |
| `tools/td5_diag.py` | Standalone diagnostic tool — progressive 7-stage verification |
| `documentation/TD5-ECU-Protocol-Technical-Reference.md` | Full protocol spec from open-source research (some details differ from our ECU) |
