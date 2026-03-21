# TD5 ECU Diagnostic Protocol Technical Reference

**Two open-source codebases — EA2EGA/Ekaitza_Itzali (Python) and pajacobson/td5keygen (C/Python) — together document the complete Land Rover TD5 Storm ECU diagnostic protocol over K-Line.** The protocol follows ISO 14230 (KWP2000) on an ISO 9141-2 physical layer, using a non-standard **10400 baud** rate with a Fast Init handshake, seed-key LFSR authentication, and manufacturer-specific diagnostic PIDs. This document extracts every confirmed technical constant, byte sequence, frame format, and timing value from the repositories' first-party documentation and cross-references them against five additional open-source TD5 projects.

---

## 1. Communication setup and physical layer

The TD5 ECU communicates via the **K-Line** (OBD-II pin 7) using a single-wire bidirectional serial bus. The L-Line (pin 15) is not used. The protocol stack is **ISO 14230 (KWP2000)** for the session/application layer on top of an **ISO 9141-2** physical layer. The ECU follows the **1997/98 draft of ISO 14230**, not the official 1999 version.

| Parameter | Value |
|---|---|
| **Baud rate** | **10400 bps** |
| Data bits | 8 |
| Parity | None |
| Stop bits | 1 |
| Format | 8N1 |
| Physical layer | K-Line, OBD-II pin 7 |
| Protocol | ISO 14230 (KWP2000) / ISO 9141-2 |
| ECU target address (TGT) | **`0x13`** |
| Tester source address (SRC) | **`0xF7`** (Ekaitza_Itzali) or `0xF9` (OffTrack) |

### Hardware interface options

The Ekaitza_Itzali project uses a **CP2102 USB-to-TTL converter** that requires custom baud rate reprogramming via Silicon Labs' AN205SW tool: the **360 baud slot replaces 300**, and the **10400 baud slot replaces 14400**. The Td5OpenDiag and DiscoTD5 projects use a **VAG COM KKL USB cable with a genuine FTDI FT232RL chip**, which supports bitbang mode for generating the Fast Init pulse. The td5opencomstm32 project uses an **L9637D K-line transceiver chip** driven by STM32 serial output via bit-banging.

---

## 2. Initialisation sequence — exact bytes

The complete initialisation handshake is documented in the Ekaitza_Itzali README with exact hex values and checksums. Every frame's last byte (shown in parentheses) is a **modulo-256 checksum** of all preceding bytes.

### Fast Init wakeup

```
Step 1:  Hold K-Line LOW  for 25ms (±1ms)    — TiniL
Step 2:  Hold K-Line HIGH for 25ms (±1ms)    — TiniH
Step 3:  Switch UART to 10400 baud, 8N1
```

Total wakeup time Twup = **50ms ±1ms** per spec. The Nanocom uses 26.54ms low + 118.9ms high (145.44ms total), demonstrating the ECU is tolerant of longer timing.

### Complete handshake frame sequence

```
TESTER → ECU:  0x81 0x13 0xF7 0x81 (0x0C)              startCommunications
ECU → TESTER:  0x03 0xC1 0x57 0x8F (0xAA)              startComms positive response

TESTER → ECU:  0x02 0x10 0xA0 (0xB2)                   startDiagnosticSession
ECU → TESTER:  0x01 0x50 (0x51)                         positive response

TESTER → ECU:  0x02 0x27 0x01 (0x2A)                   securityAccess requestSeed
ECU → TESTER:  0x04 0x67 0x01 [Seed1] [Seed2] (cs)     seed response

TESTER → ECU:  0x04 0x27 0x02 [Key1] [Key2] (cs)       securityAccess sendKey
ECU → TESTER:  0x02 0x67 0x02 (0x6B)                   auth OK — session unlocked
```

### Frame-by-frame breakdown

**startCommunications Request** — `0x81 0x13 0xF7 0x81 0x0C`:
- `0x81`: FMT byte — bits [7:6] = `10` (physical addressing with TGT/SRC), bits [5:0] = `000001` (1 data byte)
- `0x13`: TGT — ECU target address
- `0xF7`: SRC — tester source address
- `0x81`: SID — startCommunications Request (ISO 14230 service)
- `0x0C`: checksum — `(0x81 + 0x13 + 0xF7 + 0x81) & 0xFF = 0x20C & 0xFF = 0x0C`

**startCommunications Positive Response** — `0x03 0xC1 0x57 0x8F 0xAA`:
- `0x03`: FMT byte — bits [7:6] = `00` (no address info), bits [5:0] = `000011` (3 data bytes)
- `0xC1`: positive response SID = `0x81 + 0x40`
- `0x57`: **KeyByte1**
- `0x8F`: **KeyByte2**
- `0xAA`: checksum — `(0x03 + 0xC1 + 0x57 + 0x8F) & 0xFF = 0xAA`

The key bytes `0x57 0x8F` indicate that subsequent messages switch to a **simplified 1-byte header format** without TGT/SRC address bytes.

**startDiagnosticSession** — `0x02 0x10 0xA0 0xB2`:
- `0x02`: length (2 data bytes, no address fields)
- `0x10`: SID — diagnosticSessionControl
- `0xA0`: diagnostic mode — **manufacturer-specific** (TD5 diagnostic mode)
- `0xB2`: checksum

**securityAccess requestSeed** — `0x02 0x27 0x01 0x2A`:
- `0x27`: SID — securityAccess
- `0x01`: sub-function — requestSeed

**securityAccess seed response** — `0x04 0x67 0x01 [Seed1] [Seed2] [cs]`:
- `0x67`: positive response SID = `0x27 + 0x40`
- `0x01`: sub-function echo
- Seed1, Seed2: two seed bytes

**securityAccess sendKey** — `0x04 0x27 0x02 [Key1] [Key2] [cs]`:
- `0x02`: sub-function — sendKey
- Key1, Key2: computed key bytes

**Note on byte ordering**: The td5keygen README explicitly states the seed uses **big-endian byte ordering**: `[high_byte][low_byte]`, combined as `seed = seed_1 << 8 | seed_2`. However, the Ekaitza_Itzali README labels the same fields as `[Seed_L] [Seed_H]`, which is an inconsistency between the two projects. The td5keygen ordering (first byte = high byte) is the authoritative reference for the algorithm, since it was written by the person who disassembled the ECU code.

---

## 3. Message frame format

### ISO 14230-2 message structure

The general frame format for the init message (with physical addressing):

```
[FMT] [TGT] [SRC] [DATA...] [CHECKSUM]
```

After startComms establishes the session, messages use a simplified format (no address bytes):

```
[LEN] [SID] [DATA...] [CHECKSUM]
```

**FMT byte encoding**:
- Bits [7:6] — address mode: `10` = physical addressing (TGT+SRC follow), `00` = no address info
- Bits [5:0] — data length (0–63 bytes); if 0, an additional LEN byte follows

**Positive response convention**: response SID = request SID + **`0x40`** (e.g., `0x27` → `0x67`, `0x10` → `0x50`, `0x21` → `0x61`)

**Negative response format**: `0x7F [rejected_SID] [error_code]`

---

## 4. Checksum calculation

The checksum is a simple **modulo-256 sum**: take the arithmetic sum of all bytes in the frame except the checksum itself, then mask to the lowest 8 bits.

```
checksum = (sum of all preceding bytes) & 0xFF
```

Verification against known frames:
- `0x81 + 0x13 + 0xF7 + 0x81 = 0x20C` → checksum = **`0x0C`** ✓
- `0x03 + 0xC1 + 0x57 + 0x8F = 0x1AA` → checksum = **`0xAA`** ✓
- `0x02 + 0x10 + 0xA0 = 0xB2` → checksum = **`0xB2`** ✓
- `0x02 + 0x27 + 0x01 = 0x2A` → checksum = **`0x2A`** ✓
- `0x02 + 0x67 + 0x02 = 0x6B` → checksum = **`0x6B`** ✓

---

## 5. Seed-key security access algorithm

### Source repository

**Repository**: `pajacobson/td5keygen` — BSD-2-Clause license
**Author**: Paul Jacobson (OffTrack) — reverse-engineered from ECU firmware disassembly
**Languages**: C (80.1%), Python (19.9%)
**Published**: January 10, 2017

**Files in repository**:

| File | Purpose |
|---|---|
| `keygen.c` | Core algorithm — portable C with bit masks and shifts |
| `keygen.h` | Header — `typedef union` for accessing seed as bytes or uint16 |
| `keygen_bitfield.c` | Alternative — C bitfields eliminate bit-twiddling; faster but less portable |
| `keytool.py` | Python implementation |
| `demo.c` | Usage example — takes two hex bytes, outputs key |
| `table_generator.c` | Generates full lookup table of all 65536 seed-key pairs |

### Input/output specification

- **Input**: 16-bit unsigned seed (2 bytes, big-endian: `[high_byte][low_byte]`)
- **Output**: 16-bit unsigned key (2 bytes)
- **Seed combination**: `seed = high_byte << 8 | low_byte`
- **Key extraction**: `key_high = result >> 8; key_low = result & 0xFF`

### Algorithm type

The algorithm is an **LFSR (Linear Feedback Shift Register)** based computation. The README states that the `keygen_bitfield.c` version "uses C bitfields and union structure to eliminate the bit-twiddling" and that "the bits the algorithm manipulates are not obscured by masks and shifts" — confirming this is a bit-manipulation/shift-register algorithm.

### Source code access limitation

**Critical note**: I was unable to fetch the raw contents of `keygen.c`, `keygen_bitfield.c`, or `keytool.py` from GitHub during this research session. The web fetching tool blocked all attempts to access `raw.githubusercontent.com` and GitHub blob page URLs. **The exact polynomial constant, shift direction, iteration count, and complete implementation must be obtained by cloning the repository directly:**

```bash
git clone https://github.com/pajacobson/td5keygen.git
```

### Verified test vectors

These test vectors can be used to validate any implementation of the algorithm:

| Seed (hex) | Key (hex) | Source |
|---|---|---|
| **`0x34A5`** | **`0x54D3`** | td5keygen README (demo output) |
| `0xF0DD` | `0x7D51` | DiscoTD5.com keygen validation |
| `0xF0DE` | `0xF9A1` | DiscoTD5.com keygen validation |
| `0xF0DF` | `0xFCD1` | DiscoTD5.com keygen validation |
| `0xF0E0` | `0x2607` | DiscoTD5.com keygen validation |
| `0xF0E1` | `0x9303` | DiscoTD5.com keygen validation |
| `0xF0E2` | `0x2A0F` | DiscoTD5.com keygen validation |
| `0xF0E3` | `0x9506` | DiscoTD5.com keygen validation |
| `0xF0E4` | `0x321E` | DiscoTD5.com keygen validation |
| `0xF0E5` | `0x990E` | DiscoTD5.com keygen validation |

### Validation against Seed-Key.txt database

The keygen was validated against the complete 65,536-entry Seed-Key.txt lookup database. Results: **65,228 keys match exactly**. Approximately **308 entries** in the database were identified as filler/error values (`0x2020` or `0xF781` placeholders). An additional 2 entries are believed wrong in the database. The keygen was also independently verified in MATLAB and double-checked against the ECU assembly code.

### Compilation

```bash
gcc demo.c keygen.c -o demo.o
./demo.o
```

```
Td5 security key example usage.
Enter first byte of security key: 34
Enter second byte of security key: A5

seed: 34A5
key: 54D3
high byte: 54    low byte: D3
```

---

## 6. PID request and response frame formats

After authentication, diagnostic data is read using **SID `0x21`** (readDataByLocalIdentifier — manufacturer-specific, not standard OBD-II).

### Request format

```
[LEN] 0x21 [PID_ID] [CHECKSUM]
```

Example: `0x02 0x21 0x01 0x24` — request fuelling data (PID `0x01`)

### Response format

```
[LEN] 0x61 [PID_ID] [data bytes...] [CHECKSUM]
```

Positive response SID = `0x21 + 0x40` = **`0x61`**

### Known service IDs

| SID | Service | Sub-function | Description |
|---|---|---|---|
| `0x81` | startCommunications | — | Initial connection (with TGT/SRC) |
| `0x82` | stopCommunication | — | End diagnostic session |
| `0x10` | diagnosticSessionControl | `0xA0` | Start manufacturer diagnostic mode |
| `0x27` | securityAccess | `0x01` / `0x02` | Request seed / Send key |
| **`0x21`** | **readDataByLocalIdentifier** | **PID ID** | **Primary data read command** |
| `0x30` | inputOutputControlByLocalIdentifier | varies | Test outputs (A/C clutch, MIL, etc.) |
| `0x14` | clearDiagnosticInformation | — | Clear fault codes |
| `0x18`/`0x19` | readDTCByStatus | — | Read fault codes |
| `0x3E` | testerPresent | — | **Keepalive / heartbeat** |
| `0x23` | readMemoryByAddress | address | Read flash memory (NNN ECUs) |
| `0x34`/`0x36`/`0x37` | requestDownload/transferData/transferExit | — | Flash programming (NNN ECUs) |

### Known PID IDs (for SID 0x21)

| PID ID | Description | Notes |
|---|---|---|
| `0x01` | **Fuelling parameters** | RPM, temps, pressures, injection quantities — 22+ fields |
| `0x08` / `0x09` | Input switches | Brake pedal, cruise control, digital inputs |
| `0x1A` | Logged/historic faults | Fault codes from previous power cycles |
| `0x20` | Current/active faults | Currently active fault codes |

---

## 7. Fuelling parameter data fields (PID `0x21 0x01`)

The response payload for the fuelling PID contains **22 data fields** in the order documented by the Td5OpenDiag/TD5Tester project README. Raw values in the ECU response are 16-bit unsigned integers.

| # | Parameter | Raw units | Conversion to display units |
|---|---|---|---|
| 1 | Engine RPM | RPM | `raw_value` |
| 2 | Battery voltage | mV | `raw_value / 1000` → Volts |
| 3 | Vehicle speed | km/h | `raw_value` (× 0.621371 for MPH) |
| 4 | Coolant temperature | 0.1 K | `(raw_value - 2732) / 10` → °C |
| 5 | External temperature | 0.1 K | `(raw_value - 2732) / 10` → °C |
| 6 | Inlet temperature | 0.1 K | `(raw_value - 2732) / 10` → °C |
| 7 | Fuel temperature | 0.1 K | `(raw_value - 2732) / 10` → °C |
| 8 | Accelerator track 1 | mV | `raw_value / 1000` → Volts |
| 9 | Accelerator track 2 | mV | `raw_value / 1000` → Volts |
| 10 | Accelerator track 3 | 0.01% | `raw_value / 100` → % |
| 11 | Accelerator supply | mV | `raw_value / 1000` → Volts |
| 12 | Ambient pressure | 0.01 kPa | `raw_value / 100` → kPa |
| 13 | Manifold air pressure (MAP) | 0.01 kPa | `raw_value / 100` → kPa |
| 14 | Manifold air flow (MAF) | 0.1 kg/h | `raw_value / 10` → kg/h |
| 15 | Driver demand | 0.01 mg/stroke | `raw_value / 100` → mg/stroke |
| 16 | MAF air mass | 0.1 mg/stroke | `raw_value / 10` → mg/stroke |
| 17 | MAP air mass | 0.1 mg/stroke | `raw_value / 10` → mg/stroke |
| 18 | Injection quantity | 0.01 mg/stroke | `raw_value / 100` → mg/stroke |
| 19 | Fuel demand 5 | unknown | `raw_value / 100` |
| 20 | Torque limit | 0.01 mg/stroke | `raw_value / 100` → mg/stroke |
| 21 | Smoke limit | 0.01 mg/stroke | `raw_value / 100` → mg/stroke |
| 22 | Idle demand | 0.01 mg/stroke | `raw_value / 100` → mg/stroke |

**Temperature conversion note**: all temperature values use a **Kelvin × 10** representation. The constant **2732** equals 273.2 K (0°C). So `(raw - 2732) / 10` converts tenths-of-Kelvin to degrees Celsius.

Ekaitza_Itzali additionally reads **EGR modulator duty**, **wastegate duty**, and **WGM duty ratio** — parameters the Nanocom does not expose.

---

## 8. Timing parameters

| Parameter | Value | Source / Notes |
|---|---|---|
| **Baud rate** | 10400 bps | All sources agree |
| **TiniL** (Fast Init low) | **25ms ±1ms** | ISO 14230 spec; Ekaitza README |
| **TiniH** (Fast Init high) | **25ms ±1ms** | ISO 14230 spec; Ekaitza README |
| **Twup** (total wakeup) | **50ms ±1ms** | ISO 14230 spec |
| Tidle (pre-init idle) | ≥300ms | After ECU power-up, K-line must idle high |
| **P1** (inter-byte, ECU→tester) | 0–20ms | ISO 14230 standard |
| **P4** (inter-byte, tester→ECU) | **5ms recommended** | ECU does not strictly enforce; works with or without |
| **P2** (ECU response time) | ≤50ms | Time from end of request to start of ECU response |
| **P3max** (session timeout) | **~5000ms** | Maximum time between tester requests before ECU drops session |
| Single PID cycle time | ~20ms | Best case on bench (OffTrack measurement) |
| Nanocom full log line | ~1.25 seconds | Multiple PIDs per log line |

OffTrack's key reliability observation: *"The ECU doesn't seem to care too much about interbyte timing... I've tried it with and without the 5ms space between bytes when sending and it doesn't really make any appreciable difference."* However, *"The closer you follow the error handling and timing from ISO14230 the more robust the comms."*

---

## 9. Keepalive mechanism

The ECU requires periodic communication to maintain the diagnostic session. Without activity, the session drops after the **P3max timeout (~5 seconds)**.

**Keepalive command**: SID **`0x3E`** (testerPresent) — standard ISO 14230 service.

```
Request:   [0x01] [0x3E] [checksum]
Response:  [0x01] [0x7E] [checksum]     (0x3E + 0x40 = 0x7E)
```

The Td5OpenDiag TODO list explicitly notes: *"Implement the KEEP_ALIVE PID so connection is kept active whilst no other PIDs are being sent"*, confirming this is the standard mechanism. When actively polling PIDs (e.g., fuelling data in a loop), the polling itself serves as the keepalive.

---

## 10. Error handling and negative responses

**Negative response frame**: `[LEN] 0x7F [rejected_SID] [error_code] [checksum]`

Standard ISO 14230 / KWP2000 error codes applicable to TD5:

| Code | Meaning |
|---|---|
| `0x10` | generalReject |
| `0x11` | serviceNotSupported |
| `0x12` | subFunctionNotSupported |
| `0x13` | incorrectMessageLengthOrInvalidFormat |
| `0x22` | conditionsNotCorrect (e.g., engine running during test output) |
| `0x31` | requestOutOfRange |
| `0x33` | securityAccessDenied |
| `0x35` | invalidKey |
| `0x36` | exceededNumberOfAttempts |
| `0x78` | requestCorrectlyReceivedResponsePending |

**Checksum failure handling**: if a received message has a bad checksum, the tester should **resend the request** rather than abort. OffTrack notes this was a critical reliability fix — the K-line occasionally corrupts a byte during in-vehicle transmission.

**Security lockout**: per ISO 14230 spec, after two failed key attempts, the ECU imposes a **10-second delay** before accepting further securityAccess requests.

---

## 11. ECU types and flash capability

| ECU type | Part number prefix | Flash capability | Driver chip |
|---|---|---|---|
| **MSB** | MSBxxxxxx | No flash — earlier models | — |
| **NNN** (variant 1) | NNN000xxx | Flash programmable | Intersil HIP0060 |
| **NNN** (variant 2) | NNN500xxx | Flash programmable | Infineon TLE6220GP |

Both types use the same diagnostic protocol. NNN ECUs additionally support **readMemoryByAddress** (`0x23`) for map reading and **requestDownload/transferData** (`0x34`/`0x36`/`0x37`) for reflashing. The Ekaitza_Itzali project includes `read_NNN_Flash.py` and `read_NNN_RAM.py` for these operations.

The ECU processor is a **Motorola/Freescale MC68332** family with crystal likely at 4.194 MHz, PLL-configured to 16.776 MHz. EEPROM (93C46 for MSB, 93C66 for NNN) stores injector codes, fault codes, and immobiliser codes.

---

## 12. Repository file inventories

### EA2EGA/Ekaitza_Itzali

| File | Language | Purpose |
|---|---|---|
| `main.py` | Python | Main diagnostic session: init, auth, PID requests |
| `main_menu.py` | Python | Menu/UI interface layer |
| `post1.py` | Python | Post-processing / additional protocol handling |
| `read_NNN_Flash.py` | Python | Flash memory reader for NNN ECUs |
| `read_NNN_RAM.py` | Python | RAM reader for NNN ECUs |
| `sniffer.py` | Python | K-Line serial bus sniffer |
| `fuelling_to_json.py` | Python | Fuelling parameter converter |
| `data.json` | JSON | ECU diagnostic parameter definitions |
| `Fuelling_params.txt` | Text | Fuelling parameter documentation |
| `Sniffing/` | Directory | Captured sniffing data / earlier Arduino-era code |

### pajacobson/td5keygen

| File | Language | Purpose |
|---|---|---|
| `keygen.c` | C | Core LFSR algorithm — portable, uses bit masks/shifts |
| `keygen.h` | C | Header with `typedef union` for seed byte access |
| `keygen_bitfield.c` | C | Alternative using C bitfields — faster, less portable |
| `keytool.py` | Python | Python implementation of keygen |
| `demo.c` | C | Usage demonstration |
| `table_generator.c` | C | Generates full 65536-entry lookup table |

---

## Conclusion

The TD5 diagnostic protocol is a well-documented, manufacturer-specific implementation of ISO 14230 (KWP2000) with a **10400 baud K-line physical layer**, a **4-step Fast Init handshake** (wakeup → startComms → startDiagnostic → seed/key auth), and **LFSR-based seed-key security**. Every byte in the initialisation sequence is confirmed from the Ekaitza_Itzali and td5keygen READMEs. The 22-field fuelling PID response structure is documented by Td5OpenDiag with exact unit conversions.

**The one critical gap is the exact keygen algorithm implementation** — the polynomial constant, shift direction, and loop structure reside in `keygen.c` and `keytool.py`, which must be obtained by cloning `https://github.com/pajacobson/td5keygen.git` directly. The 10 test vectors provided above (including the README-confirmed `0x34A5 → 0x54D3`) enable immediate validation of any implementation. For the protocol layer, no source code access was necessary: the README documentation from both repositories, combined with the Td5OpenDiag CSV field specification, provides byte-level completeness for every diagnostic frame format, timing parameter, and data conversion used in TD5 ECU communication.