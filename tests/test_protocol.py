"""
Tests for backend/obd/protocol.py — KWP2000 frame builder, seed-key, constants.

Checksum and frame test vectors from TD5-ECU-Protocol-Technical-Reference.md,
confirmed on vehicle 2026-03-21.
"""

import pytest
from obd.protocol import (
    checksum,
    build_frame,
    build_start_comm,
    td5_seed_to_key,
    ECU_ADDR,
    TESTER_ADDR,
    SVC_START_COMMUNICATION,
    SVC_START_DIAG,
    SVC_SECURITY_ACCESS,
    SVC_READ_LOCAL_ID,
    SVC_TESTER_PRESENT,
    SVC_STOP_COMMUNICATION,
    POSITIVE_RESPONSE_OFFSET,
    SA_REQUEST_SEED,
    SA_SEND_KEY,
    PID_RPM,
    PID_TEMPS,
    PID_MAP_MAF,
    PID_BATTERY,
    PID_SPEED,
    PID_THROTTLE,
    PID_FAULTS,
    PID_FUELLING,
    BAUD_RATE,
)


# ── Checksum ─────────────────────────────────────────────────────────────────

class TestChecksum:
    def test_start_comm(self):
        """StartComm frame sans checksum: 81 13 F7 81 → CS = 0x0C."""
        data = bytes([0x81, 0x13, 0xF7, 0x81])
        assert checksum(data) == 0x0C

    def test_diag_session(self):
        """DiagSession frame sans checksum: 02 10 A0 → CS = 0xB2."""
        data = bytes([0x02, 0x10, 0xA0])
        assert checksum(data) == 0xB2

    def test_security_request_seed(self):
        """SecurityAccess RequestSeed sans checksum: 02 27 01 → CS = 0x2A."""
        data = bytes([0x02, 0x27, 0x01])
        assert checksum(data) == 0x2A

    def test_empty(self):
        assert checksum(b'') == 0x00

    def test_single_byte(self):
        assert checksum(b'\xFF') == 0xFF

    def test_overflow_wraps(self):
        """Checksum wraps at 256 (mod 256)."""
        assert checksum(b'\xFF\x01') == 0x00
        assert checksum(b'\xFF\x02') == 0x01


# ── build_start_comm ─────────────────────────────────────────────────────────

class TestBuildStartComm:
    def test_vehicle_confirmed_bytes(self):
        """Vehicle-confirmed: 81 13 F7 81 0C."""
        frame = build_start_comm()
        assert frame == b'\x81\x13\xF7\x81\x0C'

    def test_length(self):
        assert len(build_start_comm()) == 5

    def test_contains_ecu_and_tester_addresses(self):
        frame = build_start_comm()
        assert frame[1] == ECU_ADDR      # 0x13
        assert frame[2] == TESTER_ADDR   # 0xF7

    def test_checksum_is_last_byte(self):
        frame = build_start_comm()
        assert frame[-1] == checksum(frame[:-1])


# ── build_frame ──────────────────────────────────────────────────────────────

class TestBuildFrame:
    def test_diag_session(self):
        """build_frame(0x10, 0xA0) → 02 10 A0 B2."""
        frame = build_frame(0x10, 0xA0)
        assert frame == b'\x02\x10\xA0\xB2'

    def test_security_request_seed(self):
        """build_frame(0x27, 0x01) → 02 27 01 2A."""
        frame = build_frame(0x27, 0x01)
        assert frame == b'\x02\x27\x01\x2A'

    def test_tester_present(self):
        """TesterPresent: build_frame(0x3E)."""
        frame = build_frame(0x3E)
        expected_body = bytes([0x01, 0x3E])
        expected = expected_body + bytes([checksum(expected_body)])
        assert frame == expected

    def test_read_local_id_rpm(self):
        """ReadDataByLocalIdentifier for RPM PID."""
        frame = build_frame(SVC_READ_LOCAL_ID, PID_RPM)
        # Length = 2 (service + pid), then SVC, PID, checksum
        assert frame[0] == 2
        assert frame[1] == SVC_READ_LOCAL_ID
        assert frame[2] == PID_RPM
        assert frame[3] == checksum(frame[:-1])

    def test_checksum_always_present(self):
        """Every frame must end with an ISO 14230 checksum byte."""
        for svc, payload in [(0x10, (0xA0,)), (0x27, (0x01,)), (0x3E, ())]:
            frame = build_frame(svc, *payload)
            assert frame[-1] == checksum(frame[:-1]), \
                f"Frame for SVC 0x{svc:02X} missing valid checksum"

    def test_length_byte_is_correct(self):
        """First byte should be the count of data bytes that follow (excluding checksum)."""
        frame = build_frame(0x27, 0x01)
        length_byte = frame[0]
        data_bytes = frame[1:-1]  # everything between length and checksum
        assert length_byte == len(data_bytes)


# ── Seed-Key Algorithm ───────────────────────────────────────────────────────

class TestSeedToKey:
    def test_canonical_vector(self):
        """Canonical test vector from td5keygen README: 0x34A5 → 0x54D3."""
        assert td5_seed_to_key(0x34A5) == 0x54D3

    def test_vehicle_confirmed_BA08(self):
        """Vehicle-confirmed: seed 0xBA08 → key 0x70DC."""
        assert td5_seed_to_key(0xBA08) == 0x70DC

    def test_zero_seed(self):
        """Seed 0 should not crash and should return a 16-bit value."""
        result = td5_seed_to_key(0x0000)
        assert 0 <= result <= 0xFFFF

    def test_max_seed(self):
        """Seed 0xFFFF should not crash and should return a 16-bit value."""
        result = td5_seed_to_key(0xFFFF)
        assert 0 <= result <= 0xFFFF

    def test_result_is_16bit(self):
        """All outputs must be 16-bit unsigned."""
        for seed in [0x0000, 0x1234, 0x34A5, 0xBA08, 0xFFFF, 0xDEAD]:
            result = td5_seed_to_key(seed)
            assert 0 <= result <= 0xFFFF, f"seed 0x{seed:04X} gave out-of-range result"

    def test_deterministic(self):
        """Same seed must always produce the same key."""
        for seed in [0x34A5, 0xBA08]:
            a = td5_seed_to_key(seed)
            b = td5_seed_to_key(seed)
            assert a == b


# ── Constants ────────────────────────────────────────────────────────────────

class TestConstants:
    def test_ecu_addr(self):
        assert ECU_ADDR == 0x13

    def test_tester_addr(self):
        assert TESTER_ADDR == 0xF7

    def test_baud_rate(self):
        assert BAUD_RATE == 10400

    def test_positive_response_offset(self):
        assert POSITIVE_RESPONSE_OFFSET == 0x40

    def test_service_ids(self):
        assert SVC_START_COMMUNICATION == 0x81
        assert SVC_STOP_COMMUNICATION == 0x82
        assert SVC_START_DIAG == 0x10
        assert SVC_SECURITY_ACCESS == 0x27
        assert SVC_READ_LOCAL_ID == 0x21
        assert SVC_TESTER_PRESENT == 0x3E

    def test_security_subfunctions(self):
        assert SA_REQUEST_SEED == 0x01
        assert SA_SEND_KEY == 0x02

    def test_pid_values(self):
        assert PID_RPM == 0x09
        assert PID_TEMPS == 0x1A
        assert PID_MAP_MAF == 0x1C
        assert PID_BATTERY == 0x10
        assert PID_SPEED == 0x0D
        assert PID_THROTTLE == 0x1B
        assert PID_FAULTS == 0x20
        assert PID_FUELLING == 0x01
