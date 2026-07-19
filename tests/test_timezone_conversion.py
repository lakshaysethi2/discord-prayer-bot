"""Tests for timezone conversion: UTC storage + client-side browser conversion.

Key invariants:
- Server always stores UTC
- Client browser detects timezone and converts UTC ↔ local on display/form submit
- Round-trip: local → UTC → local must give the original local time
"""

from __future__ import annotations

from datetime import datetime, time, timezone, timedelta

import pytest

from dashboard.prayers_routes import _format_time_local


class TestFormatTimeLocal:
    """Tests for the server-side _format_time_local helper (used in public view)."""

    def test_utc_plus_offset(self):
        """UTC 06:00 + 12h offset = 18:00 local."""
        result = _format_time_local(time(6, 0), 12.0)
        assert result == "18:00"

    def test_utc_minus_offset(self):
        """UTC 18:00 - 5h offset = 13:00 local."""
        result = _format_time_local(time(18, 0), -5.0)
        assert result == "13:00"

    def test_utc_wrap_around_midnight_positive(self):
        """UTC 20:00 + 5.5h = 01:30 local (next day)."""
        result = _format_time_local(time(20, 0), 5.5)
        assert result == "01:30"

    def test_utc_wrap_around_midnight_negative(self):
        """UTC 02:00 - 3h = 23:00 local (previous day)."""
        result = _format_time_local(time(2, 0), -3.0)
        assert result == "23:00"

    def test_zero_offset(self):
        """UTC 12:00 + 0h = 12:00."""
        result = _format_time_local(time(12, 0), 0.0)
        assert result == "12:00"

    def test_half_hour_offset(self):
        """UTC 09:00 + 5.5h = 14:30."""
        result = _format_time_local(time(9, 0), 5.5)
        assert result == "14:30"


class TestTimezoneRoundTrip:
    """Simulate the browser's local→UTC→local conversion."""

    @staticmethod
    def local_to_utc(local_h: int, local_m: int, offset_hours: float) -> time:
        """Simulate browser JS: new Date with local time, read getUTCHours()."""
        # Create a UTC-aware datetime at the given offset
        tz = timezone(timedelta(hours=offset_hours))
        local_dt = datetime(2024, 1, 1, local_h, local_m, tzinfo=tz)
        utc_dt = local_dt.astimezone(timezone.utc)
        return time(utc_dt.hour, utc_dt.minute)

    @staticmethod
    def utc_to_local(utc_h: int, utc_m: int, offset_hours: float) -> time:
        """Simulate browser JS: new Date(Date.UTC(h,m)), read getHours()."""
        utc_dt = datetime(2024, 1, 1, utc_h, utc_m, tzinfo=timezone.utc)
        local_tz = timezone(timedelta(hours=offset_hours))
        local_dt = utc_dt.astimezone(local_tz)
        return time(local_dt.hour, local_dt.minute)

    @pytest.mark.parametrize("local_h,local_m,offset", [
        (7, 30, 12.0),    # NZST: 7:30 AM → 19:30 UTC
        (18, 0, -5.0),    # EST:  6:00 PM → 23:00 UTC
        (8, 0, 5.5),      # IST:  8:00 AM → 02:30 UTC
        (12, 0, 0.0),     # UTC:  12:00 → 12:00 UTC
        (23, 30, 12.0),   # NZST: 11:30 PM → 11:30 UTC
        (0, 15, -8.0),    # PST:  12:15 AM → 08:15 UTC
    ])
    def test_local_to_utc_round_trip(self, local_h, local_m, offset):
        """local → UTC → local must return the original local time."""
        utc_time = self.local_to_utc(local_h, local_m, offset)
        back_to_local = self.utc_to_local(utc_time.hour, utc_time.minute, offset)
        assert back_to_local.hour == local_h
        assert back_to_local.minute == local_m

    @pytest.mark.parametrize("utc_h,utc_m,offset", [
        (19, 30, 12.0),   # 19:30 UTC → 07:30 NZST
        (23, 0, -5.0),    # 23:00 UTC → 18:00 EST
        (2, 30, 5.5),     # 02:30 UTC → 08:00 IST
        (11, 30, 12.0),   # 11:30 UTC → 23:30 NZST
    ])
    def test_utc_to_local_round_trip(self, utc_h, utc_m, offset):
        """UTC → local → UTC must return the original UTC time."""
        local_time = self.utc_to_local(utc_h, utc_m, offset)
        back_to_utc = self.local_to_utc(local_time.hour, local_time.minute, offset)
        assert back_to_utc.hour == utc_h
        assert back_to_utc.minute == utc_m


class TestDBUtcStorage:
    """Verify the DB layer stores and retrieves UTC times correctly."""

    def test_schedule_stored_as_utc(self):
        """upsert_schedule stores time_utc, get_weekly_schedule retrieves it."""
        from db.database import Database
        from db.prayers import upsert_schedule, get_weekly_schedule
        from db.models import PrayerType

        with Database(":memory:") as db:
            upsert_schedule(db, "g1", 1, PrayerType.BUDDHIST, time(19, 30), enabled=True)
            schedules = get_weekly_schedule(db, "g1")
            assert len(schedules) == 1
            assert schedules[0].time_utc == time(19, 30)
            assert schedules[0].prayer_type == PrayerType.BUDDHIST

    def test_multiple_schedules_different_utc_times(self):
        """Multiple prayer types on same day, all stored as UTC."""
        from db.database import Database
        from db.prayers import upsert_schedule, get_weekly_schedule
        from db.models import PrayerType

        with Database(":memory:") as db:
            upsert_schedule(db, "g1", 0, PrayerType.BUDDHIST, time(19, 30), enabled=True)
            upsert_schedule(db, "g1", 0, PrayerType.CHRISTIAN, time(20, 0), enabled=True)
            upsert_schedule(db, "g1", 0, PrayerType.JEWISH, time(21, 0), enabled=False)
            schedules = get_weekly_schedule(db, "g1")
            assert len(schedules) == 3
            utc_times = [(s.prayer_type.value, s.time_utc) for s in schedules]
            assert ("buddhist", time(19, 30)) in utc_times
            assert ("christian", time(20, 0)) in utc_times
            assert ("jewish", time(21, 0)) in utc_times
