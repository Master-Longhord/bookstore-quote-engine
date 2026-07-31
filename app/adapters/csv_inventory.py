"""InventoryRepository backed by a CSV file.

Expected columns (header row required, case-insensitive):
    sku, title, author_or_publisher, price, stock

Single Responsibility: this class only reads/caches the CSV. If the
store later moves to Postgres or a Google Sheet, write a new class that
implements the same InventoryRepository port and inject it in main.py —
nothing else changes (Open/Closed).
"""
from __future__ import annotations

import csv
import os
import threading
from typing import Optional

from app.domain.models import InventoryItem


class CsvInventoryRepository:
    def __init__(self, csv_path: str):
        self._path = csv_path
        self._lock = threading.Lock()
        self._items: list[InventoryItem] = []
        self._by_sku: dict[str, InventoryItem] = {}
        self._mtime: float = 0.0
        self.refresh()

    # ---- InventoryRepository port ----

    def all_items(self) -> list[InventoryItem]:
        self._maybe_reload()
        return self._items

    def get(self, sku: str) -> Optional[InventoryItem]:
        self._maybe_reload()
        return self._by_sku.get(sku)

    def refresh(self) -> None:
        with self._lock:
            items: list[InventoryItem] = []
            with open(self._path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames is None:
                    raise ValueError(f"{self._path} has no header row")
                field_map = {name.strip().lower(): name for name in reader.fieldnames}
                required = {"sku", "title", "author_or_publisher", "price", "stock"}
                missing = required - set(field_map)
                if missing:
                    raise ValueError(
                        f"{self._path} missing columns: {', '.join(sorted(missing))}"
                    )
                for row in reader:
                    title = (row[field_map["title"]] or "").strip()
                    if not title:
                        continue  # skip blank lines
                    items.append(
                        InventoryItem(
                            sku=(row[field_map["sku"]] or "").strip(),
                            title=title,
                            author_or_publisher=(
                                row[field_map["author_or_publisher"]] or ""
                            ).strip(),
                            price=float(str(row[field_map["price"]]).replace(",", "") or 0),
                            stock=int(float(str(row[field_map["stock"]]) or 0)),
                        )
                    )
            self._items = items
            self._by_sku = {i.sku: i for i in items if i.sku}
            self._mtime = os.path.getmtime(self._path)

    # ---- internals ----

    def _maybe_reload(self) -> None:
        """Hot-reload when the shop owner uploads a new CSV over the old one."""
        try:
            if os.path.getmtime(self._path) > self._mtime:
                self.refresh()
        except OSError:
            pass  # file briefly missing mid-upload; keep serving the cache
