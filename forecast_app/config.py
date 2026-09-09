from __future__ import annotations

import json
import re
from pathlib import Path


GROUPS = ("shared", "specialist", "expert")
DEFAULT_SLA = {
    "shared": {"target_percent": 90.0, "threshold_minutes": 30},
    "specialist": {"target_percent": 90.0, "threshold_minutes": 30},
    "expert": {"target_percent": 90.0, "threshold_minutes": 40},
}
DEFAULT_POSITIONS = [
    {
        "id": "junior",
        "name": "Младший специалист",
        "schedule_role": "Младший специалист",
        "capacity": 4.0,
        "enabled": True,
        "groups": ["shared"],
        "night_groups": ["shared"],
    },
    {
        "id": "specialist",
        "name": "Специалист",
        "schedule_role": "Специалист",
        "capacity": 4.0,
        "enabled": True,
        "groups": ["specialist", "shared"],
        "night_groups": ["specialist", "shared"],
    },
    {
        "id": "expert",
        "name": "Эксперт",
        "schedule_role": "Эксперт",
        "capacity": 3.2,
        "enabled": True,
        "groups": ["expert"],
        "night_groups": ["expert", "specialist", "shared"],
    },
]


def default_config() -> dict[str, object]:
    return {
        "operation_start_hour": 0,
        "operation_end_hour": 0,
        "positions": [dict(item) for item in DEFAULT_POSITIONS],
        "sla_groups": {group: dict(settings) for group, settings in DEFAULT_SLA.items()},
        "sla_queues": {},
    }


class StaffingConfig:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, object]:
        if not self.path.exists():
            return default_config()
        try:
            with self.path.open("r", encoding="utf-8") as source:
                return self.validate(json.load(source))
        except (OSError, ValueError, json.JSONDecodeError):
            return default_config()

    def save(self, value: object) -> dict[str, object]:
        config = self.validate(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as target:
            json.dump(config, target, ensure_ascii=False, indent=2)
        temp.replace(self.path)
        return config

    @staticmethod
    def validate(value: object) -> dict[str, object]:
        if not isinstance(value, dict) or not isinstance(value.get("positions"), list):
            raise ValueError("Ожидается список должностей")
        if not value["positions"]:
            raise ValueError("Добавьте хотя бы одну должность")
        if len(value["positions"]) > 20:
            raise ValueError("Допустимо не более 20 должностей")
        positions = []
        used_ids = set()
        for index, raw in enumerate(value["positions"]):
            if not isinstance(raw, dict):
                raise ValueError(f"Некорректная должность №{index + 1}")
            name = str(raw.get("name") or "").strip()
            if not name:
                raise ValueError(f"Укажите название должности №{index + 1}")
            position_id = re.sub(r"[^a-z0-9_-]", "", str(raw.get("id") or "").lower())
            if not position_id:
                position_id = f"position_{index + 1}"
            if position_id in used_ids:
                raise ValueError("Идентификаторы должностей должны быть уникальными")
            used_ids.add(position_id)
            try:
                capacity = float(raw.get("capacity"))
            except (TypeError, ValueError):
                raise ValueError(f"Некорректная ёмкость должности «{name}»") from None
            if not 0.1 <= capacity <= 100:
                raise ValueError(f"Ёмкость «{name}» должна быть от 0,1 до 100")
            groups = [group for group in raw.get("groups", []) if group in GROUPS]
            night_groups = [group for group in raw.get("night_groups", groups) if group in GROUPS]
            positions.append({
                "id": position_id,
                "name": name[:80],
                "schedule_role": str(raw.get("schedule_role") or name).strip()[:80],
                "capacity": capacity,
                "enabled": bool(raw.get("enabled", True)),
                "groups": list(dict.fromkeys(groups)),
                "night_groups": list(dict.fromkeys(night_groups)),
            })
        if not any(item["enabled"] for item in positions):
            raise ValueError("Включите хотя бы одну должность")
        # Старый переключатель автоматически переводится в часы работы.
        legacy_night = bool(value.get("night_operation_enabled", True))
        default_start, default_end = ((0, 0) if legacy_night else (6, 23))
        try:
            start_hour = int(value.get("operation_start_hour", default_start))
            end_hour = int(value.get("operation_end_hour", default_end))
        except (TypeError, ValueError):
            raise ValueError("Некорректное время работы поддержки") from None
        if not 0 <= start_hour <= 23 or not 0 <= end_hour <= 23:
            raise ValueError("Часы работы должны быть в диапазоне от 00:00 до 23:00")
        sla_groups = {}
        raw_groups = value.get("sla_groups", {}) if isinstance(value.get("sla_groups", {}), dict) else {}
        for group in GROUPS:
            raw = raw_groups.get(group, {}) if isinstance(raw_groups.get(group, {}), dict) else {}
            sla_groups[group] = StaffingConfig._validate_sla(raw, DEFAULT_SLA[group], f"группы «{group}»")
        raw_queues = value.get("sla_queues", {}) if isinstance(value.get("sla_queues", {}), dict) else {}
        if len(raw_queues) > 500:
            raise ValueError("Допустимо не более 500 настроек SLA очередей")
        sla_queues = {}
        for queue, raw in raw_queues.items():
            name = str(queue).strip()[:200]
            if name and isinstance(raw, dict):
                sla_queues[name] = StaffingConfig._validate_sla(raw, None, f"очереди «{name}»", allow_inherit=True)
        return {
            "operation_start_hour": start_hour,
            "operation_end_hour": end_hour,
            "positions": positions,
            "sla_groups": sla_groups,
            "sla_queues": sla_queues,
        }

    @staticmethod
    def _validate_sla(raw: dict[str, object], default: dict[str, object] | None, label: str, allow_inherit: bool = False) -> dict[str, object]:
        result = {}
        for key, low, high, title in (("target_percent", 1, 100, "процент"), ("threshold_minutes", 1, 1440, "время")):
            value = raw.get(key, None)
            if allow_inherit and (value is None or value == ""):
                result[key] = None
                continue
            if value is None and default is not None:
                value = default[key]
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"Некорректный {title} SLA для {label}") from None
            if not low <= number <= high:
                raise ValueError(f"{title.capitalize()} SLA для {label} должен быть от {low} до {high}")
            result[key] = round(number, 1) if key == "target_percent" else int(number)
        return result
