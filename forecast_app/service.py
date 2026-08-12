from __future__ import annotations

import datetime as dt
import json
import math
import re
import calendar
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from statistics import median

from .config import StaffingConfig, default_config
from .database import AppDatabase

from .loaders import (
    available_schedule_sheets,
    load_schedule_details,
    load_or_build_history,
    load_schedule,
    shifts_to_hourly,
    Shift,
    QueueRule,
)
from .model import (
    ForecastParameters,
    build_forecast,
    evaluate_capacity,
    summarize,
)


class ForecastService:
    def __init__(self, root: Path):
        self.root = root
        self.workload_path = root / "нагрузка"
        self.queue_path = root / "очереди.xlsx"
        self.schedule_path = root / "МЕТРИКА (2).xlsx"
        self.cache_path = root / "cache" / "history.json"
        self.staffing_config = StaffingConfig(root / "config" / "staffing.json")
        self.schedule_overrides_path = root / "config" / "schedule_overrides.json"
        self.schedule_staff_path = root / "config" / "schedule_staff.json"
        self.employees_path = root / "config" / "employees.json"
        self.hr_records_path = root / "config" / "hr_records.json"
        self.hr_services_path = root / "config" / "hr_services.json"
        self.database = AppDatabase(root / "data" / "forecaster.sqlite3")
        self.database.migrate_documents({
            "staffing": self.staffing_config.path,
            "employees": self.employees_path,
            "hr_records": self.hr_records_path,
            "hr_services": self.hr_services_path,
        }, {
            "staffing": default_config(),
            "employees": [],
            "hr_records": {},
            "hr_services": [{"id": "metrika", "name": "Метрика"}],
        })
        self.database.migrate_schedules(
            self.schedule_path, self.schedule_staff_path, self.schedule_overrides_path
        )
        self._history = None
        self._queue_rules = None

    def _load_history(self):
        if self._history is None:
            snapshot = self.database.document("history_snapshot", None)
            if isinstance(snapshot, dict) and isinstance(snapshot.get("history"), dict):
                raw_history = snapshot["history"]
                self._queue_rules = self._deserialize_queue_rules(snapshot.get("queue_rules", {}))
            else:
                raw_history, self._queue_rules = load_or_build_history(
                    self.workload_path,
                    self.queue_path,
                    self.cache_path,
                )
                self._save_history_snapshot(raw_history, self._queue_rules)
            self._history = self._exclude_incomplete_tail(raw_history)
        return self._history

    @staticmethod
    def _deserialize_queue_rules(items: dict[str, object]) -> dict[str, QueueRule]:
        rules: dict[str, QueueRule] = {}
        for name, item in items.items():
            if not isinstance(item, dict):
                continue
            active_from = item.get("active_from")
            rules[name] = QueueRule(
                name=str(item.get("name") or name),
                subline=int(item.get("subline") or 0),
                active_from=dt.date.fromisoformat(active_from) if active_from else None,
                group=str(item.get("group") or "specialist"),
            )
        return rules

    def _save_history_snapshot(self, history: dict[str, object], rules: dict[str, QueueRule]):
        self.database.save_document("history_snapshot", {
            "history": history,
            "queue_rules": {
                name: {
                    "name": rule.name,
                    "subline": rule.subline,
                    "active_from": rule.active_from.isoformat() if rule.active_from else None,
                    "group": rule.group,
                }
                for name, rule in rules.items()
            },
        })

    @staticmethod
    def _exclude_incomplete_tail(history: dict[str, object]) -> dict[str, object]:
        """Remove trailing calendar days whose export clearly stopped several hours early."""
        daily_totals: dict[dt.date, float] = defaultdict(float)
        latest_hour: dict[dt.date, int] = {}
        for day, _group, count in history["daily"]:
            daily_totals[dt.date.fromisoformat(day)] += float(count)
        for day, hour, _group, _count in history["hourly"]:
            current = dt.date.fromisoformat(day)
            latest_hour[current] = max(latest_hour.get(current, -1), int(hour))

        excluded: list[dt.date] = []
        candidate = dt.date.fromisoformat(history["meta"]["last_date"])
        while candidate in daily_totals:
            comparable = sorted(
                (day for day in daily_totals if day < candidate and day.weekday() == candidate.weekday()),
                reverse=True,
            )[:8]
            if len(comparable) < 4:
                break
            normal_total = median(daily_totals[day] for day in comparable)
            normal_latest_hour = median(latest_hour.get(day, -1) for day in comparable)
            stopped_early = latest_hour.get(candidate, -1) <= normal_latest_hour - 3
            unusually_small = daily_totals[candidate] < normal_total * 0.7
            if not (stopped_early and unusually_small):
                break
            excluded.append(candidate)
            candidate -= dt.timedelta(days=1)

        if not excluded:
            return history
        cutoff = min(excluded) - dt.timedelta(days=1)
        return {
            **history,
            "meta": {
                **history["meta"],
                "last_date": cutoff.isoformat(),
                "original_last_date": history["meta"]["last_date"],
                "excluded_incomplete_dates": [day.isoformat() for day in sorted(excluded)],
            },
            "hourly": [row for row in history["hourly"] if dt.date.fromisoformat(row[0]) <= cutoff],
            "daily": [row for row in history["daily"] if dt.date.fromisoformat(row[0]) <= cutoff],
            "queue_hourly": [row for row in history.get("queue_hourly", []) if dt.date.fromisoformat(row[0]) <= cutoff],
            "interval_10": [row for row in history.get("interval_10", []) if dt.date.fromisoformat(row[0]) <= cutoff],
            "queue_interval_10": [row for row in history.get("queue_interval_10", []) if dt.date.fromisoformat(row[0]) <= cutoff],
            "sla_daily": [row for row in history.get("sla_daily", []) if dt.date.fromisoformat(row[0]) <= cutoff],
            "sla_queue_daily": [row for row in history.get("sla_queue_daily", []) if dt.date.fromisoformat(row[0]) <= cutoff],
        }

    def reload_history(self):
        """Drop in-memory data and rebuild history from the current workload file."""
        self._history = None
        self._queue_rules = None
        raw_history, self._queue_rules = load_or_build_history(
            self.workload_path,
            self.queue_path,
            self.cache_path,
        )
        self._save_history_snapshot(raw_history, self._queue_rules)
        self._history = self._exclude_incomplete_tail(raw_history)
        return self._history

    def metadata(self) -> dict[str, object]:
        history = self._load_history()
        sheets = self.database.schedule_sheets()
        staffing = StaffingConfig.validate(self.database.document("staffing", default_config()))
        positions = staffing["positions"]
        queue_groups = {"shared": [], "specialist": [], "expert": []}
        for rule in (self._queue_rules or {}).values():
            queue_groups[rule.group].append(rule.name)
        return {
            "history": history["meta"],
            "schedule_sheets": sheets,
            "default_schedule_sheet": self._default_schedule_sheet(sheets),
            "capacities": {position["id"]: position["capacity"] for position in positions if position["enabled"]},
            "positions": positions,
            "operation_start_hour": staffing["operation_start_hour"],
            "operation_end_hour": staffing["operation_end_hour"],
            "queue_groups": {group: sorted(names) for group, names in queue_groups.items()},
            "rules": {
                "junior": "Только Метрика: metrika_chats",
                "specialist": "Все очереди подлинии 1",
                "expert": "Подлиния 2; ночные эксперты — все очереди",
            },
        }

    def analyze(self, sheet_name: str, params: ForecastParameters) -> dict[str, object]:
        history = self._load_history()
        staffing = self.get_staffing_config()
        positions = staffing["positions"]
        shifts = self._schedule_shifts(
            sheet_name,
            {str(position["schedule_role"]) for position in positions if position["enabled"]},
        )
        schedule_dates = sorted({dt.date.fromisoformat(shift.date) for shift in shifts})
        first_future_date = dt.date.fromisoformat(history["meta"]["last_date"]) + dt.timedelta(days=1)
        start_date = max(first_future_date, schedule_dates[0]) if schedule_dates else first_future_date
        forecast, trends = build_forecast(history, params, start_date=start_date)
        start_hour = staffing["operation_start_hour"]
        end_hour = staffing["operation_end_hour"]
        forecast = [
            item for item in forecast
            if self._is_operating_hour(int(item["hour"]), start_hour, end_hour)
        ]
        hourly_staff = shifts_to_hourly(shifts, params.include_vacancies, positions)
        evaluated = evaluate_capacity(forecast, hourly_staff, params.reserve_percent, positions)
        summary = summarize(history, forecast, evaluated, trends, positions)
        summary["backtest"] = self._backtest(history, params, start_hour, end_hour)
        summary["queues"] = self._queue_coverage(history, evaluated, params.lookback_weeks)
        real_people = {
            position["name"]: len({shift.login or shift.name for shift in shifts if shift.role == position["schedule_role"] and not shift.is_vacancy})
            for position in positions if position["enabled"]
        }
        sla = self._sla_summary(history, params.lookback_weeks)
        summary["sla"] = sla
        summary["recommendations"] = self._sla_recommendations(sla, positions, real_people)
        vacancies = {
            position["name"]: len({shift.name + shift.role for shift in shifts if shift.role == position["schedule_role"] and shift.is_vacancy})
            for position in positions if position["enabled"]
        }
        daily = self._daily_rows(evaluated)
        heatmap = self._hourly_heatmap(evaluated)
        critical = self._critical_hours(evaluated, positions, params.reserve_percent)
        return {
            "parameters": params.__dict__,
            "schedule": {
                "sheet": sheet_name,
                "start_date": start_date.isoformat() if start_date else None,
                "real_people": real_people,
                "vacancies": vacancies,
                "shift_count": len(shifts),
            },
            "summary": summary,
            "daily": daily,
            "heatmap": heatmap,
            "critical_hours": critical,
        }

    def _load_schedule_overrides(self) -> list[dict[str, object]]:
        if not self.schedule_overrides_path.exists():
            return []
        try:
            with self.schedule_overrides_path.open("r", encoding="utf-8") as source:
                value = json.load(source)
            return value if isinstance(value, list) else []
        except (OSError, ValueError, json.JSONDecodeError):
            return []

    def _load_schedule_staff(self) -> list[dict[str, object]]:
        if not self.schedule_staff_path.exists():
            return []
        try:
            with self.schedule_staff_path.open("r", encoding="utf-8") as source:
                value = json.load(source)
            return value if isinstance(value, list) else []
        except (OSError, ValueError, json.JSONDecodeError):
            return []

    def _schedule_shifts(self, sheet_name: str, roles: set[str] | None = None) -> list[Shift]:
        _, employees, cells = self.database.schedule_rows(sheet_name)
        employees_by_id = {item["id"]: item for item in employees}
        result = []
        for cell in cells:
            employee = employees_by_id.get(cell["employee_id"])
            if not employee or cell["activity"] != "work" or cell["start"] is None or cell["end"] is None:
                continue
            if roles and employee["role"] not in roles:
                continue
            result.append(Shift(
                date=cell["date"], name=employee["name"], login=employee["login"],
                role=employee["role"], start=int(cell["start"]), end=int(cell["end"]),
                is_vacancy=bool(employee["is_vacancy"]), source_sheet=sheet_name,
            ))
        return sorted(result, key=lambda shift: (shift.date, shift.role, shift.name))

    def schedule(self, sheet_name: str) -> dict[str, object]:
        if sheet_name not in self.database.schedule_sheets():
            raise ValueError("Неизвестный лист графика")
        dates, stored_employees, cells = self.database.schedule_rows(sheet_name)
        shifts = self._schedule_shifts(sheet_name)
        if not dates:
            return {"sheet": sheet_name, "dates": [], "employees": [], "coverage": []}
        employee_map = {
            item["id"]: {
                "id": item["id"], "name": item["name"], "login": item["login"],
                "role": item["role"], "is_vacancy": bool(item["is_vacancy"]),
                "schedule_pattern": item.get("schedule_pattern", ""),
                "shifts": {},
                "activities": {},
                "notes": {},
                "hours": 0,
            }
            for item in stored_employees
        }
        for cell in cells:
            employee = employee_map.get(cell["employee_id"])
            if not employee:
                continue
            if cell["activity"] == "work" and cell["start"] is not None and cell["end"] is not None:
                employee["shifts"][cell["date"]] = f"{cell['start']}/{cell['end']}"
                employee["hours"] += (int(cell["end"]) - int(cell["start"])) % 24
            elif cell["activity"] in {"vacation", "sick", "training"}:
                employee["activities"][cell["date"]] = cell["activity"]
            if cell.get("note"):
                employee["notes"][cell["date"]] = cell["note"]
        coverage: dict[tuple[str, int], int] = defaultdict(int)
        for shift in shifts:
            day = dt.date.fromisoformat(shift.date)
            for offset in range((shift.end - shift.start) % 24):
                moment = dt.datetime.combine(day, dt.time()) + dt.timedelta(hours=shift.start + offset)
                coverage[(moment.date().isoformat(), moment.hour)] += 1
        coverage_rows = []
        for date in dates:
            values = [coverage[(date, hour)] for hour in range(24)]
            coverage_rows.append({
                "date": date,
                "hours": values,
                "minimum": min(values),
                "maximum": max(values),
            })
        employees = list(employee_map.values())
        return {
            "sheet": sheet_name,
            "dates": dates,
            "employees": employees,
            "coverage": coverage_rows,
            "summary": {
                "employees": len([item for item in employees if not item["is_vacancy"]]),
                "vacancies": len([item for item in employees if item["is_vacancy"]]),
                "scheduled_hours": sum(int(item["hours"]) for item in employees if not item["is_vacancy"]),
            },
        }

    def vacation_capacity(self, sheet_name: str) -> dict[str, object]:
        if sheet_name not in self.database.schedule_sheets():
            raise ValueError("Неизвестный месяц графика")
        dates_text, employees, cells = self.database.schedule_rows(sheet_name)
        if not dates_text:
            return {"sheet": sheet_name, "dates": [], "roles": [], "rows": [], "work_days": []}
        dates = [dt.date.fromisoformat(value) for value in dates_text]
        first_date, last_date = min(dates), max(dates)
        history = self._load_history()
        staffing = self.get_staffing_config()
        positions = [item for item in staffing["positions"] if item.get("enabled", True)]
        seasonality = self._role_seasonality(history, positions, first_date.month)
        params = ForecastParameters(horizon_days=len(dates), lookback_weeks=12, reserve_percent=15)
        forecast, _ = build_forecast(history, params, start_date=first_date)
        forecast = [
            item for item in forecast
            if first_date <= dt.date.fromisoformat(item["date"]) <= last_date
            and self._is_operating_hour(
                int(item["hour"]), staffing["operation_start_hour"], staffing["operation_end_hour"]
            )
        ]
        shifts = self._schedule_shifts(sheet_name, {str(item["schedule_role"]) for item in positions})
        hourly_staff = shifts_to_hourly(shifts, False, positions)
        evaluated = evaluate_capacity(forecast, hourly_staff, params.reserve_percent, positions)
        evaluated_by_date: dict[str, list[dict[str, object]]] = defaultdict(list)
        for item in evaluated:
            evaluated_by_date[str(item["date"])].append(item)
        employee_by_id = {str(item["id"]): item for item in employees if not item.get("is_vacancy")}
        absences: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        for cell in cells:
            if cell.get("activity") not in {"vacation", "sick", "training"}:
                continue
            employee = employee_by_id.get(str(cell["employee_id"]))
            if not employee:
                continue
            absences[(str(cell["date"]), str(employee["role"]))].append({
                "employee_id": str(employee["id"]), "name": str(employee["name"]),
                "activity": str(cell["activity"]),
            })
        rows = []
        role_meta = []
        for position in positions:
            role = str(position["schedule_role"])
            role_employees = [item for item in employee_by_id.values() if item["role"] == role]
            headcount = len(role_employees)
            if not headcount:
                continue
            seasonal = seasonality[position["id"]]
            ratio = 0.10 if seasonal["level"] == "peak" else 0.20
            limit = math.floor(headcount * ratio)
            role_meta.append({
                "role": role, "name": position["name"], "headcount": headcount,
                "absence_percent": int(ratio * 100), "absence_limit": limit, **seasonal,
            })
            for date in dates_text:
                current_absences = absences.get((date, role), [])
                policy_available = max(0, limit - len(current_absences))
                capacity_available, blocking_hours = self._capacity_safe_absences(
                    evaluated_by_date.get(date, []), position, policy_available,
                    params.reserve_percent, positions,
                )
                vacation_subbotnik = None
                if policy_available > capacity_available and blocking_hours:
                    start, end = self._covering_shift(blocking_hours)
                    vacation_subbotnik = {
                        "role": position["name"], "people": 1,
                        "start": start, "end": end,
                        "shift": f"{start:02d}:00–{end:02d}:00",
                        "critical_hours": blocking_hours,
                        "purpose": "approve_additional_vacation",
                    }
                rows.append({
                    "date": date, "role": role, "position_name": position["name"],
                    "headcount": headcount, "absence_limit": limit,
                    "absent": len(current_absences), "absences": current_absences,
                    "policy_available": policy_available,
                    "available": min(policy_available, capacity_available),
                    "capacity_available": capacity_available,
                    "critical": capacity_available == 0 and policy_available > 0,
                    "vacation_subbotnik": vacation_subbotnik,
                })
        subbotniks = self._subbotnik_recommendations(
            evaluated, positions, params.reserve_percent
        )
        return {
            "sheet": sheet_name, "dates": dates_text, "roles": role_meta, "rows": rows,
            "subbotniks": subbotniks,
            "season_rule": "Пик: нагрузка должности минимум на 15% выше средней за год",
            "absence_types": ["vacation", "sick", "training"],
        }

    @staticmethod
    def _role_seasonality(history, positions, target_month: int) -> dict[str, dict[str, object]]:
        totals: dict[tuple[int, str], float] = defaultdict(float)
        days: dict[int, set[str]] = defaultdict(set)
        for day, group, count in history["daily"]:
            month = dt.date.fromisoformat(day).month
            totals[(month, group)] += float(count)
            days[month].add(day)
        result = {}
        for position in positions:
            groups = set(position.get("groups", [])) | set(position.get("night_groups", []))
            monthly = {
                month: sum(totals[(month, group)] for group in groups) / max(1, len(days[month]))
                for month in days
            }
            baseline = sum(monthly.values()) / max(1, len(monthly))
            value = monthly.get(target_month, baseline)
            index = value / baseline if baseline else 1.0
            level = "peak" if index >= 1.15 else "calm" if index <= 0.85 else "normal"
            result[position["id"]] = {
                "season_level": level, "level": level,
                "season_index": round(index * 100, 1),
                "season_daily_load": round(value, 1),
            }
        return result

    @staticmethod
    def _capacity_safe_absences(day_rows, position, maximum, reserve_percent, positions) -> tuple[int, list[int]]:
        if maximum <= 0 or not day_rows:
            return 0, []
        safe = 0
        blocking_hours = []
        for amount in range(1, maximum + 1):
            valid = True
            current_blocking = []
            for row in day_rows:
                staff = Counter(row.get("staff", {}))
                for mode in ("day", "night"):
                    key = f"{position['id']}:{mode}"
                    staff[key] = max(0, staff[key] - amount)
                simulated = evaluate_capacity([{
                    "date": row["date"], "hour": row["hour"], "demand": row["demand"],
                }], {(row["date"], row["hour"]): staff}, reserve_percent, positions)[0]
                baseline_deficit = sum(float(value) for value in row["deficit"].values())
                simulated_deficit = sum(float(value) for value in simulated["deficit"].values())
                if simulated_deficit > baseline_deficit + 0.01:
                    valid = False
                    current_blocking.append(int(row["hour"]))
            if not valid:
                blocking_hours = sorted(set(current_blocking))
                break
            safe = amount
        return safe, blocking_hours

    @staticmethod
    def _covering_shift(hours: list[int]) -> tuple[int, int]:
        hours = sorted(set(hours))
        if not hours:
            return 0, 0
        gaps = [
            ((hours[(index + 1) % len(hours)] - hours[index]) % 24, index)
            for index in range(len(hours))
        ]
        _, gap_index = max(gaps)
        return hours[(gap_index + 1) % len(hours)], (hours[gap_index] + 1) % 24

    @staticmethod
    def _subbotnik_recommendations(evaluated, positions, reserve_percent):
        critical = ForecastService._critical_hours(evaluated, positions, reserve_percent, None)
        grouped: dict[tuple[str, str], dict[str, object]] = {}
        for hour in critical:
            for staffing in hour["staffing"]:
                key = (hour["date"], staffing["role"])
                item = grouped.setdefault(key, {
                    "date": hour["date"], "role": staffing["role"], "hours": [], "people": 0,
                })
                item["hours"].append(int(hour["hour"]))
                item["people"] = max(int(item["people"]), int(staffing["people"]))
        result = []
        for item in grouped.values():
            hours = sorted(set(item.pop("hours")))
            start, end = ForecastService._covering_shift(hours)
            result.append({
                **item, "start": start, "end": end,
                "shift": f"{start:02d}:00–{end:02d}:00",
                "critical_hours": hours,
            })
        return sorted(result, key=lambda item: (item["date"], item["start"], item["role"]))

    def add_schedule_employee(self, value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            raise ValueError("Некорректные данные сотрудника")
        sheet = str(value.get("sheet") or "")
        if sheet not in self.database.schedule_sheets():
            raise ValueError("Неизвестный месяц графика")
        name = str(value.get("name") or "").strip()[:120]
        login = str(value.get("login") or "").strip().rstrip("@")[:100]
        role = str(value.get("role") or "").strip()[:100]
        schedule_pattern = str(value.get("schedule_pattern") or "").strip()
        if not name or not login or not role:
            raise ValueError("Заполните ФИО, логин и должность")
        current_schedule = self.schedule(sheet)
        if login in {str(item["id"]) for item in current_schedule["employees"]}:
            raise ValueError("Сотрудник с таким логином уже есть в графике")
        import re
        pattern = re.fullmatch(r"(5/2|2/2)\s+(\d{1,2})-(\d{1,2})", schedule_pattern)
        if not pattern:
            raise ValueError("Выберите тип графика")
        cycle, start_text, end_text = pattern.groups()
        start, end = int(start_text), int(end_text)
        if start > 23 or end > 23 or start == end:
            raise ValueError("Некорректное время смены")
        self.database.add_schedule_employee(sheet, login, name, login, role, schedule_pattern)
        dates = [dt.date.fromisoformat(date) for date in current_schedule["dates"]]
        first_date = min(dates) if dates else None
        for date in dates:
            is_working = date.weekday() < 5 if cycle == "5/2" else (date - first_date).days % 4 < 2
            if not is_working:
                continue
            self.database.set_schedule_cell(sheet, login, date.isoformat(), start, end, "work")
        return self.schedule(sheet)

    @staticmethod
    def _russian_holiday(date: dt.date) -> bool:
        """Statutory Russian holidays plus approved transferred days used by current calendars."""
        if (date.month, date.day) in {
            (1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7), (1, 8),
            (2, 23), (3, 8), (5, 1), (5, 9), (6, 12), (11, 4),
        }:
            return True
        transferred_days = {
            2025: {(5, 2), (5, 8), (6, 13), (11, 3), (12, 31)},
            2026: {(1, 9), (12, 31)},
        }
        return (date.month, date.day) in transferred_days.get(date.year, set())

    @staticmethod
    def _month_name(date: dt.date) -> str:
        months = (
            "", "январь", "февраль", "март", "апрель", "май", "июнь",
            "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
        )
        return f"{months[date.month]} {date.year % 100:02d}"

    @classmethod
    def _infer_schedule_pattern(cls, dates: list[dt.date], cells: list[dict]) -> tuple[str, int, int, int | None]:
        work = {
            dt.date.fromisoformat(str(cell["date"])): cell
            for cell in cells
            if cell.get("activity") == "work" and cell.get("start") is not None and cell.get("end") is not None
        }
        if not work:
            return "", 9, 18, None
        shifts: dict[tuple[int, int], int] = defaultdict(int)
        for cell in work.values():
            shifts[(int(cell["start"]), int(cell["end"]))] += 1
        start, end = max(shifts, key=shifts.get)
        observed = set(work)
        five_two = {date for date in dates if date.weekday() < 5 and not cls._russian_holiday(date)}
        five_score = len(observed.symmetric_difference(five_two))
        phases = []
        for phase in range(4):
            expected = {date for date in dates if (date.toordinal() - phase) % 4 < 2}
            phases.append((len(observed.symmetric_difference(expected)), phase))
        two_score, phase = min(phases)
        if five_score <= two_score:
            return f"5/2 {start:02d}-{end:02d}", start, end, None
        return f"2/2 {start:02d}-{end:02d}", start, end, phase

    def create_schedule_month(self, value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            raise ValueError("Некорректные данные нового месяца")
        source = str(value.get("source") or "")
        if source not in self.database.schedule_sheets():
            raise ValueError("Выберите существующий месяц-образец")
        source_dates_text, employees, source_cells = self.database.schedule_rows(source)
        if not source_dates_text:
            raise ValueError("В месяце-образце нет дат")
        source_dates = [dt.date.fromisoformat(date) for date in source_dates_text]
        last = max(source_dates)
        target_first = (last.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        target_name = self._month_name(target_first)
        if target_name in self.database.schedule_sheets():
            raise ValueError(f"График «{target_name}» уже существует")
        target_dates = [
            target_first + dt.timedelta(days=offset)
            for offset in range(calendar.monthrange(target_first.year, target_first.month)[1])
        ]
        cells_by_employee: dict[str, list[dict]] = defaultdict(list)
        for cell in source_cells:
            cells_by_employee[str(cell["employee_id"])].append(cell)
        generated_cells = []
        copied_employees = []
        for employee in employees:
            pattern_text = str(employee.get("schedule_pattern") or "")
            match = re.fullmatch(r"(5/2|2/2)\s+(\d{1,2})-(\d{1,2})", pattern_text)
            inferred, inferred_start, inferred_end, inferred_phase = self._infer_schedule_pattern(
                source_dates, cells_by_employee[str(employee["id"])]
            )
            if match:
                cycle, start_text, end_text = match.groups()
                start, end = int(start_text), int(end_text)
                if cycle == "2/2":
                    observed = {
                        dt.date.fromisoformat(str(cell["date"]))
                        for cell in cells_by_employee[str(employee["id"])]
                        if cell.get("activity") == "work"
                    }
                    phase = min(
                        range(4),
                        key=lambda candidate: len(observed.symmetric_difference({
                            date for date in source_dates
                            if (date.toordinal() - candidate) % 4 < 2
                        })),
                    )
                else:
                    phase = None
            else:
                pattern_text = inferred
                start, end, phase = inferred_start, inferred_end, inferred_phase
                cycle = inferred.split(" ", 1)[0] if inferred else ""
            copied = dict(employee)
            copied["schedule_pattern"] = pattern_text
            copied_employees.append(copied)
            if not cycle:
                continue
            for date in target_dates:
                working = (
                    date.weekday() < 5 and not self._russian_holiday(date)
                    if cycle == "5/2" else (date.toordinal() - int(phase)) % 4 < 2
                )
                if working:
                    generated_cells.append({
                        "employee_id": employee["id"], "date": date.isoformat(),
                        "start": start, "end": end, "activity": "work",
                    })
        self.database.create_schedule_month(
            target_name, [date.isoformat() for date in target_dates], copied_employees, generated_cells
        )
        result = self.schedule(target_name)
        result["created_from"] = source
        result["holidays"] = [date.isoformat() for date in target_dates if self._russian_holiday(date)]
        return result

    def update_schedule(self, value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            raise ValueError("Некорректные данные смены")
        sheet = str(value.get("sheet") or "")
        employee_id = str(value.get("employee_id") or "")
        date = str(value.get("date") or "")
        shift_text = str(value.get("shift") or "").strip()
        activity = str(value.get("activity") or "work").strip()
        note_supplied = "note" in value
        note = str(value.get("note") or "").strip()[:500]
        if activity not in {"work", "day_off", "vacation", "sick", "training"}:
            raise ValueError("Неизвестный тип занятости")
        current = self.schedule(sheet)
        if employee_id not in {str(item["id"]) for item in current["employees"]}:
            raise ValueError("Сотрудник не найден в графике")
        if not note_supplied:
            employee = next(item for item in current["employees"] if str(item["id"]) == employee_id)
            note = str(employee.get("notes", {}).get(date, ""))
        try:
            dt.date.fromisoformat(date)
        except ValueError:
            raise ValueError("Некорректная дата") from None
        if date not in current["dates"]:
            raise ValueError("Дата не относится к выбранному графику")
        start = end = None
        if activity == "work" and shift_text:
            import re
            match = re.fullmatch(r"\s*(\d{1,2})(?::\d{2})?\s*[/\-]\s*(\d{1,2})(?::\d{2})?\s*", shift_text)
            if not match:
                raise ValueError("Используйте формат 8/20 или оставьте поле пустым")
            start, end = int(match.group(1)), int(match.group(2))
            if start > 23 or end > 23 or start == end:
                raise ValueError("Некорректное время смены")
        self.database.set_schedule_cell(
            sheet, employee_id, date,
            start if activity == "work" else None,
            end if activity == "work" else None,
            activity,
            note,
        )
        return self.schedule(sheet)

    def update_schedule_bulk(self, value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            raise ValueError("Некорректные данные периода")
        sheet = str(value.get("sheet") or "")
        employee_id = str(value.get("employee_id") or "")
        try:
            date_from = dt.date.fromisoformat(str(value.get("date_from") or ""))
            date_to = dt.date.fromisoformat(str(value.get("date_to") or ""))
        except ValueError:
            raise ValueError("Укажите корректный период") from None
        if date_to < date_from:
            raise ValueError("Дата окончания раньше даты начала")
        if value.get("activity") == "work" and not str(value.get("shift") or "").strip():
            raise ValueError("Для рабочих дней укажите смену")
        current = self.schedule(sheet)
        if employee_id not in {str(item["id"]) for item in current["employees"]}:
            raise ValueError("Сотрудник не найден в графике")
        available_dates = set(current["dates"])
        selected_dates = []
        date = date_from
        while date <= date_to:
            if date.isoformat() in available_dates:
                selected_dates.append(date.isoformat())
            date += dt.timedelta(days=1)
        if not selected_dates:
            raise ValueError("Выбранный период не относится к этому графику")
        result = current
        for selected_date in selected_dates:
            result = self.update_schedule({
                "sheet": sheet,
                "employee_id": employee_id,
                "date": selected_date,
                "activity": value.get("activity"),
                "shift": value.get("shift"),
            })
        return result

    def reorder_schedule_employees(self, value: object) -> dict[str, object]:
        if not isinstance(value, dict) or not isinstance(value.get("employee_ids"), list):
            raise ValueError("Некорректный порядок сотрудников")
        sheet = str(value.get("sheet") or "")
        current = self.schedule(sheet)
        known = {str(item["id"]) for item in current["employees"]}
        employee_ids = [str(item) for item in value["employee_ids"] if str(item) in known]
        if set(employee_ids) != known:
            raise ValueError("Передан неполный список сотрудников")
        self.database.reorder_schedule_employees(sheet, employee_ids)
        return self.schedule(sheet)

    def delete_schedule_employee(self, value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            raise ValueError("Некорректные данные сотрудника")
        sheet = str(value.get("sheet") or "")
        employee_id = str(value.get("employee_id") or "")
        self.database.delete_schedule_employee(sheet, employee_id)
        return self.schedule(sheet)

    def edit_schedule_employee(self, value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            raise ValueError("Некорректные данные сотрудника")
        sheet = str(value.get("sheet") or "")
        employee_id = str(value.get("employee_id") or "")
        name = str(value.get("name") or "").strip()[:120]
        login = str(value.get("login") or "").strip().rstrip("@")[:100]
        role = str(value.get("role") or "").strip()[:100]
        if not name or not role:
            raise ValueError("Заполните ФИО и должность")
        self.database.update_schedule_employee(
            sheet, employee_id, login or employee_id, name, login, role
        )
        return self.schedule(sheet)

    def hr_services(self) -> list[dict[str, str]]:
        value = self.database.document("hr_services", [{"id": "metrika", "name": "Метрика"}])
        return value if isinstance(value, list) and value else [{"id": "metrika", "name": "Метрика"}]

    def add_hr_service(self, value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            raise ValueError("Некорректные данные сервиса")
        name = str(value.get("name") or "").strip()[:100]
        if not name:
            raise ValueError("Укажите название сервиса")
        services = self.hr_services()
        if any(item["name"].casefold() == name.casefold() for item in services):
            raise ValueError("Такой сервис уже существует")
        import re
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "service"
        service_id = base
        suffix = 2
        used = {item["id"] for item in services}
        while service_id in used:
            service_id = f"{base}-{suffix}"
            suffix += 1
        service = {"id": service_id, "name": name}
        services.append(service)
        self.database.save_document("hr_services", services)
        return service

    def employees(self, service_id: str | None = None) -> list[dict[str, object]]:
        value = self.database.document("employees", [])
        rows = value if isinstance(value, list) else []
        if service_id:
            return [row for row in rows if str(row.get("service_id") or "metrika") == service_id]
        return rows

    def add_employee(self, value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            raise ValueError("Некорректные данные сотрудника")
        fields = (
            "interviewer", "registration_date", "recruiter", "full_name",
            "training_track", "source", "oko_url", "schedule", "subgroup",
            "status", "welcome_book", "start_date", "added_to_chat",
            "hr_ticket", "staff_login", "equipment_ticket", "cargo_ticket",
            "equipment_delivery", "delivery_address", "documents", "comments",
        )
        employee = {field: str(value.get(field) or "").strip()[:4000] for field in fields}
        service_id = str(value.get("service_id") or "metrika")
        if service_id not in {item["id"] for item in self.hr_services()}:
            raise ValueError("Выбран неизвестный сервис")
        employee["service_id"] = service_id
        if not employee["full_name"]:
            raise ValueError("Укажите ФИО сотрудника")
        if not employee["start_date"]:
            raise ValueError("Укажите дату выхода")
        records = self.employees()
        normalized_login = employee["staff_login"].strip().lower().rstrip("@")
        for record in records:
            record_login = str(record.get("staff_login") or "").strip().lower().rstrip("@")
            same_login = normalized_login and record_login == normalized_login
            same_person = (
                str(record.get("full_name") or "").strip().lower() == employee["full_name"].lower()
                and str(record.get("start_date") or "") == employee["start_date"]
            )
            if same_login or same_person:
                raise ValueError("Такой сотрудник уже есть в реестре")
        employee["id"] = f"employee-{int(dt.datetime.now().timestamp() * 1000)}"
        employee["created_at"] = dt.datetime.now().isoformat(timespec="seconds")
        records.insert(0, employee)
        self.database.save_document("employees", records)
        return employee

    def hr_records(self, section: str, service_id: str | None = None) -> list[dict[str, object]]:
        value = self.database.document("hr_records", {})
        rows = value.get(section, []) if isinstance(value, dict) else []
        if not isinstance(rows, list):
            return []
        if service_id:
            return [row for row in rows if str(row.get("service_id") or "metrika") == service_id]
        return rows

    def add_hr_record(self, section: str, value: object, service_id: str = "metrika") -> dict[str, object]:
        allowed = {"portrait", "interview_guide", "demand", "interviews", "contacts", "vacancies"}
        if section not in allowed or not isinstance(value, dict):
            raise ValueError("Неизвестный раздел документа")
        if service_id not in {item["id"] for item in self.hr_services()}:
            raise ValueError("Выбран неизвестный сервис")
        clean = {
            str(key)[:80]: str(raw or "").strip()[:10000]
            for key, raw in value.items()
            if str(key) not in {"id", "created_at"}
        }
        if not any(clean.values()):
            raise ValueError("Заполните хотя бы одно поле")
        record = {
            **clean,
            "service_id": service_id,
            "id": f"{section}-{int(dt.datetime.now().timestamp() * 1000)}",
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        }
        loaded = self.database.document("hr_records", {})
        data: dict[str, object] = loaded if isinstance(loaded, dict) else {}
        rows = data.get(section, [])
        if not isinstance(rows, list):
            rows = []
        rows.insert(0, record)
        data[section] = rows
        self.database.save_document("hr_records", data)
        return record

    def get_staffing_config(self) -> dict[str, object]:
        return StaffingConfig.validate(self.database.document("staffing", default_config()))

    def update_staffing_config(self, value: object) -> dict[str, object]:
        config = StaffingConfig.validate(value)
        return self.database.save_document("staffing", config)

    @staticmethod
    def _sla_summary(history: dict[str, object], lookback_weeks: int) -> dict[str, object]:
        end = dt.date.fromisoformat(history["meta"]["last_date"])
        start = end - dt.timedelta(weeks=lookback_weeks) + dt.timedelta(days=1)
        values: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for day, group, metric, count in history.get("sla_daily", []):
            if start <= dt.date.fromisoformat(day) <= end:
                values[group][metric] += float(count)
        result = {}
        overall_total = 0.0
        overall_within = 0.0
        for group in ("shared", "specialist", "expert"):
            total = values[group]["total"]
            within = values[group]["within"]
            overall_total += total
            overall_within += within
            rate = within / total * 100 if total else 0.0
            result[group] = {
                "target_percent": 90.0,
                "threshold_minutes": 40 if group == "expert" else 30,
                "actual_percent": round(rate, 1),
                "sample_size": int(total),
                "meets_target": rate >= 90,
            }
        overall_rate = overall_within / overall_total * 100 if overall_total else 0.0
        return {
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "target_percent": 90.0,
            "actual_percent": round(overall_rate, 1),
            "sample_size": int(overall_total),
            "meets_target": overall_rate >= 90,
            "groups": result,
        }

    @staticmethod
    def _sla_recommendations(
        sla: dict[str, object],
        positions: list[dict[str, object]],
        real_people: dict[str, int],
    ) -> list[dict[str, object]]:
        enabled = [position for position in positions if position.get("enabled", True)]
        result = []
        for group, metric in sla["groups"].items():
            actual = float(metric["actual_percent"])
            if metric["meets_target"] or not metric["sample_size"]:
                continue
            candidates = [position for position in enabled if group in position.get("groups", [])]
            if not candidates:
                continue
            candidates.sort(key=lambda position: len(position.get("groups", [])))
            position = candidates[0]
            current_people = max(1, int(real_people.get(position["name"], 0)))
            people = max(1, math.ceil(current_people * (90 / max(actual, 1) - 1)))
            result.append({
                "role": position["name"],
                "people": people,
                "shift": f"для достижения SLA {int(metric['threshold_minutes'])} мин",
                "sla_current": actual,
                "sla_target": 90.0,
                "group": group,
            })
        return result

    def _queue_coverage(
        self,
        history: dict[str, object],
        evaluated: list[dict[str, object]],
        lookback_weeks: int,
    ) -> list[dict[str, object]]:
        """Split group demand and deficit across real queues by their historical hourly shares."""
        last_date = dt.date.fromisoformat(history["meta"]["last_date"])
        lookback_start = last_date - dt.timedelta(weeks=lookback_weeks) + dt.timedelta(days=1)
        queue_groups = {name: rule.group for name, rule in (self._queue_rules or {}).items()}
        profiles: dict[tuple[int, int, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
        fallback: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        queue_sla: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for day, queue, metric, count in history.get("sla_queue_daily", []):
            if dt.date.fromisoformat(day) >= lookback_start:
                queue_sla[queue][metric] += float(count)
        for day, hour, queue, count in history.get("queue_hourly", []):
            current = dt.date.fromisoformat(day)
            if current < lookback_start or queue not in queue_groups:
                continue
            group = queue_groups[queue]
            profiles[(current.weekday(), int(hour), group)][queue] += float(count)
            fallback[group][queue] += float(count)

        stats: dict[str, dict[str, float]] = {
            queue: defaultdict(float, group=group)
            for queue, group in queue_groups.items()
        }
        hourly_stats: dict[tuple[str, int], dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for row in evaluated:
            row["queue_risk"] = []
        for row in evaluated:
            weekday = dt.date.fromisoformat(row["date"]).weekday()
            hour = int(row["hour"])
            for group, group_demand in row["demand"].items():
                if group_demand <= 0:
                    continue
                weights = profiles.get((weekday, hour, group)) or fallback.get(group, {})
                weight_total = sum(weights.values())
                if not weight_total:
                    continue
                deficit_ratio = min(1.0, float(row["deficit"][group]) / float(group_demand))
                for queue, weight in weights.items():
                    demand = float(group_demand) * weight / weight_total
                    deficit = demand * deficit_ratio
                    stats[queue]["demand"] += demand
                    stats[queue]["deficit"] += deficit
                    hourly_stats[(queue, hour)]["demand"] += demand
                    hourly_stats[(queue, hour)]["deficit"] += deficit
                    hourly_stats[(queue, hour)]["samples"] += 1
                    if deficit > 0:
                        stats[queue]["uncovered_hours"] += 1
                        row["queue_risk"].append({
                            "name": queue,
                            "demand": round(demand, 2),
                            "deficit": round(deficit, 2),
                            "coverage_percent": round((1 - deficit / demand) * 100, 1) if demand else 100.0,
                        })

        result = []
        forecast_days = max(1, len({row["date"] for row in evaluated}))
        for queue, values in stats.items():
            demand = values["demand"]
            deficit = values["deficit"]
            sla_total = queue_sla[queue]["total"]
            sla_within = queue_sla[queue]["within"]
            sla_percent = sla_within / sla_total * 100 if sla_total else 0.0
            result.append({
                "name": queue,
                "group": values["group"],
                "demand": round(demand, 1),
                "deficit": round(deficit, 1),
                "coverage_percent": round(sla_percent, 1),
                "capacity_coverage_percent": round((1 - deficit / demand) * 100, 1) if demand else 100.0,
                "sla_target_percent": 90.0,
                "sla_threshold_minutes": 40 if values["group"] == "expert" else 30,
                "sla_sample_size": int(sla_total),
                "outside_sla": int(sla_total - sla_within),
                "uncovered_hours": int(values["uncovered_hours"]),
                "hourly": [
                    {
                        "hour": hour,
                        "demand": round(hourly_stats[(queue, hour)]["demand"] / forecast_days, 2),
                        "deficit": round(hourly_stats[(queue, hour)]["deficit"] / forecast_days, 2),
                    }
                    for hour in sorted(
                        current_hour for current_queue, current_hour in hourly_stats
                        if current_queue == queue
                    )
                ],
            })
        return sorted(result, key=lambda item: (item["coverage_percent"], -item["demand"], item["name"]))

    @staticmethod
    def _critical_hours(
        evaluated: list[dict[str, object]],
        positions: list[dict[str, object]],
        reserve_percent: float,
        max_rows: int | None = 24,
    ) -> list[dict[str, object]]:
        utilization = max(0.05, 1 - reserve_percent / 100)
        enabled = [position for position in positions if position.get("enabled", True)]
        hourly: dict[tuple[str, int], dict[str, object]] = {}
        for row in evaluated:
            key = (row["date"], int(row["hour"]))
            item = hourly.setdefault(key, {
                "date": row["date"], "hour": int(row["hour"]),
                "demand": defaultdict(float), "deficit": defaultdict(float), "queues": defaultdict(lambda: defaultdict(float)),
            })
            for group, value in row["demand"].items():
                item["demand"][group] += value
                item["deficit"][group] += row["deficit"][group]
            for queue in row.get("queue_risk", []):
                item["queues"][queue["name"]]["demand"] += queue["demand"]
                item["queues"][queue["name"]]["deficit"] += queue["deficit"]
        result = []
        ranked = sorted(hourly.values(), key=lambda item: sum(item["deficit"].values()), reverse=True)
        if max_rows is not None:
            ranked = ranked[:max_rows]
        for row in ranked:
            demand = sum(row["demand"].values())
            deficit = sum(row["deficit"].values())
            required: dict[str, float] = defaultdict(float)
            is_night = int(row["hour"]) >= 23 or int(row["hour"]) < 6
            skills_key = "night_groups" if is_night else "groups"
            for group, group_deficit in row["deficit"].items():
                if group_deficit <= 0:
                    continue
                candidates = [position for position in enabled if group in position.get(skills_key, [])]
                if not candidates:
                    continue
                position = candidates[0]
                required[position["name"]] += group_deficit / (float(position["capacity"]) * utilization)
            queues = sorted(({
                "name": name,
                "demand": round(values["demand"], 2),
                "deficit": round(values["deficit"], 2),
                "coverage_percent": round((1 - values["deficit"] / values["demand"]) * 100, 1) if values["demand"] else 100.0,
            } for name, values in row["queues"].items()), key=lambda item: item["deficit"], reverse=True)
            result.append({
                "date": row["date"],
                "hour": row["hour"],
                "demand": round(demand, 1),
                "deficit": round(deficit, 1),
                "coverage_percent": round((1 - deficit / demand) * 100, 1) if demand else 100.0,
                "queues": queues,
                "staffing": [
                    {"role": role, "people": math.ceil(people)}
                    for role, people in required.items()
                ],
            })
        return result

    def _backtest(
        self,
        history: dict[str, object],
        params: ForecastParameters,
        start_hour: int,
        end_hour: int,
    ) -> dict[str, object]:
        """Run up to six non-overlapping holdout windows and aggregate their errors."""
        latest_end = dt.date.fromisoformat(history["meta"]["last_date"])
        earliest = dt.date.fromisoformat(history["meta"]["first_date"])
        windows = []
        for offset in range(6):
            window_end = latest_end - dt.timedelta(days=28 * offset)
            validation_start = window_end - dt.timedelta(days=27)
            training_days = (validation_start - earliest).days
            if training_days < max(56, params.lookback_weeks * 7):
                break
            windows.append(self._backtest_window(history, params, start_hour, end_hour, window_end))

        totals = defaultdict(float)
        groups: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for window in windows:
            for key, value in window.pop("_raw").items():
                totals[key] += value
            for group, values in window.pop("_group_raw").items():
                for key, value in values.items():
                    groups[group][key] += value

        def metrics(values: dict[str, float]) -> dict[str, float]:
            actual = values["actual"]
            result = {
                "actual": round(actual, 1),
                "predicted": round(values["predicted"], 1),
                "wape_percent": round(values["absolute_error"] / actual * 100, 1) if actual else 0.0,
                "mae": round(values["absolute_error"] / max(1, values["observations"]), 2),
                "bias_percent": round((values["predicted"] / actual - 1) * 100, 1) if actual else 0.0,
            }
            if "daily_absolute_error" in values:
                result["daily_wape_percent"] = round(values["daily_absolute_error"] / actual * 100, 1) if actual else 0.0
                result["hourly_distribution_error_percent"] = round(values["distribution_error_weighted"] / actual * 100, 1) if actual else 0.0
            return result

        return {
            "window_count": len(windows),
            "period_start": windows[-1]["period_start"] if windows else None,
            "period_end": windows[0]["period_end"] if windows else None,
            "days": sum(window["days"] for window in windows),
            "overall": metrics(totals),
            "groups": {group: metrics(values) for group, values in groups.items()},
            "windows": windows,
        }

    def _backtest_window(
        self,
        history: dict[str, object],
        params: ForecastParameters,
        start_hour: int,
        end_hour: int,
        history_end: dt.date,
    ) -> dict[str, object]:
        """Forecast one known 28-day window using only data available before it."""
        history_start = dt.date.fromisoformat(history["meta"]["first_date"])
        available_days = (history_end - history_start).days + 1
        validation_days = min(28, max(7, available_days // 4))
        validation_start = history_end - dt.timedelta(days=validation_days - 1)
        training_end = validation_start - dt.timedelta(days=1)

        training = {
            "meta": {**history["meta"], "last_date": training_end.isoformat()},
            "hourly": [row for row in history["hourly"] if dt.date.fromisoformat(row[0]) <= training_end],
            "daily": [row for row in history["daily"] if dt.date.fromisoformat(row[0]) <= training_end],
        }
        validation_params = replace(
            params,
            horizon_days=validation_days,
            shared_growth=0.0,
            specialist_growth=0.0,
            expert_growth=0.0,
        )
        predicted, _trends = build_forecast(training, validation_params, start_date=validation_start)
        predicted = [
            row for row in predicted
            if self._is_operating_hour(int(row["hour"]), start_hour, end_hour)
        ]
        actual = {
            (day, int(hour), group): float(count)
            for day, hour, group, count in history["hourly"]
            if validation_start <= dt.date.fromisoformat(day) <= history_end
        }

        totals = defaultdict(float)
        group_errors: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        daily_values: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        hourly_values: dict[tuple[str, int], dict[str, float]] = defaultdict(lambda: defaultdict(float))
        observations = 0
        for row in predicted:
            for group, predicted_value in row["demand"].items():
                actual_value = actual.get((row["date"], int(row["hour"]), group), 0.0)
                error = float(predicted_value) - actual_value
                totals["actual"] += actual_value
                totals["predicted"] += float(predicted_value)
                totals["absolute_error"] += abs(error)
                group_errors[group]["actual"] += actual_value
                group_errors[group]["predicted"] += float(predicted_value)
                group_errors[group]["absolute_error"] += abs(error)
                daily_values[row["date"]]["actual"] += actual_value
                daily_values[row["date"]]["predicted"] += float(predicted_value)
                hourly_values[(row["date"], int(row["hour"]))]["actual"] += actual_value
                hourly_values[(row["date"], int(row["hour"]))]["predicted"] += float(predicted_value)
                observations += 1

        totals["daily_absolute_error"] = sum(
            abs(values["predicted"] - values["actual"])
            for values in daily_values.values()
        )
        distribution_error_weighted = 0.0
        for day, day_values in daily_values.items():
            actual_total = day_values["actual"]
            predicted_total = day_values["predicted"]
            if not actual_total or not predicted_total:
                continue
            share_distance = sum(
                abs(
                    hourly_values[(day, hour)]["actual"] / actual_total
                    - hourly_values[(day, hour)]["predicted"] / predicted_total
                )
                for hour in range(24)
            ) / 2
            distribution_error_weighted += share_distance * actual_total
        totals["distribution_error_weighted"] = distribution_error_weighted

        def metrics(values: dict[str, float], count: int) -> dict[str, float]:
            actual_total = values["actual"]
            return {
                "actual": round(actual_total, 1),
                "predicted": round(values["predicted"], 1),
                "wape_percent": round(values["absolute_error"] / actual_total * 100, 1) if actual_total else 0.0,
                "mae": round(values["absolute_error"] / max(1, count), 2),
                "bias_percent": round((values["predicted"] / actual_total - 1) * 100, 1) if actual_total else 0.0,
                **({
                    "daily_wape_percent": round(values["daily_absolute_error"] / actual_total * 100, 1) if actual_total else 0.0,
                    "hourly_distribution_error_percent": round(values["distribution_error_weighted"] / actual_total * 100, 1) if actual_total else 0.0,
                } if "daily_absolute_error" in values else {}),
            }

        hours_count = max(1, len(predicted))
        raw_totals = {**totals, "observations": observations}
        raw_groups = {
            group: {**values, "observations": hours_count}
            for group, values in group_errors.items()
        }
        return {
            "period_start": validation_start.isoformat(),
            "period_end": history_end.isoformat(),
            "training_end": training_end.isoformat(),
            "days": validation_days,
            "overall": metrics(totals, observations),
            "groups": {group: metrics(values, hours_count) for group, values in group_errors.items()},
            "_raw": raw_totals,
            "_group_raw": raw_groups,
        }

    @staticmethod
    def _is_operating_hour(hour: int, start_hour: int, end_hour: int) -> bool:
        """Equal boundaries mean 24/7; reversed boundaries describe a shift through midnight."""
        if start_hour == end_hour:
            return True
        if start_hour < end_hour:
            return start_hour <= hour < end_hour
        return hour >= start_hour or hour < end_hour

    @staticmethod
    def _daily_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
        result: dict[str, dict[str, object]] = {}
        for row in rows:
            item = result.setdefault(row["date"], {
                "date": row["date"],
                "demand": {"shared": 0.0, "specialist": 0.0, "expert": 0.0},
                "deficit": {"shared": 0.0, "specialist": 0.0, "expert": 0.0},
            })
            for group in item["demand"]:
                item["demand"][group] += row["demand"][group]
                item["deficit"][group] += row["deficit"][group]
        for item in result.values():
            for field in ("demand", "deficit"):
                item[field] = {key: round(value, 1) for key, value in item[field].items()}
        return list(result.values())

    @staticmethod
    def _hourly_heatmap(rows: list[dict[str, object]]) -> list[dict[str, object]]:
        by_day_hour: dict[tuple[str, int], dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for row in rows:
            values = by_day_hour[(row["date"], int(row["hour"]))]
            values["demand"] += sum(row["demand"].values())
            values["deficit"] += sum(row["deficit"].values())
        accumulator: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for (_day, hour), values in by_day_hour.items():
            accumulator[hour]["demand"] += values["demand"]
            accumulator[hour]["deficit"] += values["deficit"]
            accumulator[hour]["samples"] += 1
        return [
            {
                "hour": hour,
                "demand": round(values["demand"] / max(1, values["samples"]), 1),
                "deficit": round(values["deficit"] / max(1, values["samples"]), 1),
            }
            for hour, values in accumulator.items()
        ]

    @staticmethod
    def _default_schedule_sheet(sheets: list[str]) -> str | None:
        month_names = {
            1: "январь", 2: "февраль", 3: "март", 4: "апрель",
            5: "май", 6: "июнь", 7: "июль", 8: "август",
            9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь",
        }
        today = dt.date.today()
        desired = f"{month_names[today.month]} {str(today.year)[-2:]}"
        return desired if desired in sheets else (sheets[-1] if sheets else None)
