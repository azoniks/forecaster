from __future__ import annotations

import datetime as dt
import math
from collections import Counter, defaultdict
from dataclasses import dataclass

from .config import default_config

GROUPS = ("shared", "specialist", "expert")
GROUP_LABELS = {
    "shared": "Общая очередь",
    "specialist": "Очереди специалистов",
    "expert": "Очереди экспертов",
}


@dataclass
class ForecastParameters:
    horizon_days: int = 28
    lookback_weeks: int = 8
    reserve_percent: float = 15.0
    shared_growth: float = 0.0
    specialist_growth: float = 0.0
    expert_growth: float = 0.0
    include_vacancies: bool = False

    def growth(self, group: str) -> float:
        return {
            "shared": self.shared_growth,
            "specialist": self.specialist_growth,
            "expert": self.expert_growth,
        }[group]


def _history_map(history: dict[str, object]) -> dict[tuple[dt.date, int, str], int]:
    return {
        (dt.date.fromisoformat(day), int(hour), group): int(count)
        for day, hour, group, count in history["hourly"]
    }


def _trend_factors(
    hourly: dict[tuple[dt.date, int, str], int],
    end_date: dt.date,
) -> dict[str, float]:
    factors: dict[str, float] = {}
    for group in GROUPS:
        recent = sum(
            count for (day, _hour, current_group), count in hourly.items()
            if current_group == group and end_date - dt.timedelta(days=27) <= day <= end_date
        )
        previous = sum(
            count for (day, _hour, current_group), count in hourly.items()
            if current_group == group and end_date - dt.timedelta(days=55) <= day <= end_date - dt.timedelta(days=28)
        )
        raw = recent / previous if previous else 1.0
        factors[group] = min(1.30, max(0.70, raw))
    return factors


def build_forecast(
    history: dict[str, object],
    params: ForecastParameters,
    start_date: dt.date | None = None,
) -> tuple[list[dict[str, object]], dict[str, float]]:
    hourly = _history_map(history)
    last_date = dt.date.fromisoformat(history["meta"]["last_date"])
    start = start_date or (last_date + dt.timedelta(days=1))
    lookback_start = last_date - dt.timedelta(weeks=params.lookback_weeks) + dt.timedelta(days=1)
    # Rolling backtests showed that extrapolating a four-week jump makes the
    # forecast oscillate. Keep the stable seasonal profile; scenario growth is
    # still applied explicitly by the user.
    trends = {group: 1.0 for group in GROUPS}

    profiles: dict[tuple[int, int, str], list[int]] = defaultdict(list)
    day = lookback_start
    while day <= last_date:
        for hour in range(24):
            for group in GROUPS:
                profiles[(day.weekday(), hour, group)].append(hourly.get((day, hour, group), 0))
        day += dt.timedelta(days=1)

    result: list[dict[str, object]] = []
    for day_offset in range(params.horizon_days):
        forecast_day = start + dt.timedelta(days=day_offset)
        for hour in range(24):
            demand = {}
            for group in GROUPS:
                values = profiles.get((forecast_day.weekday(), hour, group), [0])
                base = sum(values) / len(values)
                multiplier = trends[group] * (1 + params.growth(group) / 100)
                demand[group] = round(base * multiplier, 2)
            result.append({
                "date": forecast_day.isoformat(),
                "hour": hour,
                "demand": demand,
            })
    return result, trends


def build_interval_forecast(
    history: dict[str, object],
    params: ForecastParameters,
    start_date: dt.date | None = None,
) -> tuple[list[dict[str, object]], dict[str, float]]:
    """Build the same seasonal forecast at 10-minute resolution for SLA simulation."""
    last_date = dt.date.fromisoformat(history["meta"]["last_date"])
    start = start_date or (last_date + dt.timedelta(days=1))
    lookback_start = last_date - dt.timedelta(weeks=params.lookback_weeks) + dt.timedelta(days=1)
    source = {
        (dt.date.fromisoformat(day), int(interval), group): int(count)
        for day, interval, group, count in history.get("interval_10", [])
    }
    profiles: dict[tuple[int, int, str], list[int]] = defaultdict(list)
    day = lookback_start
    while day <= last_date:
        for interval in range(144):
            for group in GROUPS:
                profiles[(day.weekday(), interval, group)].append(source.get((day, interval, group), 0))
        day += dt.timedelta(days=1)
    result = []
    for day_offset in range(params.horizon_days):
        forecast_day = start + dt.timedelta(days=day_offset)
        for interval in range(144):
            demand = {}
            for group in GROUPS:
                values = profiles.get((forecast_day.weekday(), interval, group), [0])
                base = sum(values) / len(values)
                demand[group] = round(base * (1 + params.growth(group) / 100), 3)
            result.append({
                "date": forecast_day.isoformat(),
                "hour": interval // 6,
                "minute": (interval % 6) * 10,
                "interval": interval,
                "demand": demand,
            })
    return result, {group: 1.0 for group in GROUPS}


def _allocate_hour(
    demand: dict[str, float],
    staff: Counter[str],
    reserve_percent: float,
    positions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    utilization = max(0.05, 1 - reserve_percent / 100)
    configured = [item for item in (positions or default_config()["positions"]) if item.get("enabled", True)]
    legacy_keys = {
        ("junior", "day"): "junior",
        ("specialist", "day"): "specialist",
        ("expert", "day"): "day_expert",
        ("expert", "night"): "night_expert",
    }
    pools = []
    capacities = {}
    for position in configured:
        for mode in ("day", "night"):
            key = f"{position['id']}:{mode}"
            count = staff[key] + staff[legacy_keys.get((position["id"], mode), "")]
            value = count * float(position["capacity"]) * utilization
            capacities[key] = round(value, 2)
            pools.append({
                "position": position,
                "mode": mode,
                "remaining": value,
            })

    deficits = {}
    # Сначала закрываем самые узкие компетенции, затем общую очередь.
    for group in ("expert", "specialist", "shared"):
        remaining = float(demand[group])
        for pool in pools:
            skills_key = "night_groups" if pool["mode"] == "night" else "groups"
            if group not in pool["position"].get(skills_key, []):
                continue
            used = min(remaining, pool["remaining"])
            remaining -= used
            pool["remaining"] -= used
            if remaining <= 0:
                break
        deficits[group] = round(max(0.0, remaining), 2)

    return {
        "capacity": capacities,
        "deficit": deficits,
        "unused": round(sum(pool["remaining"] for pool in pools), 2),
    }


def evaluate_capacity(
    forecast: list[dict[str, object]],
    hourly_staff: dict[tuple[str, int], Counter[str]],
    reserve_percent: float,
    positions: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    result = []
    for item in forecast:
        staff = hourly_staff.get((item["date"], item["hour"]), Counter())
        allocation = _allocate_hour(item["demand"], staff, reserve_percent, positions)
        result.append({
            **item,
            "staff": dict(staff),
            **allocation,
        })
    return result


def evaluate_sla_capacity(
    forecast: list[dict[str, object]],
    hourly_staff: dict[tuple[str, int], Counter[str]],
    reserve_percent: float,
    positions: list[dict[str, object]] | None = None,
    sla_minutes: dict[str, int] | None = None,
) -> list[dict[str, object]]:
    """Carry backlog between 10-minute intervals and count a deficit once its SLA expires."""
    utilization = max(0.05, 1 - reserve_percent / 100)
    configured = [item for item in (positions or default_config()["positions"]) if item.get("enabled", True)]
    sla = sla_minutes or {"shared": 30, "specialist": 30, "expert": 40}
    backlog: dict[str, list[dict[str, object]]] = {group: [] for group in GROUPS}
    legacy_keys = {
        ("junior", "day"): "junior", ("specialist", "day"): "specialist",
        ("expert", "day"): "day_expert", ("expert", "night"): "night_expert",
    }
    result = []
    for step, item in enumerate(forecast):
        for group, value in item["demand"].items():
            if value > 0:
                slots = max(1, math.ceil(sla[group] / 10))
                backlog[group].append({
                    "remaining": float(value), "deadline": step + slots - 1, "missed": False,
                })
        staff = hourly_staff.get((item["date"], int(item["hour"])), Counter())
        pools = []
        capacities = {}
        for position in configured:
            for mode in ("day", "night"):
                key = f"{position['id']}:{mode}"
                count = staff[key] + staff[legacy_keys.get((position["id"], mode), "")]
                capacity = count * float(position["capacity"]) * utilization / 6
                capacities[key] = round(capacity, 3)
                pools.append({"position": position, "mode": mode, "remaining": capacity})
        pools.sort(key=lambda pool: len(pool["position"].get("night_groups" if pool["mode"] == "night" else "groups", [])))
        for pool in pools:
            skills_key = "night_groups" if pool["mode"] == "night" else "groups"
            skills = pool["position"].get(skills_key, [])
            while pool["remaining"] > 1e-9:
                candidates = [
                    group for group in skills
                    if any(float(batch["remaining"]) > 1e-9 for batch in backlog[group])
                ]
                if not candidates:
                    break
                group = min(candidates, key=lambda value: next(
                    int(batch["deadline"]) for batch in backlog[value] if float(batch["remaining"]) > 1e-9
                ))
                batch = next(batch for batch in backlog[group] if float(batch["remaining"]) > 1e-9)
                used = min(pool["remaining"], float(batch["remaining"]))
                pool["remaining"] -= used
                batch["remaining"] = float(batch["remaining"]) - used
        missed = {group: 0.0 for group in GROUPS}
        for group in GROUPS:
            for batch in backlog[group]:
                if not batch["missed"] and int(batch["deadline"]) <= step and float(batch["remaining"]) > 1e-9:
                    missed[group] += float(batch["remaining"])
                    batch["missed"] = True
            backlog[group] = [batch for batch in backlog[group] if float(batch["remaining"]) > 1e-9]
        result.append({
            **item,
            "staff": dict(staff),
            "capacity": capacities,
            "deficit": {group: round(value, 3) for group, value in missed.items()},
            "backlog": {group: round(sum(float(batch["remaining"]) for batch in backlog[group]), 3) for group in GROUPS},
            "unused": round(sum(pool["remaining"] for pool in pools), 3),
        })
    return result


def _historical_change(
    history: dict[str, object],
    forecast: list[dict[str, object]],
) -> dict[str, float]:
    last_date = dt.date.fromisoformat(history["meta"]["last_date"])
    days = min(28, len({item["date"] for item in forecast}))
    start = last_date - dt.timedelta(days=days - 1)
    historical = Counter()
    for day, group, count in history["daily"]:
        current = dt.date.fromisoformat(day)
        if start <= current <= last_date:
            historical[group] += int(count)
    predicted = Counter()
    comparison_dates = set(sorted({item["date"] for item in forecast})[:days])
    for item in forecast:
        if item["date"] not in comparison_dates:
            continue
        for group, value in item["demand"].items():
            predicted[group] += value
    result = {
        group: round((predicted[group] / historical[group] - 1) * 100, 1)
        if historical[group] else 0.0
        for group in GROUPS
    }
    historical_total = sum(historical.values())
    predicted_total = sum(predicted.values())
    result["overall"] = round((predicted_total / historical_total - 1) * 100, 1) if historical_total else 0.0
    return result


def recommendations(
    rows: list[dict[str, object]],
    positions: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    configured = [item for item in (positions or default_config()["positions"]) if item.get("enabled", True)]
    by_id = {item["id"]: item for item in configured}
    role_deficit_hours = Counter()
    role_periods = Counter()
    peak = Counter()
    deficit_by_role_hour: dict[str, Counter[int]] = defaultdict(Counter)

    for row in rows:
        date = dt.date.fromisoformat(row["date"])
        hour = int(row["hour"])
        deficits = row["deficit"]
        for group, value in deficits.items():
            if value <= 0:
                continue
            is_night = hour >= 23 or hour < 6
            skills_key = "night_groups" if is_night else "groups"
            candidates = [item for item in configured if group in item.get(skills_key, [])]
            if is_night:
                candidates.sort(key=lambda item: len(item.get("night_groups", [])) <= len(item.get("groups", [])))
            if not candidates:
                continue
            role = candidates[0]["id"]
            role_periods[role] += value
            equivalent_capacity = float(candidates[0]["capacity"])
            role_deficit_hours[role] += value / equivalent_capacity
            peak[role] = max(peak[role], value / equivalent_capacity)
            deficit_by_role_hour[role][hour] += value

    day_shifts = [(6, 18, "2/2 06:00–18:00"), (10, 22, "2/2 10:00–22:00"), (13, 22, "5/2 13:00–22:00")]
    flexible_shifts = [(20, 8, "2/2 20:00–08:00"), (8, 20, "2/2 08:00–20:00"), (10, 19, "5/2 10:00–19:00")]
    horizon_days = max(1, len({row["date"] for row in rows}))
    result = []
    for position in configured:
        role = position["id"]
        if role_periods[role] <= 0:
            continue
        weekly_hours = role_deficit_hours[role] * 7 / horizon_days
        weekly_fte = weekly_hours / 40
        if weekly_fte < 0.5:
            continue
        people = max(1, math.ceil(weekly_hours / 40))
        hourly = deficit_by_role_hour[role]
        night_weight = sum(value for hour, value in hourly.items() if hour >= 23 or hour < 6)
        shift_suggestions = flexible_shifts if night_weight > sum(hourly.values()) * 0.35 else day_shifts
        best_shift = max(
            shift_suggestions,
            key=lambda shift: sum(hourly[h] for h in _shift_hours(shift[0], shift[1])),
        )
        result.append({
            "role": by_id[role]["name"],
            "people": people,
            "shift": best_shift[2],
            "deficit_periods": round(role_periods[role], 1),
            "peak_people": math.ceil(peak[role]),
        })
    return result


def _shift_hours(start: int, end: int) -> list[int]:
    return list(range(start, 24)) + list(range(0, end)) if end <= start else list(range(start, end))


def summarize(
    history: dict[str, object],
    forecast: list[dict[str, object]],
    evaluated: list[dict[str, object]],
    trends: dict[str, float],
    positions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    demand = Counter()
    deficit = Counter()
    peak_deficit = Counter()
    uncovered_hour_keys: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for row in evaluated:
        for group in GROUPS:
            demand[group] += row["demand"][group]
            deficit[group] += row["deficit"][group]
            peak_deficit[group] = max(peak_deficit[group], row["deficit"][group])
            if row["deficit"][group] > 0:
                uncovered_hour_keys[group].add((row["date"], int(row["hour"])))
    total_demand = sum(demand.values())
    total_deficit = sum(deficit.values())
    comparison_days = min(28, len({item["date"] for item in forecast}))
    history_end = dt.date.fromisoformat(history["meta"]["last_date"])
    history_start = history_end - dt.timedelta(days=max(0, comparison_days - 1))
    forecast_dates = sorted({item["date"] for item in forecast})[:comparison_days]
    return {
        "change_percent": _historical_change(history, forecast),
        "comparison_period": {
            "days": comparison_days,
            "history_start": history_start.isoformat(),
            "history_end": history_end.isoformat(),
            "forecast_start": forecast_dates[0] if forecast_dates else None,
            "forecast_end": forecast_dates[-1] if forecast_dates else None,
        },
        "trend_percent": {group: round((value - 1) * 100, 1) for group, value in trends.items()},
        "groups": {
            group: {
                "label": GROUP_LABELS[group],
                "demand": round(demand[group], 1),
                "deficit": round(deficit[group], 1),
                "coverage_percent": round((1 - deficit[group] / demand[group]) * 100, 1) if demand[group] else 100.0,
                "peak_deficit": round(peak_deficit[group], 1),
                "uncovered_hours": len(uncovered_hour_keys[group]),
            }
            for group in GROUPS
        },
        "overall": {
            "demand": round(total_demand, 1),
            "deficit": round(total_deficit, 1),
            "coverage_percent": round((1 - total_deficit / total_demand) * 100, 1) if total_demand else 100.0,
        },
        "recommendations": recommendations(evaluated, positions),
    }
