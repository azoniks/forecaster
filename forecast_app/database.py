from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

class AppDatabase:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        seed_path = path.with_name(f"{path.stem}.seed{path.suffix}")
        if not path.exists() and seed_path.exists():
            shutil.copy2(seed_path, path)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS documents (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schedule_months (
                    name TEXT PRIMARY KEY,
                    position INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schedule_dates (
                    month TEXT NOT NULL REFERENCES schedule_months(name) ON DELETE CASCADE,
                    date TEXT NOT NULL,
                    PRIMARY KEY (month, date)
                );
                CREATE TABLE IF NOT EXISTS schedule_employees (
                    month TEXT NOT NULL REFERENCES schedule_months(name) ON DELETE CASCADE,
                    employee_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    login TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL,
                    is_vacancy INTEGER NOT NULL DEFAULT 0,
                    schedule_pattern TEXT NOT NULL DEFAULT '',
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    terminated_on TEXT,
                    PRIMARY KEY (month, employee_id)
                );
                CREATE TABLE IF NOT EXISTS schedule_cells (
                    month TEXT NOT NULL,
                    employee_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    start INTEGER,
                    end INTEGER,
                    activity TEXT NOT NULL DEFAULT 'day_off',
                    note TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (month, employee_id, date),
                    FOREIGN KEY (month, employee_id)
                        REFERENCES schedule_employees(month, employee_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS schedule_cells_month_date
                    ON schedule_cells(month, date);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(schedule_employees)")}
            if "sort_order" not in columns:
                db.execute("ALTER TABLE schedule_employees ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
            if "terminated_on" not in columns:
                db.execute("ALTER TABLE schedule_employees ADD COLUMN terminated_on TEXT")
            cell_columns = {row["name"] for row in db.execute("PRAGMA table_info(schedule_cells)")}
            if "note" not in cell_columns:
                db.execute("ALTER TABLE schedule_cells ADD COLUMN note TEXT NOT NULL DEFAULT ''")
            db.execute("""
                UPDATE schedule_employees
                SET sort_order = rowid
                WHERE sort_order = 0
            """)

    def connect(self):
        connection = sqlite3.connect(str(self.path), timeout=20)
        connection.row_factory = sqlite3.Row
        return connection

    def document(self, key: str, default):
        with self.connect() as db:
            row = db.execute("SELECT value FROM documents WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return default

    def save_document(self, key: str, value):
        payload = json.dumps(value, ensure_ascii=False)
        with self.connect() as db:
            db.execute(
                "INSERT INTO documents(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, payload),
            )
        return value

    def migrate_documents(self, paths: dict[str, Path], defaults: dict[str, object]):
        for key, path in paths.items():
            with self.connect() as db:
                exists = db.execute("SELECT 1 FROM documents WHERE key=?", (key,)).fetchone()
            if exists:
                continue
            value = defaults.get(key)
            if path.exists():
                try:
                    with path.open("r", encoding="utf-8") as source:
                        value = json.load(source)
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
            self.save_document(key, value)

    def schedule_sheets(self) -> list[str]:
        with self.connect() as db:
            return [row["name"] for row in db.execute("SELECT name FROM schedule_months ORDER BY position")]

    def schedule_rows(self, month: str):
        with self.connect() as db:
            dates = [row["date"] for row in db.execute(
                "SELECT date FROM schedule_dates WHERE month=? ORDER BY date", (month,)
            )]
            employees = [dict(row) for row in db.execute(
                "SELECT employee_id AS id,name,login,role,is_vacancy,schedule_pattern,sort_order,terminated_on "
                "FROM schedule_employees WHERE month=? ORDER BY role,sort_order,name", (month,)
            )]
            cells = [dict(row) for row in db.execute(
                "SELECT employee_id,date,start,end,activity,note FROM schedule_cells WHERE month=?",
                (month,),
            )]
        return dates, employees, cells

    def create_schedule_month(self, name: str, dates: list[str], employees: list[dict], cells: list[dict]):
        with self.connect() as db:
            if db.execute("SELECT 1 FROM schedule_months WHERE name=?", (name,)).fetchone():
                raise ValueError("График на этот месяц уже существует")
            position = db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM schedule_months").fetchone()[0]
            db.execute("INSERT INTO schedule_months(name,position) VALUES(?,?)", (name, position))
            db.executemany(
                "INSERT INTO schedule_dates(month,date) VALUES(?,?)",
                [(name, date) for date in dates],
            )
            db.executemany(
                "INSERT INTO schedule_employees(month,employee_id,name,login,role,is_vacancy,schedule_pattern,sort_order,terminated_on) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                [(
                    name, item["id"], item["name"], item.get("login", ""), item["role"],
                    int(item.get("is_vacancy", False)), item.get("schedule_pattern", ""),
                    int(item.get("sort_order", 0)),
                    item.get("terminated_on"),
                ) for item in employees],
            )
            db.executemany(
                "INSERT INTO schedule_cells(month,employee_id,date,start,end,activity,note) VALUES(?,?,?,?,?,?,?)",
                [(
                    name, item["employee_id"], item["date"], item.get("start"), item.get("end"),
                    item.get("activity", "work"), item.get("note", ""),
                ) for item in cells],
            )

    def add_schedule_employee(self, month: str, employee_id: str, name: str, login: str, role: str, pattern: str):
        with self.connect() as db:
            order = db.execute(
                "SELECT COALESCE(MAX(sort_order),0)+1 FROM schedule_employees WHERE month=? AND role=?",
                (month, role),
            ).fetchone()[0]
            db.execute(
                "INSERT INTO schedule_employees(month,employee_id,name,login,role,schedule_pattern,sort_order) VALUES(?,?,?,?,?,?,?)",
                (month, employee_id, name, login, role, pattern, order),
            )

    def copy_schedule_employee(self, month: str, employee: dict):
        with self.connect() as db:
            order = db.execute(
                "SELECT COALESCE(MAX(sort_order),0)+1 FROM schedule_employees WHERE month=? AND role=?",
                (month, employee["role"]),
            ).fetchone()[0]
            db.execute(
                "INSERT OR IGNORE INTO schedule_employees("
                "month,employee_id,name,login,role,is_vacancy,schedule_pattern,sort_order,terminated_on"
                ") VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    month, employee["id"], employee["name"], employee.get("login", ""),
                    employee["role"], int(employee.get("is_vacancy", False)),
                    employee.get("schedule_pattern", ""), order, employee.get("terminated_on"),
                ),
            )

    def set_schedule_cell(self, month: str, employee_id: str, date: str, start, end, activity: str, note: str = ""):
        with self.connect() as db:
            db.execute(
                "INSERT INTO schedule_cells(month,employee_id,date,start,end,activity,note) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(month,employee_id,date) DO UPDATE SET "
                "start=excluded.start,end=excluded.end,activity=excluded.activity,note=excluded.note",
                (month, employee_id, date, start, end, activity, note),
            )

    def reorder_schedule_employees(self, month: str, employee_ids: list[str]):
        with self.connect() as db:
            for order, employee_id in enumerate(employee_ids, 1):
                db.execute(
                    "UPDATE schedule_employees SET sort_order=? WHERE month=? AND employee_id=?",
                    (order, month, employee_id),
                )

    def delete_schedule_employee(self, month: str, employee_id: str):
        with self.connect() as db:
            cursor = db.execute(
                "DELETE FROM schedule_employees WHERE month=? AND employee_id=?",
                (month, employee_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Сотрудник не найден в графике")

    def terminate_schedule_employee(self, month: str, employee_id: str, deletion_date: str):
        """Keep schedule history, clear the departure day onward and remove future rows."""
        with self.connect() as db:
            exists = db.execute(
                "SELECT 1 FROM schedule_employees WHERE month=? AND employee_id=?",
                (month, employee_id),
            ).fetchone()
            if exists is None:
                raise ValueError("Сотрудник не найден в графике")
            db.execute(
                "UPDATE schedule_employees SET terminated_on=? WHERE employee_id=?",
                (deletion_date, employee_id),
            )
            db.execute(
                "DELETE FROM schedule_cells WHERE employee_id=? AND date>=?",
                (employee_id, deletion_date),
            )
            db.execute(
                "DELETE FROM schedule_employees WHERE employee_id=? AND month IN ("
                "SELECT month FROM schedule_dates GROUP BY month HAVING MIN(date)>?"
                ")",
                (employee_id, deletion_date),
            )

    def update_schedule_employee(
        self, month: str, employee_id: str, new_id: str,
        name: str, login: str, role: str,
    ):
        with self.connect() as db:
            current = db.execute(
                "SELECT * FROM schedule_employees WHERE month=? AND employee_id=?",
                (month, employee_id),
            ).fetchone()
            if current is None:
                raise ValueError("Сотрудник не найден в графике")
            if new_id != employee_id:
                duplicate = db.execute(
                    "SELECT 1 FROM schedule_employees WHERE month=? AND employee_id=?",
                    (month, new_id),
                ).fetchone()
                if duplicate:
                    raise ValueError("Сотрудник с таким логином уже есть в графике")
                db.execute(
                    "INSERT INTO schedule_employees("
                    "month,employee_id,name,login,role,is_vacancy,schedule_pattern,sort_order,terminated_on"
                    ") VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        month, new_id, name, login, role, current["is_vacancy"],
                        current["schedule_pattern"], current["sort_order"],
                        current["terminated_on"],
                    ),
                )
                db.execute(
                    "UPDATE schedule_cells SET employee_id=? WHERE month=? AND employee_id=?",
                    (new_id, month, employee_id),
                )
                db.execute(
                    "DELETE FROM schedule_employees WHERE month=? AND employee_id=?",
                    (month, employee_id),
                )
            else:
                db.execute(
                    "UPDATE schedule_employees SET name=?,login=?,role=? "
                    "WHERE month=? AND employee_id=?",
                    (name, login, role, month, employee_id),
                )
