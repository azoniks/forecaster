import unittest
import datetime as dt
import tempfile
from collections import Counter
from pathlib import Path

from forecast_app.model import _allocate_hour, _historical_change, _previous_year, evaluate_sla_capacity, recommendations
from forecast_app.config import StaffingConfig
from forecast_app.service import ForecastService


class AllocationTest(unittest.TestCase):
    def test_daily_people_use_hourly_peak_reserve_and_shared_skills(self):
        positions = [{"id": "specialist", "name": "Специалист", "capacity": 4,
                      "groups": ["shared", "specialist"], "night_groups": []}]
        def row(hour, shared, specialist):
            values = {"shared": shared, "specialist": specialist, "expert": 0}
            return {"date": "2026-09-08", "hour": hour, "demand": values, "deficit": values}
        result = ForecastService._daily_rows([row(9, 1, 2), row(10, 5, 0)], positions, 25)[0]
        self.assertEqual(result["missing_people"], 1)
        self.assertEqual(result["peak_hour"], 10)
        self.assertEqual(result["missing_by_role"], {"Специалист": 1})
        self.assertEqual(result["peak_missing_people"], 2)
        shared = ForecastService._daily_rows([row(9, 1, 2)], positions, 25)[0]
        self.assertEqual(shared["missing_people"], 1)
        night = ForecastService._daily_rows([row(1, 1, 0)], positions, 25)[0]
        self.assertEqual(night["uncovered_groups"], ["shared"])
        covered = ForecastService._daily_rows([row(9, 0, 0)], positions, 25)[0]
        self.assertEqual(covered["missing_people"], 0)
        self.assertIsNone(covered["peak_hour"])

    @staticmethod
    def _empty_month(service, name, first, days):
        dates = [(first + dt.timedelta(days=offset)).isoformat() for offset in range(days)]
        service.database.create_schedule_month(name, dates, [], [])

    def test_added_schedule_employee_is_propagated_to_existing_future_months(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ForecastService(Path(directory))
            self._empty_month(service, "январь 26", dt.date(2026, 1, 1), 31)
            self._empty_month(service, "февраль 26", dt.date(2026, 2, 1), 28)
            service.add_schedule_employee({
                "sheet": "январь 26", "name": "Иванов", "login": "ivanov",
                "role": "Специалист", "schedule_pattern": "5/2 09-18",
            })
            january = service.schedule("январь 26")
            february = service.schedule("февраль 26")
            self.assertEqual([item["id"] for item in january["employees"]], ["ivanov"])
            self.assertEqual([item["id"] for item in february["employees"]], ["ivanov"])
            self.assertIn("2026-02-02", february["employees"][0]["shifts"])

    def test_terminated_employee_keeps_history_and_loses_future_shifts(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ForecastService(Path(directory))
            self._empty_month(service, "январь 26", dt.date(2026, 1, 1), 31)
            self._empty_month(service, "февраль 26", dt.date(2026, 2, 1), 28)
            service.add_schedule_employee({
                "sheet": "январь 26", "name": "Иванов", "login": "ivanov",
                "role": "Специалист", "schedule_pattern": "5/2 09-18",
            })
            service.delete_schedule_employee({
                "sheet": "январь 26", "employee_id": "ivanov",
                "deletion_date": "2026-01-15",
            })
            january = service.schedule("январь 26")
            february = service.schedule("февраль 26")
            self.assertEqual([item["id"] for item in january["employees"]], ["ivanov"])
            self.assertTrue(all(day < "2026-01-15" for day in january["employees"][0]["shifts"]))
            self.assertEqual(february["employees"], [])

    def test_terminated_employee_is_not_copied_into_new_month(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ForecastService(Path(directory))
            self._empty_month(service, "январь 26", dt.date(2026, 1, 1), 31)
            service.add_schedule_employee({
                "sheet": "январь 26", "name": "Иванов", "login": "ivanov",
                "role": "Специалист", "schedule_pattern": "5/2 09-18",
            })
            service.delete_schedule_employee({
                "sheet": "январь 26", "employee_id": "ivanov",
                "deletion_date": "2026-01-15",
            })
            february = service.create_schedule_month({"source": "январь 26"})
            self.assertEqual(february["employees"], [])

    def test_hiring_plan_selects_two_two_for_weekend_deficit(self):
        positions = [{"id": "role", "name": "Специалист", "capacity": 1, "enabled": True, "groups": ["shared"], "night_groups": ["shared"]}]
        rows = [{"date": "2026-08-01", "hour": hour, "deficit": {"shared": 8, "specialist": 0, "expert": 0}} for hour in range(8, 20)]
        plan = recommendations(rows, positions)[0]
        self.assertEqual(plan["schedule_type"], "2/2")
        self.assertEqual(plan["shift_duration_hours"], 12)
        self.assertGreaterEqual(plan["people"], 2)
        self.assertIn("выходные", plan["schedule_reason"])

    def test_historical_change_compares_same_dates_previous_year(self):
        history = {
            "meta": {"last_date": "2026-06-30"},
            "daily": [
                ["2025-08-01", "shared", 100],
                ["2025-08-02", "shared", 100],
                ["2026-06-29", "shared", 1000],
                ["2026-06-30", "shared", 1000],
            ],
        }
        forecast = [
            {"date": "2026-08-01", "demand": {"shared": 120, "specialist": 0, "expert": 0}},
            {"date": "2026-08-02", "demand": {"shared": 120, "specialist": 0, "expert": 0}},
        ]
        self.assertEqual(_historical_change(history, forecast)["overall"], 20.0)
        self.assertEqual(_previous_year(dt.date(2024, 2, 29)), dt.date(2023, 2, 28))

    def test_schedule_pattern_inference_and_holidays(self):
        dates = [dt.date(2026, 10, day) for day in range(1, 32)]
        cells = [
            {"date": date.isoformat(), "start": 9, "end": 18, "activity": "work"}
            for date in dates if date.weekday() < 5
        ]
        pattern, start, end, phase = ForecastService._infer_schedule_pattern(dates, cells)
        self.assertEqual((pattern, start, end, phase), ("5/2 09-18", 9, 18, None))
        self.assertTrue(ForecastService._russian_holiday(dt.date(2026, 11, 4)))

    def test_two_two_pattern_keeps_absolute_phase(self):
        dates = [dt.date(2026, 10, day) for day in range(1, 32)]
        phase = 2
        cells = [
            {"date": date.isoformat(), "start": 8, "end": 20, "activity": "work"}
            for date in dates if (date.toordinal() - phase) % 4 < 2
        ]
        pattern, _, _, inferred_phase = ForecastService._infer_schedule_pattern(dates, cells)
        self.assertEqual(pattern, "2/2 08-20")
        self.assertEqual(inferred_phase, phase)

    def test_role_seasonality_marks_peak_and_calm_months(self):
        history = {"daily": [
            ["2026-01-01", "shared", 80], ["2026-02-01", "shared", 100],
            ["2026-03-01", "shared", 120],
        ]}
        positions = [{"id": "role", "groups": ["shared"], "night_groups": []}]
        self.assertEqual(ForecastService._role_seasonality(history, positions, 1)["role"]["level"], "calm")
        self.assertEqual(ForecastService._role_seasonality(history, positions, 3)["role"]["level"], "peak")

    def test_operating_hours_support_day_overnight_and_24_7(self):
        active = ForecastService._is_operating_hour
        self.assertTrue(active(10, 9, 18))
        self.assertFalse(active(20, 9, 18))
        self.assertTrue(active(23, 20, 8))
        self.assertTrue(active(5, 20, 8))
        self.assertFalse(active(12, 20, 8))
        self.assertTrue(active(12, 0, 0))

    def test_legacy_night_setting_migrates_to_operating_hours(self):
        value = {
            "night_operation_enabled": False,
            "positions": [{
                "id": "role", "name": "Роль", "capacity": 1,
                "enabled": True, "groups": ["shared"], "night_groups": ["shared"],
            }],
        }
        config = StaffingConfig.validate(value)
        self.assertEqual((config["operation_start_hour"], config["operation_end_hour"]), (6, 23))

    def test_sla_settings_support_group_defaults_and_queue_overrides(self):
        value = {
            "positions": [{
                "id": "role", "name": "Роль", "capacity": 1,
                "enabled": True, "groups": ["shared"], "night_groups": ["shared"],
            }],
            "sla_groups": {"shared": {"target_percent": 85, "threshold_minutes": 20}},
            "sla_queues": {"Очередь": {"target_percent": 95, "threshold_minutes": None}},
        }
        config = StaffingConfig.validate(value)
        inherited = ForecastService._effective_sla(config, "shared", "Другая очередь")
        overridden = ForecastService._effective_sla(config, "shared", "Очередь")
        self.assertEqual(inherited, {"target_percent": 85.0, "threshold_minutes": 20.0})
        self.assertEqual(overridden, {"target_percent": 95.0, "threshold_minutes": 20.0})

    def test_incomplete_trailing_day_is_excluded(self):
        history = {
            "meta": {"first_date": "2026-06-01", "last_date": "2026-07-01"},
            "daily": [
                ["2026-06-03", "shared", 100], ["2026-06-10", "shared", 110],
                ["2026-06-17", "shared", 90], ["2026-06-24", "shared", 100],
                ["2026-07-01", "shared", 10],
            ],
            "hourly": [
                ["2026-06-03", 23, "shared", 1], ["2026-06-10", 23, "shared", 1],
                ["2026-06-17", 22, "shared", 1], ["2026-06-24", 23, "shared", 1],
                ["2026-07-01", 2, "shared", 10],
            ],
        }
        cleaned = ForecastService._exclude_incomplete_tail(history)
        self.assertEqual(cleaned["meta"]["last_date"], "2026-06-30")
        self.assertEqual(cleaned["meta"]["excluded_incomplete_dates"], ["2026-07-01"])

    def test_sla_capacity_carries_work_without_counting_it_twice(self):
        position = [{
            "id": "role", "name": "Роль", "schedule_role": "Роль",
            "capacity": 6, "enabled": True,
            "groups": ["shared"], "night_groups": ["shared"],
        }]
        forecast = [{
            "date": "2026-01-01", "hour": 10, "minute": index * 10, "interval": 60 + index,
            "demand": {"shared": 4 if index == 0 else 0, "specialist": 0, "expert": 0},
        } for index in range(3)]
        staff = {("2026-01-01", 10): Counter({"role:day": 1})}
        rows = evaluate_sla_capacity(forecast, staff, 0, position, {"shared": 30, "specialist": 30, "expert": 40})
        self.assertEqual(sum(row["deficit"]["shared"] for row in rows), 1.0)

    def test_specialists_cover_specialist_before_shared(self):
        result = _allocate_hour(
            {"shared": 4, "specialist": 4, "expert": 0},
            Counter(junior=0, specialist=1),
            reserve_percent=0,
        )
        self.assertEqual(result["deficit"]["specialist"], 0)
        self.assertEqual(result["deficit"]["shared"], 4)

    def test_night_expert_spills_into_line_one(self):
        result = _allocate_hour(
            {"shared": 1, "specialist": 2, "expert": 3},
            Counter(night_expert=2),
            reserve_percent=0,
        )
        self.assertEqual(result["deficit"], {"shared": 0.0, "specialist": 0.0, "expert": 0.0})

    def test_reserve_reduces_capacity(self):
        result = _allocate_hour(
            {"shared": 4, "specialist": 0, "expert": 0},
            Counter(junior=1),
            reserve_percent=25,
        )
        self.assertEqual(result["deficit"]["shared"], 1.0)

    def test_custom_position_capacity_and_skills(self):
        positions = [{
            "id": "universal",
            "name": "Универсал",
            "schedule_role": "Универсал",
            "capacity": 5.0,
            "enabled": True,
            "groups": ["specialist", "shared"],
            "night_groups": ["expert", "specialist", "shared"],
        }]
        result = _allocate_hour(
            {"shared": 2, "specialist": 3, "expert": 2},
            Counter({"universal:day": 1}),
            reserve_percent=0,
            positions=positions,
        )
        self.assertEqual(result["deficit"], {"expert": 2.0, "specialist": 0.0, "shared": 0.0})

    def test_disabled_position_does_not_add_capacity(self):
        positions = [{
            "id": "disabled",
            "name": "Отключённая",
            "schedule_role": "Отключённая",
            "capacity": 100.0,
            "enabled": False,
            "groups": ["shared"],
            "night_groups": ["shared"],
        }]
        result = _allocate_hour(
            {"shared": 1, "specialist": 0, "expert": 0},
            Counter({"disabled:day": 1}),
            reserve_percent=0,
            positions=positions,
        )
        self.assertEqual(result["deficit"]["shared"], 1.0)


if __name__ == "__main__":
    unittest.main()
