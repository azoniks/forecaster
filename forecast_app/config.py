from __future__ import annotations

import json
import re
from pathlib import Path


GROUPS = ("shared", "specialist", "expert")
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
        return {
            "operation_start_hour": start_hour,
            "operation_end_hour": end_hour,
            "positions": positions,
        }
