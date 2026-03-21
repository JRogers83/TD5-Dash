"""
TD5 K-Line physical layer via PyFtdi (VAG-COM KKL cable, FTDI FT232RL).

The KKL cable contains a built-in K-Line level shifter (TTL ↔ 12 V) and
presents as an FTDI FT232RL USB-serial device (/dev/ttyUSB0 on Linux).

Fast-init sequence (ISO 9141-2 / KWP2000):
  1. Switch FTDI TX pin to GPIO bitbang mode
  2. Hold K-Line LOW  for 25 ms  — wakes the ECU
  3. Hold K-Line HIGH for 25 ms  — signals end of init pulse
  4. Return to UART mode at 10,400 baud
  5. The ECU responds with keyword bytes; the session can begin

The fast-init requires direct GPIO control of the TX pin, which is only
possible via PyFtdi's bitbang mode — ordinary pyserial cannot do this.

Reference: github.com/hairyone/pyTD5Tester
"""

import logging
import time

from . import protocol as P

log = logging.getLogger(__name__)


class KLineError(Exception):
    """Raised when K-Line communication fails."""


class KLineConnection:
    """
    Low-level K-Line connection over a VAG-COM KKL FTDI cable.

    Parameters
    ----------
    url : str
        PyFtdi device URL. Default 'ftdi://ftdi:232/1' resolves to the first
        FT232-series device found on the bus — correct for a Pi with only the
        KKL cable attached. Override with the TD5_FTDI_URL environment variable
        if multiple FTDI devices are present.
    """

    def __init__(self, url: str = 'ftdi://ftdi:232/1') -> None:
        self._url  = url
        self._ftdi = None
        self._last_rx_time = 0.0

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def open(self) -> None:
        """Open the FTDI device, perform fast-init, and prepare for UART I/O.

        Sends StopCommunication first to clear any leftover session from a
        previous crash — confirmed necessary because the ECU rejects
        StartCommunication with generalReject (0x10) if a session is active,
        and each rejection resets the P3max timer (deadlock).
        """
        try:
            from pyftdi.ftdi import Ftdi
        except ImportError as exc:
            raise KLineError(
                "pyftdi is not installed — add it to requirements.txt and "
                "run:  pip install pyftdi"
            ) from exc

        log.info("Opening FTDI device: %s", self._url)
        self._ftdi = Ftdi()
        self._ftdi.open_from_url(self._url)
        self._cleanup_session()
        self._fast_init()
        self._start_communication()
        log.info("K-Line initialised — ready at %d baud", P.BAUD_RATE)

    def close(self) -> None:
        if self._ftdi:
            try:
                self._stop_communication()
            except Exception:
                pass
            try:
                self._ftdi.close()
            except Exception:
                pass
            self._ftdi = None

    def __enter__(self) -> "KLineConnection":
        self.open()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ── Fast-init ──────────────────────────────────────────────────────────────

    def _fast_init(self) -> None:
        """
        ISO 9141-2 fast-init: drive K-Line low then high to wake the ECU.

        Uses FTDI bitbang mode to directly control the TX pin (GPIO bit 0).
        Timing is critical — the ECU expects the low pulse to be 25 ms ±3 ms.

        Troubleshooting:
          - ECU not responding after init → try adjusting FAST_INIT_LOW_MS
            in protocol.py by ±2 ms. Some TD5 ECUs are picky about timing.
          - 'device busy' error → ensure no other process holds /dev/ttyUSB0
            (e.g. `sudo lsof /dev/ttyUSB0`).
          - Key bytes not received after init → increase SETTLE_MS.
        """
        from pyftdi.ftdi import Ftdi

        TX_PIN = 0x01   # TX is bit 0 in the FT232 GPIO map

        # Purge RX buffer BEFORE the pulse.  Purging after the mode-switch risks
        # flushing keyword bytes the ECU sent during the brief UART→bitbang window.
        self._ftdi.purge_buffers()

        # Switch to bitbang mode — TX pin becomes a manually-driven GPIO output
        self._ftdi.set_bitmode(TX_PIN, Ftdi.BitMode.BITBANG)

        self._ftdi.write_data(bytes([0x00]))                     # K-Line LOW
        time.sleep(P.FAST_INIT_LOW_MS  / 1000.0)

        self._ftdi.write_data(bytes([TX_PIN]))                   # K-Line HIGH
        time.sleep(P.FAST_INIT_HIGH_MS / 1000.0)

        # Return to normal UART mode
        self._ftdi.set_bitmode(0x00, Ftdi.BitMode.RESET)
        self._ftdi.set_baudrate(P.BAUD_RATE)
        time.sleep(P.SETTLE_MS / 1000.0)

        # Purge fast-init pulse artifacts.  The bitbang LOW→HIGH transitions
        # are decoded by the FTDI UART as spurious bytes (0xC0, 0xCC, 0xFC).
        # In KWP2000, the ECU is completely silent after the wake pulse and
        # will not transmit anything until it receives StartCommunication, so
        # purging here does not risk discarding genuine ECU data.
        self._ftdi.purge_buffers()

    # ── StartCommunication ─────────────────────────────────────────────────────

    def _start_communication(self) -> None:
        """
        Send StartCommunication (SID 0x81) and verify the ECU responds.

        Uses the physical-addressing short format (see build_start_comm()).
        Confirmed frame: 81 13 F7 81 0C → ECU replies 03 C1 57 8F AA.

        This must be the first KWP2000 service after the fast-init wake pulse.
        The ECU is silent until it receives this greeting.
        """
        frame    = P.build_start_comm()
        expected = P.SVC_START_COMMUNICATION + P.POSITIVE_RESPONSE_OFFSET   # 0xC1
        self.send(frame)
        resp = self.recv_frame()
        # Response is short-format with no address bytes: [FMT][SVC][data…]
        # frame[1] is the service byte (0xC1 = StartCommunication positive)
        if len(resp) < 2 or resp[1] != expected:
            actual = resp[1] if len(resp) >= 2 else 0xFF
            raise KLineError(
                f"StartCommunication failed: expected 0x{expected:02X}, "
                f"got 0x{actual:02X} — frame: {resp.hex(' ')}"
            )
        log.info("StartCommunication accepted — keyword bytes: %s", resp[2:].hex(' '))

    def _stop_communication(self) -> None:
        """Send StopCommunication (0x82) for clean session teardown."""
        try:
            frame = P.build_frame(P.SVC_STOP_COMMUNICATION)
            log.debug("TX StopCommunication: %s", frame.hex(' '))
            for byte in frame:
                self._ftdi.write_data(bytes([byte]))
                time.sleep(P.P4_INTER_BYTE_MS / 1000.0)
            # Best-effort echo + response drain — don't raise on failure
            time.sleep(0.1)
            self._ftdi.purge_buffers()
        except Exception:
            pass

    def _cleanup_session(self) -> None:
        """Clear any leftover ECU session before fast-init.

        If the ECU is in an active session (e.g. from a previous crash), it
        rejects StartCommunication and each rejection resets the P3max timer.
        Sending StopCommunication on the raw UART breaks this deadlock.
        """
        self._ftdi.set_baudrate(P.BAUD_RATE)
        self._ftdi.purge_buffers()
        self._stop_communication()
        time.sleep(0.5)

    # ── Frame I/O ──────────────────────────────────────────────────────────────

    def _consume_echo(self, frame: bytes) -> None:
        """
        Read and discard the TX echo.

        K-Line is a half-duplex single-wire bus — every byte we transmit is
        immediately reflected back on RX.  This must be called right after the
        last byte of a frame is sent so that the subsequent recv_frame() call
        sees only the ECU's reply and not our own transmission.
        """
        needed   = len(frame)
        deadline = time.monotonic() + 0.1   # 100 ms is ample at 10,400 baud
        while needed > 0 and time.monotonic() < deadline:
            chunk = self._ftdi.read_data(needed)
            if chunk:
                log.debug("Echo consumed: %s", chunk.hex(' '))
                needed -= len(chunk)
            else:
                time.sleep(0.002)
        if needed > 0:
            log.warning("TX echo drain: expected %d more bytes — proceeding anyway", needed)

    def send(self, frame: bytes) -> None:
        """
        Write a KWP2000 frame byte-by-byte with P3 inter-message gap and
        P4 inter-byte timing, then consume the TX echo.

        P3 gap (55ms) between end of last ECU response and start of next
        request is critical when the engine is running — the ECU needs time
        between messages to service engine management tasks.
        """
        # P3 inter-message gap
        elapsed = time.monotonic() - self._last_rx_time
        p3_gap = P.P3_INTER_MSG_MS / 1000.0
        if self._last_rx_time > 0 and elapsed < p3_gap:
            time.sleep(p3_gap - elapsed)

        log.debug("TX: %s", frame.hex(' '))
        for byte in frame:
            self._ftdi.write_data(bytes([byte]))
            time.sleep(P.P4_INTER_BYTE_MS / 1000.0)
        self._consume_echo(frame)

    def recv(self, length: int, timeout_s: float = 0.5) -> bytes:
        """
        Read exactly `length` bytes, raising KLineError on timeout.
        """
        buf      = bytearray()
        deadline = time.monotonic() + timeout_s

        while len(buf) < length:
            if time.monotonic() > deadline:
                raise KLineError(
                    f"Timeout waiting for {length} bytes — "
                    f"received {len(buf)}: {buf.hex()}"
                )
            chunk = self._ftdi.read_data(length - len(buf))
            if chunk:
                buf.extend(chunk)
            else:
                time.sleep(0.005)

        log.debug("RX: %s", bytes(buf).hex(' '))
        return bytes(buf)

    def recv_frame(self, timeout_s: float = 2.0) -> bytes:
        """
        Read a KWP2000 frame from the ECU including the trailing checksum.

        Frame structure (ISO 14230 / TD5-ECU-Protocol-Technical-Reference):

            [FMT]            format/length byte
                               bit 7 = 1: TADDR + SADDR bytes follow
                               bit 7 = 0: no address bytes (most ECU responses)
                               bits 6-0:  number of data bytes that follow
            [TADDR]          (only if bit 7 set) target address
            [SADDR]          (only if bit 7 set) source address
            [data…]          service byte + payload — exactly (FMT & 0x7F) bytes
            [CS]             checksum = sum of all preceding bytes mod 256

        Returns the frame WITHOUT the checksum byte (for caller convenience).
        Logs a warning if the checksum doesn't match.
        """
        fmt_raw  = self.recv(1, timeout_s)
        fmt      = fmt_raw[0]
        has_addr = bool(fmt & 0x80)
        data_len = fmt & 0x7F

        if has_addr:
            addr  = self.recv(2)
            data  = self.recv(data_len)
            frame = fmt_raw + addr + data
        else:
            data  = self.recv(data_len)
            frame = fmt_raw + data

        # Read and verify checksum byte
        cs_raw   = self.recv(1)
        expected = P.checksum(frame)
        if cs_raw[0] != expected:
            log.warning(
                "Checksum mismatch: got 0x%02X, expected 0x%02X — frame: %s",
                cs_raw[0], expected, frame.hex(' '),
            )

        self._last_rx_time = time.monotonic()
        log.debug("RX frame: %s (cs=0x%02X)", frame.hex(' '), cs_raw[0])
        return frame
