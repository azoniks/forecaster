from __future__ import annotations

import csv
import datetime as dt
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .config import default_config
from .xlsx import XlsxBook, excel_date


MOSCOW_OFFSET = dt.timedelta(hours=3)
TARGET_ROLES = {"Младший специалист", "Специалист", "Эксперт"}
SHIFT_RE = re.compile(r"^\s*(\d{1,2})(?::\d{2})?\s*[/\-]\s*(\d{1,2})(?::\d{2})?\s*$")


@dataclass(frozen=True)
class QueueRule:
    name: str
    subline: int
    active_from: dt.date | None
    group: str


@dataclass(frozen=True)
class Shift:
    date: str
    name: str
    login: str
    role: str
    start: int
    end: int
    is_vacancy: bool
    source_sheet: str

    @property
    def overnight(self) -> bool:
        return self.end <= self.start


def load_queue_rules(path: Path) -> dict[str, QueueRule]:
    rows = XlsxBook(path).rows("Chart data")
    if not rows:
        return {}
    headers = {str(value).strip(): index for index, value in enumerate(rows[0]) if value}
    rules: dict[str, QueueRule] = {}
    for row in rows[1:]:
        def get(name: str, default: object = "") -> object:
            index = headers.get(name)
            return row[index] if index is not None and index < len(row) else default

        name = str(get("Очередь") or "").strip()
        technical = str(get("Техническая очередь") or "0") == "1"
        archived = str(get("Архивная очередь") or "0") == "1"
        enabled = str(get("Учитываем в прогнозе") or "0") == "1"
        if not name or technical or archived or not enabled:
            continue
        subline = int(float(str(get("Подлиния") or 0)))
        group = "shared" if name == "Метрика: metrika_chats" else (
            "specialist" if subline == 1 else "expert"
        )
        rules[name] = QueueRule(
            name=name,
            subline=subline,
            active_from=excel_date(get("Данные актуальны с")),
            group=group,
        )
    return rules


def _parse_timestamp(value: str) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.timezone(MOSCOW_OFFSET)).replace(tzinfo=None)
    return parsed


def build_history(
    workload_path: Path,
    queue_rules: dict[str, QueueRule],
) -> dict[str, object]:
    hourly: Counter[tuple[str, int, str]] = Counter()
    queue_hourly: Counter[tuple[str, int, str]] = Counter()
    interval_10: Counter[tuple[str, int, str]] = Counter()
    queue_interval_10: Counter[tuple[str, int, str]] = Counter()
    daily: Counter[tuple[str, str]] = Counter()
    queue_counts: Counter[str] = Counter()
    channel_counts: Counter[str] = Counter()
    sla_daily: Counter[tuple[str, str, str]] = Counter()
    sla_queue_daily: Counter[tuple[str, str, str]] = Counter()
    sla_queue_minutes: Counter[tuple[str, str, float]] = Counter()
    first_date: dt.date | None = None
    last_date: dt.date | None = None
    included = 0
    excluded = 0

    with workload_path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, delimiter="\t")
        for row in reader:
            queue_name = (row.get("Очередь") or "").strip()
            rule = queue_rules.get(queue_name)
            if rule is None:
                excluded += 1
                continue
            timestamp = _parse_timestamp(row.get("Начало периода") or "")
            if timestamp is None:
                excluded += 1
                continue
            day = timestamp.date()
            if rule.active_from and day < rule.active_from:
                excluded += 1
                continue
            key_date = day.isoformat()
            hourly[(key_date, timestamp.hour, rule.group)] += 1
            queue_hourly[(key_date, timestamp.hour, queue_name)] += 1
            interval = timestamp.hour * 6 + timestamp.minute // 10
            interval_10[(key_date, interval, rule.group)] += 1
            queue_interval_10[(key_date, interval, queue_name)] += 1
            daily[(key_date, rule.group)] += 1
            queue_counts[queue_name] += 1
            channel_counts[(row.get("Канал") or "Не указан").strip()] += 1
            try:
                assignment_minutes = float((row.get("Ср время назначения менеджера (мин)") or "").strip())
            except ValueError:
                assignment_minutes = None
            if assignment_minutes is not None:
                sla_queue_minutes[(key_date, queue_name, round(assignment_minutes, 3))] += 1
                sla_daily[(key_date, rule.group, "total")] += 1
                sla_queue_daily[(key_date, queue_name, "total")] += 1
                threshold = 40 if rule.group == "expert" else 30
                if assignment_minutes <= threshold:
                    sla_daily[(key_date, rule.group, "within")] += 1
                    sla_queue_daily[(key_date, queue_name, "within")] += 1
            first_date = day if first_date is None or day < first_date else first_date
            last_date = day if last_date is None or day > last_date else last_date
            included += 1

    return {
        "hourly": [
            [day, hour, group, count]
            for (day, hour, group), count in sorted(hourly.items())
        ],
        "daily": [
            [day, group, count]
            for (day, group), count in sorted(daily.items())
        ],
        "queue_hourly": [
            [day, hour, queue, count]
            for (day, hour, queue), count in sorted(queue_hourly.items())
        ],
        "interval_10": [
            [day, interval, group, count]
            for (day, interval, group), count in sorted(interval_10.items())
        ],
        "queue_interval_10": [
            [day, interval, queue, count]
            for (day, interval, queue), count in sorted(queue_interval_10.items())
        ],
        "meta": {
            "first_date": first_date.isoformat() if first_date else None,
            "last_date": last_date.isoformat() if last_date else None,
            "included_periods": included,
            "excluded_periods": excluded,
            "queues": dict(queue_counts),
            "channels": dict(channel_counts),
        },
        "sla_daily": [
            [day, group, metric, count]
            for (day, group, metric), count in sorted(sla_daily.items())
        ],
        "sla_queue_daily": [
            [day, queue, metric, count]
            for (day, queue, metric), count in sorted(sla_queue_daily.items())
        ],
        "sla_queue_minutes": [
            [day, queue, minutes, count]
            for (day, queue, minutes), count in sorted(sla_queue_minutes.items())
        ],
    }


def load_or_build_history(
    workload_path: Path,
    queue_path: Path,
    cache_path: Path,
) -> tuple[dict[str, object], dict[str, QueueRule]]:
    rules = load_queue_rules(queue_path)
    signature = {
        "schema_version": 8,
        "workload_mtime": workload_path.stat().st_mtime_ns,
        "workload_size": workload_path.stat().st_size,
        "queues_mtime": queue_path.stat().st_mtime_ns,
        "queues_size": queue_path.stat().st_size,
    }
    if cache_path.exists():
        with cache_path.open("r", encoding="utf-8") as source:
            cached = json.load(source)
        if cached.get("signature") == signature:
            return cached["history"], rules
    history = build_history(workload_path, rules)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("w", encoding="utf-8") as target:
        json.dump({"signature": signature, "history": history}, target, ensure_ascii=False)
    return history, rules


def available_schedule_sheets(path: Path) -> list[str]:
    result = []
    for name in XlsxBook(path).sheet_names():
        if re.fullmatch(r"[а-яё]+\s+\d{2}", name.lower()):
            result.append(name)
    return result


def _parse_shift(value: object) -> tuple[int, int] | None:
    if value is None:
        return None
    match = SHIFT_RE.match(str(value))
    if not match:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    if start > 23 or end > 23 or start == end:
        return None
    return start, end


def _parse_activity(value: object, fill: str | None = None) -> str | None:
    text = str(value or "").strip().lower()
    if fill in {"FFFF0000", "FFE06666", "FF980000"}:
        if "`" in text or "отп" in text:
            return "vacation"
        return "sick"
    if fill in {"FFA9D08E", "FF92D050", "FFB6D7A8", "FF6AA84F", "FF93C47D", "FF00FF00"} and text and _parse_shift(value) is None:
        return "training"
    if not text:
        return None
    if "`" in text or "отп" in text:
        return "vacation"
    if text in {"бл", "бол"} or "больнич" in text:
        return "sick"
    return None


def load_schedule_details(path: Path, sheet_name: str, target_roles: set[str] | None = None) -> dict[str, object]:
    styled_rows = XlsxBook(path).rows_with_fills(sheet_name)
    rows = [row for row, _ in styled_rows]
    roles = target_roles or TARGET_ROLES
    employees: dict[str, dict[str, str]] = {}
    activities: dict[tuple[str, str], str] = {}
    current_dates: dict[int, dt.date] = {}
    for row_index, (row, fills) in enumerate(styled_rows):
        if row_index + 1 < len(rows):
            next_row = rows[row_index + 1]
            marker = str(next_row[1]).strip() if len(next_row) > 1 and next_row[1] else ""
            if marker in {"Фамилия имя", "Эксперты"}:
                current_dates = {
                    index: date
                    for index, value in enumerate(row)
                    if index >= 6 and (date := excel_date(value)) is not None
                }
        role = str(row[3]).strip() if len(row) > 3 and row[3] else ""
        name = str(row[1]).strip() if len(row) > 1 and row[1] else ""
        if role not in roles or not name:
            continue
        login = str(row[2]).strip() if len(row) > 2 and row[2] else ""
        employee_id = login or name
        employees[employee_id] = {"id": employee_id, "name": name, "login": login, "role": role}
        for column, date in current_dates.items():
            value = row[column] if column < len(row) else None
            fill = fills[column] if column < len(fills) else None
            if activity := _parse_activity(value, fill):
                activities[(employee_id, date.isoformat())] = activity
    return {"employees": employees, "activities": activities}


def load_schedule(path: Path, sheet_name: str, target_roles: set[str] | None = None) -> list[Shift]:
    rows = XlsxBook(path).rows(sheet_name)
    roles = target_roles or TARGET_ROLES
    shifts: list[Shift] = []
    current_dates: dict[int, dt.date] = {}
    for row_index, row in enumerate(rows):
        if row_index + 1 < len(rows):
            next_row = rows[row_index + 1]
            marker = str(next_row[1]).strip() if len(next_row) > 1 and next_row[1] else ""
            if marker in {"Фамилия имя", "Эксперты"}:
                current_dates = {
                    index: date
                    for index, value in enumerate(row)
                    if index >= 6 and (date := excel_date(value)) is not None
                }

        role = str(row[3]).strip() if len(row) > 3 and row[3] else ""
        name = str(row[1]).strip() if len(row) > 1 and row[1] else ""
        if role not in roles or not name:
            continue
        login = str(row[2]).strip() if len(row) > 2 and row[2] else ""
        for column, date in current_dates.items():
            value = row[column] if column < len(row) else None
            parsed = _parse_shift(value)
            if parsed is None:
                continue
            shifts.append(Shift(
                date=date.isoformat(),
                name=name,
                login=login,
                role=role,
                start=parsed[0],
                end=parsed[1],
                is_vacancy=name.lower().startswith("вакансия"),
                source_sheet=sheet_name,
            ))
    return shifts


def shifts_to_hourly(
    shifts: Iterable[Shift],
    include_vacancies: bool,
    positions: list[dict[str, object]] | None = None,
) -> dict[tuple[str, int], Counter[str]]:
    result: dict[tuple[str, int], Counter[str]] = defaultdict(Counter)
    configured = positions or default_config()["positions"]
    position_by_role = {
        str(position["schedule_role"]): position
        for position in configured if position.get("enabled", True)
    }
    for shift in shifts:
        if shift.is_vacancy and not include_vacancies:
            continue
        start_date = dt.date.fromisoformat(shift.date)
        hours = list(range(shift.start, 24)) + list(range(0, shift.end)) if shift.overnight else list(range(shift.start, shift.end))
        for hour in hours:
            day = start_date + dt.timedelta(days=1 if shift.overnight and hour < shift.end else 0)
            key = (day.isoformat(), hour)
            position = position_by_role.get(shift.role)
            if position is None:
                continue
            role_key = f"{position['id']}:{'night' if shift.overnight else 'day'}"
            result[key][role_key] += 1
    return result
