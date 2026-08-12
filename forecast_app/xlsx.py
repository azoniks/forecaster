"""Small read-only XLSX reader built on Python's standard library."""

from __future__ import annotations

import datetime as dt
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def excel_date(value: object) -> dt.date | None:
    try:
        serial = float(value)
    except (TypeError, ValueError):
        return None
    return (dt.datetime(1899, 12, 30) + dt.timedelta(days=serial)).date()


def _column_index(reference: str) -> int:
    match = re.match(r"[A-Z]+", reference)
    if not match:
        return 0
    result = 0
    for char in match.group(0):
        result = result * 26 + ord(char) - 64
    return result - 1


class XlsxBook:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def sheet_names(self) -> list[str]:
        with zipfile.ZipFile(self.path) as archive:
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            return [
                sheet.attrib["name"]
                for sheet in workbook.findall(f".//{{{MAIN_NS}}}sheet")
            ]

    def rows(self, sheet_name: str) -> list[list[object | None]]:
        with zipfile.ZipFile(self.path) as archive:
            shared = self._shared_strings(archive)
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            relationships = ET.fromstring(
                archive.read("xl/_rels/workbook.xml.rels")
            )
            targets = {rel.attrib["Id"]: rel.attrib["Target"] for rel in relationships}

            target = None
            for sheet in workbook.findall(f".//{{{MAIN_NS}}}sheet"):
                if sheet.attrib["name"] == sheet_name:
                    relation_id = sheet.attrib[f"{{{DOC_REL_NS}}}id"]
                    target = targets[relation_id]
                    break
            if target is None:
                raise KeyError(f"Sheet not found: {sheet_name}")
            target = target.lstrip("/")
            if not target.startswith("xl/"):
                target = f"xl/{target}"

            root = ET.fromstring(archive.read(target))
            result: list[list[object | None]] = []
            for row_node in root.findall(f".//{{{MAIN_NS}}}sheetData/{{{MAIN_NS}}}row"):
                row: list[object | None] = []
                for cell in row_node.findall(f"{{{MAIN_NS}}}c"):
                    index = _column_index(cell.attrib["r"])
                    while len(row) <= index:
                        row.append(None)
                    row[index] = self._cell_value(cell, shared)
                result.append(row)
            return result

    def rows_with_fills(self, sheet_name: str) -> list[tuple[list[object | None], list[str | None]]]:
        """Return cell values together with their direct XLSX fill colors."""
        with zipfile.ZipFile(self.path) as archive:
            shared = self._shared_strings(archive)
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            targets = {rel.attrib["Id"]: rel.attrib["Target"] for rel in relationships}
            target = None
            for sheet in workbook.findall(f".//{{{MAIN_NS}}}sheet"):
                if sheet.attrib["name"] == sheet_name:
                    target = targets[sheet.attrib[f"{{{DOC_REL_NS}}}id"]]
                    break
            if target is None:
                raise KeyError(f"Sheet not found: {sheet_name}")
            target = target.lstrip("/")
            if not target.startswith("xl/"):
                target = f"xl/{target}"

            styles = ET.fromstring(archive.read("xl/styles.xml"))
            fills = []
            for fill in styles.findall(f"./{{{MAIN_NS}}}fills/{{{MAIN_NS}}}fill"):
                color = fill.find(f".//{{{MAIN_NS}}}fgColor")
                fills.append(color.attrib.get("rgb") if color is not None else None)
            style_fills = [
                fills[int(style.attrib.get("fillId", "0"))]
                for style in styles.findall(f"./{{{MAIN_NS}}}cellXfs/{{{MAIN_NS}}}xf")
            ]

            root = ET.fromstring(archive.read(target))
            result = []
            for row_node in root.findall(f".//{{{MAIN_NS}}}sheetData/{{{MAIN_NS}}}row"):
                values: list[object | None] = []
                colors: list[str | None] = []
                for cell in row_node.findall(f"{{{MAIN_NS}}}c"):
                    index = _column_index(cell.attrib["r"])
                    while len(values) <= index:
                        values.append(None)
                        colors.append(None)
                    values[index] = self._cell_value(cell, shared)
                    style_index = int(cell.attrib.get("s", "0"))
                    colors[index] = style_fills[style_index] if style_index < len(style_fills) else None
                result.append((values, colors))
            return result

    @staticmethod
    def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
        if "xl/sharedStrings.xml" not in archive.namelist():
            return []
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        return [
            "".join(node.text or "" for node in item.iter(f"{{{MAIN_NS}}}t"))
            for item in root.findall(f"{{{MAIN_NS}}}si")
        ]

    @staticmethod
    def _cell_value(cell: ET.Element, shared: list[str]) -> object | None:
        cell_type = cell.attrib.get("t")
        value_node = cell.find(f"{{{MAIN_NS}}}v")
        if cell_type == "inlineStr":
            inline = cell.find(f"{{{MAIN_NS}}}is")
            if inline is None:
                return None
            return "".join(node.text or "" for node in inline.iter(f"{{{MAIN_NS}}}t"))
        if value_node is None:
            return None
        value = value_node.text
        if cell_type == "s" and value is not None:
            return shared[int(value)]
        return value
