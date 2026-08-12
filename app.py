from __future__ import annotations

import argparse
import base64
import datetime as dt
import hmac
import json
import mimetypes
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from forecast_app.model import ForecastParameters
from forecast_app.service import ForecastService
from forecast_app.xlsx import XlsxBook


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
SERVICE = ForecastService(ROOT)
UPLOAD_LOCK = threading.Lock()
MAX_HISTORY_SIZE = 1024 * 1024 * 1024
REQUIRED_HISTORY_COLUMNS = {"Очередь", "Начало периода", "Канал"}
REQUIRED_QUEUE_COLUMNS = {
    "Очередь", "Подлиния", "Техническая очередь",
    "Архивная очередь", "Учитываем в прогнозе",
}


class Handler(BaseHTTPRequestHandler):
    def authenticate(self) -> bool:
        username = os.environ.get("FORECASTER_USERNAME", "")
        password = os.environ.get("FORECASTER_PASSWORD", "")
        if not username or not password:
            return True
        header = self.headers.get("Authorization", "")
        try:
            scheme, encoded = header.split(" ", 1)
            supplied = base64.b64decode(encoded).decode("utf-8")
            supplied_username, supplied_password = supplied.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            supplied_username = supplied_password = ""
            scheme = ""
        if scheme.lower() == "basic" and hmac.compare_digest(supplied_username, username) and hmac.compare_digest(supplied_password, password):
            return True
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="Support Planning", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self):
        if not self.authenticate():
            return
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/meta":
                return self.send_json(SERVICE.metadata())
            if parsed.path == "/api/config":
                return self.send_json(SERVICE.get_staffing_config())
            if parsed.path == "/api/schedule":
                query = parse_qs(parsed.query)
                metadata = SERVICE.metadata()
                sheet = self._string(query, "sheet", metadata["default_schedule_sheet"])
                return self.send_json(SERVICE.schedule(sheet))
            if parsed.path == "/api/vacation-capacity":
                query = parse_qs(parsed.query)
                metadata = SERVICE.metadata()
                sheet = self._string(query, "sheet", metadata["default_schedule_sheet"])
                return self.send_json(SERVICE.vacation_capacity(sheet))
            if parsed.path == "/api/employees":
                query = parse_qs(parsed.query)
                return self.send_json({"employees": SERVICE.employees(self._string(query, "service", ""))})
            if parsed.path == "/api/hr-services":
                return self.send_json({"services": SERVICE.hr_services()})
            if parsed.path == "/api/hr-records":
                query = parse_qs(parsed.query)
                section = self._string(query, "section", "")
                return self.send_json({"records": SERVICE.hr_records(section, self._string(query, "service", ""))})
            if parsed.path == "/api/analyze":
                query = parse_qs(parsed.query)
                metadata = SERVICE.metadata()
                sheet = self._string(query, "sheet", metadata["default_schedule_sheet"])
                if sheet not in metadata["schedule_sheets"]:
                    return self.send_json({"error": "Неизвестный лист графика"}, HTTPStatus.BAD_REQUEST)
                params = ForecastParameters(
                    horizon_days=self._integer(query, "horizon", 28, 7, 62),
                    lookback_weeks=self._integer(query, "lookback", 8, 4, 26),
                    reserve_percent=self._float(query, "reserve", 15, 0, 40),
                    shared_growth=self._float(query, "shared_growth", 0, -50, 100),
                    specialist_growth=self._float(query, "specialist_growth", 0, -50, 100),
                    expert_growth=self._float(query, "expert_growth", 0, -50, 100),
                    include_vacancies=self._string(query, "vacancies", "0") in {"1", "true", "on"},
                )
                return self.send_json(SERVICE.analyze(sheet, params))
            return self.send_static(parsed.path)
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self):
        if not self.authenticate():
            return
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/history":
                return self.upload_history()
            if parsed.path == "/api/queues":
                return self.upload_queues()
            if parsed.path == "/api/config":
                return self.update_staffing_config()
            if parsed.path == "/api/schedule":
                return self.update_schedule()
            if parsed.path == "/api/schedule-bulk":
                return self.update_schedule_bulk()
            if parsed.path == "/api/schedule-order":
                return self.schedule_action(SERVICE.reorder_schedule_employees)
            if parsed.path == "/api/schedule-delete":
                return self.schedule_action(SERVICE.delete_schedule_employee)
            if parsed.path == "/api/schedule-employee-update":
                return self.schedule_action(SERVICE.edit_schedule_employee)
            if parsed.path == "/api/employees":
                return self.add_employee()
            if parsed.path == "/api/hr-records":
                return self.add_hr_record(parsed)
            if parsed.path == "/api/hr-services":
                return self.add_hr_service()
            if parsed.path == "/api/schedule-employees":
                return self.add_schedule_employee()
            if parsed.path == "/api/schedule-months":
                return self.schedule_action(SERVICE.create_schedule_month)
            return self.send_json({"error": "Маршрут не найден"}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            return self.send_json({"error": f"Не удалось загрузить историю: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def update_staffing_config(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > 1024 * 1024:
            raise ValueError("Некорректный размер настроек")
        try:
            value = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Некорректный JSON настроек") from None
        return self.send_json({"ok": True, "config": SERVICE.update_staffing_config(value)})

    def update_schedule(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > 64 * 1024:
            raise ValueError("Некорректный размер данных смены")
        try:
            value = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Некорректный JSON смены") from None
        return self.send_json({"ok": True, "schedule": SERVICE.update_schedule(value)})

    def update_schedule_bulk(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > 64 * 1024:
            raise ValueError("Некорректный размер данных периода")
        try:
            value = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Некорректный JSON периода") from None
        return self.send_json({"ok": True, "schedule": SERVICE.update_schedule_bulk(value)})

    def schedule_action(self, action):
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > 128 * 1024:
            raise ValueError("Некорректный размер данных")
        try:
            value = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Некорректный JSON") from None
        return self.send_json({"ok": True, "schedule": action(value)})

    def add_employee(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > 256 * 1024:
            raise ValueError("Некорректный размер данных сотрудника")
        try:
            value = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Некорректный JSON сотрудника") from None
        return self.send_json({"ok": True, "employee": SERVICE.add_employee(value)}, HTTPStatus.CREATED)

    def add_hr_record(self, parsed):
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > 512 * 1024:
            raise ValueError("Некорректный размер данных")
        try:
            value = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Некорректный JSON") from None
        section = self._string(parse_qs(parsed.query), "section", "")
        service_id = self._string(parse_qs(parsed.query), "service", "metrika")
        return self.send_json({"ok": True, "record": SERVICE.add_hr_record(section, value, service_id)}, HTTPStatus.CREATED)

    def add_hr_service(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > 64 * 1024:
            raise ValueError("Некорректный размер данных сервиса")
        try:
            value = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Некорректный JSON сервиса") from None
        return self.send_json({"ok": True, "service": SERVICE.add_hr_service(value)}, HTTPStatus.CREATED)

    def add_schedule_employee(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > 64 * 1024:
            raise ValueError("Некорректный размер данных сотрудника")
        try:
            value = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Некорректный JSON сотрудника") from None
        return self.send_json({"ok": True, "schedule": SERVICE.add_schedule_employee(value)}, HTTPStatus.CREATED)

    def upload_history(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            raise ValueError("Файл пуст")
        if content_length > MAX_HISTORY_SIZE:
            raise ValueError("Файл превышает допустимый размер 1 ГБ")
        if not UPLOAD_LOCK.acquire(blocking=False):
            return self.send_json({"error": "Другая загрузка уже обрабатывается"}, HTTPStatus.CONFLICT)

        temp_path = ROOT / ".history-upload.tmp"
        try:
            remaining = content_length
            with temp_path.open("wb") as target:
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("Передача файла прервана")
                    target.write(chunk)
                    remaining -= len(chunk)

            with temp_path.open("r", encoding="utf-8-sig", newline="") as source:
                first_line = source.readline().rstrip("\r\n")
            headers = {value.strip() for value in first_line.split("\t")}
            missing = REQUIRED_HISTORY_COLUMNS - headers
            if missing:
                raise ValueError(
                    "Неверный формат выгрузки. Не найдены колонки: " + ", ".join(sorted(missing))
                )

            workload = SERVICE.workload_path
            backup_dir = ROOT / "backups"
            backup_dir.mkdir(exist_ok=True)
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = backup_dir / f"нагрузка-{stamp}.tsv"
            if workload.exists():
                os.replace(workload, backup_path)
            try:
                os.replace(temp_path, workload)
            except Exception:
                if backup_path.exists() and not workload.exists():
                    os.replace(backup_path, workload)
                raise

            try:
                history = SERVICE.reload_history()
            except Exception:
                rejected_path = backup_dir / f"отклоненная-нагрузка-{stamp}.tsv"
                if workload.exists():
                    os.replace(workload, rejected_path)
                if backup_path.exists():
                    os.replace(backup_path, workload)
                SERVICE.reload_history()
                raise ValueError("Файл не удалось обработать; предыдущая история восстановлена")
            return self.send_json({
                "ok": True,
                "message": "Исторические данные обновлены",
                "history": history["meta"],
                "backup": backup_path.name if backup_path.exists() else None,
            })
        finally:
            temp_path.unlink(missing_ok=True)
            UPLOAD_LOCK.release()

    def upload_queues(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            raise ValueError("Файл пуст")
        if content_length > 50 * 1024 * 1024:
            raise ValueError("Файл превышает допустимый размер 50 МБ")
        if not UPLOAD_LOCK.acquire(blocking=False):
            return self.send_json({"error": "Другая загрузка уже обрабатывается"}, HTTPStatus.CONFLICT)

        temp_path = ROOT / ".queues-upload.xlsx"
        try:
            remaining = content_length
            with temp_path.open("wb") as target:
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("Передача файла прервана")
                    target.write(chunk)
                    remaining -= len(chunk)

            try:
                rows = XlsxBook(temp_path).rows("Chart data")
            except Exception:
                raise ValueError("Не удалось прочитать XLSX или отсутствует лист «Chart data»") from None
            if not rows:
                raise ValueError("Лист «Chart data» пуст")
            headers = {str(value).strip() for value in rows[0] if value}
            missing = REQUIRED_QUEUE_COLUMNS - headers
            if missing:
                raise ValueError(
                    "Неверный формат справочника. Не найдены колонки: " + ", ".join(sorted(missing))
                )

            queue_column = next(index for index, value in enumerate(rows[0]) if str(value).strip() == "Очередь")
            queue_rows = [
                row for row in rows[1:]
                if queue_column < len(row) and str(row[queue_column] or "").strip()
            ]
            if not queue_rows:
                raise ValueError("В справочнике не найдено ни одной очереди")

            queue_path = SERVICE.queue_path
            backup_dir = ROOT / "backups"
            backup_dir.mkdir(exist_ok=True)
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = backup_dir / f"очереди-{stamp}.xlsx"
            if queue_path.exists():
                os.replace(queue_path, backup_path)
            try:
                os.replace(temp_path, queue_path)
                history = SERVICE.reload_history()
            except Exception:
                rejected_path = backup_dir / f"отклоненные-очереди-{stamp}.xlsx"
                if queue_path.exists():
                    os.replace(queue_path, rejected_path)
                if backup_path.exists():
                    os.replace(backup_path, queue_path)
                SERVICE.reload_history()
                raise ValueError("Справочник не удалось обработать; предыдущая версия восстановлена")

            metadata = SERVICE.metadata()
            return self.send_json({
                "ok": True,
                "message": "Справочник очередей обновлён",
                "history": history["meta"],
                "queue_groups": metadata["queue_groups"],
                "queue_count": sum(len(names) for names in metadata["queue_groups"].values()),
                "backup": backup_path.name if backup_path.exists() else None,
            })
        finally:
            temp_path.unlink(missing_ok=True)
            UPLOAD_LOCK.release()

    def send_static(self, request_path: str):
        if request_path in {"", "/"}:
            relative = "index.html"
        elif request_path.startswith("/static/"):
            relative = request_path[len("/static/"):] if request_path.startswith("/static/") else request_path
        else:
            return self.send_error(HTTPStatus.NOT_FOUND)
        target = (STATIC / relative).resolve()
        if STATIC.resolve() not in target.parents and target != STATIC.resolve():
            return self.send_error(HTTPStatus.NOT_FOUND)
        if not target.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        payload = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, value, status=HTTPStatus.OK):
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    @staticmethod
    def _string(query, key, default):
        return query.get(key, [default])[0]

    @classmethod
    def _integer(cls, query, key, default, minimum, maximum):
        return int(max(minimum, min(maximum, int(cls._string(query, key, default)))))

    @classmethod
    def _float(cls, query, key, default, minimum, maximum):
        return max(minimum, min(maximum, float(cls._string(query, key, default))))

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {format % args}")


def main():
    parser = argparse.ArgumentParser(description="Support workload forecaster MVP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Forecaster is running: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
