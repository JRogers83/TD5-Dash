"""Tests for gps_service._parse_tpv — pure function, no gpsd required."""
import pytest


class FakeTPVReport:
    """Duck-type for a gpsd TPV report object."""
    def __init__(self, mode=3, lat=52.6309, lon=1.2974, speed=13.89, track=180.5):
        self.mode = mode
        self.lat = lat
        self.lon = lon
        self.speed = speed   # m/s from gpsd
        self.track = track   # degrees true

    def get(self, key, default=None):
        return getattr(self, key, default)


class TestParseTpv:
    def test_3d_fix_returns_correct_fields(self):
        from gps_service import _parse_tpv
        result = _parse_tpv(FakeTPVReport(mode=3, lat=52.6309, lon=1.2974,
                                          speed=13.89, track=180.5))
        assert result is not None
        assert result["fix"] == 3
        assert result["lat"] == 52.6309
        assert result["lon"] == 1.2974
        assert result["speed_kmh"] == pytest.approx(50.0, abs=0.2)
        assert result["heading_deg"] == 180.5

    def test_2d_fix_returns_data(self):
        from gps_service import _parse_tpv
        result = _parse_tpv(FakeTPVReport(mode=2))
        assert result is not None
        assert result["fix"] == 2

    def test_mode_0_returns_none(self):
        from gps_service import _parse_tpv
        assert _parse_tpv(FakeTPVReport(mode=0)) is None

    def test_mode_1_returns_none(self):
        from gps_service import _parse_tpv
        assert _parse_tpv(FakeTPVReport(mode=1)) is None

    def test_speed_converted_ms_to_kmh(self):
        from gps_service import _parse_tpv
        result = _parse_tpv(FakeTPVReport(speed=1.0))
        assert result["speed_kmh"] == pytest.approx(3.6, abs=0.01)

    def test_missing_attributes_default_to_zero(self):
        from gps_service import _parse_tpv

        class MinimalReport:
            mode = 3
            def get(self, k, d=None): return getattr(self, k, d)

        result = _parse_tpv(MinimalReport())
        assert result["lat"] == 0.0
        assert result["lon"] == 0.0
        assert result["speed_kmh"] == 0.0
        assert result["heading_deg"] == 0.0

    def test_none_attribute_values_treated_as_zero(self):
        from gps_service import _parse_tpv

        class NoneReport:
            mode = 3
            lat = None; lon = None; speed = None; track = None
            def get(self, k, d=None): return getattr(self, k, d)

        result = _parse_tpv(NoneReport())
        assert result["lat"] == 0.0

    def test_coordinates_rounded_to_6dp(self):
        from gps_service import _parse_tpv
        result = _parse_tpv(FakeTPVReport(lat=52.630912345678))
        assert result["lat"] == 52.630912

    def test_speed_rounded_to_1dp(self):
        from gps_service import _parse_tpv
        result = _parse_tpv(FakeTPVReport(speed=13.8888))
        assert result["speed_kmh"] == round(13.8888 * 3.6, 1)


class TestNoFixData:
    def test_all_numeric_fields_are_none(self):
        from gps_service import _NO_FIX_DATA
        assert _NO_FIX_DATA["lat"] is None
        assert _NO_FIX_DATA["lon"] is None
        assert _NO_FIX_DATA["speed_kmh"] is None
        assert _NO_FIX_DATA["heading_deg"] is None

    def test_fix_is_zero(self):
        from gps_service import _NO_FIX_DATA
        assert _NO_FIX_DATA["fix"] == 0
