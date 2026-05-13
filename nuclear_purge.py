#!/usr/bin/env python3
"""
Nuclear purge for stale VcaniTrade host/port configuration.

Run from the repository root:
    python nuclear_purge.py

What it does:
- Replaces 192.168.0.39 with 127.0.0.1 everywhere it can safely edit.
- Replaces port-config occurrences of 9223 with 9222.
- Normalizes trading_settings.json and config_coordinates.json if present.
- Deletes Python bytecode caches and local Playwright browser cache folders.
- Backs up every edited file under .nuclear_purge_backups/<timestamp>/.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
OLD_IP = "192.168.0.39"
NEW_IP = "127.0.0.1"
OLD_PORT = "9223"
NEW_PORT = "9222"

BACKUP_DIR = ROOT / ".nuclear_purge_backups" / datetime.now().strftime("%Y%m%d_%H%M%S")
SKIP_DIR_NAMES = {".git", ".nuclear_purge_backups"}

PORT_CONTEXT_RE = re.compile(
    r"(port|cdp|debug|chrome|browser|playwright|remote|websocket|"
    r"ws://|wss://|http://|https://|localhost|127\.0\.0\.1|"
    r"192\.168\.0\.39|host|url|socket|netstat|findstr|\bfind\b)",
    re.IGNORECASE,
)
STANDALONE_9223_RE = re.compile(r"(?<![A-Za-z0-9])9223(?![A-Za-z0-9])")
SQLITE_HEADER = b"SQLite format 3\x00"

TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "utf-16", "utf-16-le", "utf-16-be", "cp1252")
backed_up: set[Path] = set()


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def backup_once(path: Path) -> None:
    if path in backed_up or not path.exists() or not path.is_file():
        return
    target = BACKUP_DIR / rel(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    backed_up.add(path)


def purge_text(text: str) -> str:
    text = text.replace(OLD_IP, NEW_IP)
    lines = text.splitlines(keepends=True)
    changed_lines: list[str] = []

    for line in lines:
        updated = line.replace(f":{OLD_PORT}", f":{NEW_PORT}")
        updated = re.sub(
            rf"(?i)(--remote-debugging-port\s*=\s*){OLD_PORT}\b",
            rf"\g<1>{NEW_PORT}",
            updated,
        )
        updated = re.sub(
            rf"(?i)(\b[A-Z0-9_]*PORT[A-Z0-9_]*\s*=\s*){OLD_PORT}\b",
            rf"\g<1>{NEW_PORT}",
            updated,
        )
        updated = re.sub(
            rf"(?i)((?:port|cdp|debug|chrome|browser|playwright|remote|host|url|socket)"
            rf"[\w.-]*\s*[:=]\s*[\"']?){OLD_PORT}\b",
            rf"\g<1>{NEW_PORT}",
            updated,
        )

        if PORT_CONTEXT_RE.search(updated):
            updated = STANDALONE_9223_RE.sub(NEW_PORT, updated)

        changed_lines.append(updated)

    return "".join(changed_lines)


def decode_text(raw: bytes) -> tuple[str, str] | None:
    if b"\x00" in raw[:4096] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return None
    for encoding in TEXT_ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return None


def purge_binary(raw: bytes) -> bytes:
    updated = raw.replace(OLD_IP.encode("ascii"), NEW_IP.encode("ascii"))
    updated = updated.replace(f":{OLD_PORT}".encode(), f":{NEW_PORT}".encode())
    updated = updated.replace(f"={OLD_PORT}".encode(), f"={NEW_PORT}".encode())
    updated = updated.replace(f'"{OLD_PORT}"'.encode(), f'"{NEW_PORT}"'.encode())
    updated = updated.replace(f"'{OLD_PORT}'".encode(), f"'{NEW_PORT}'".encode())
    return updated


def is_sqlite_file(path: Path, raw: bytes) -> bool:
    return raw.startswith(SQLITE_HEADER) or path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}


def qident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def purge_sqlite(path: Path) -> bool:
    changed = False
    try:
        conn = sqlite3.connect(path)
        conn.text_factory = str
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for (table,) in tables:
            columns = conn.execute(f"PRAGMA table_info({qident(table)})").fetchall()
            for column in columns:
                col_name = column[1]
                col_type = str(column[2] or "").upper()
                if col_type and not any(t in col_type for t in ("TEXT", "CHAR", "CLOB", "VARCHAR")):
                    continue
                try:
                    rows = conn.execute(
                        f"SELECT rowid, {qident(col_name)} FROM {qident(table)}"
                    ).fetchall()
                except sqlite3.DatabaseError:
                    continue

                for rowid, value in rows:
                    if not isinstance(value, str):
                        continue
                    new_value = purge_text(value)
                    if new_value != value:
                        if not changed:
                            backup_once(path)
                        conn.execute(
                            f"UPDATE {qident(table)} SET {qident(col_name)}=? WHERE rowid=?",
                            (new_value, rowid),
                        )
                        changed = True
        if changed:
            conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[WARN] SQLite purge skipped for {rel(path)}: {exc}")
        return False
    return changed


def purge_regular_file(path: Path) -> bool:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        print(f"[WARN] Could not read {rel(path)}: {exc}")
        return False

    if OLD_IP.encode() not in raw and OLD_PORT.encode() not in raw:
        return False

    if is_sqlite_file(path, raw):
        if purge_sqlite(path):
            print(f"[DB]   {rel(path)}")
            return True
        return False

    decoded = decode_text(raw)
    if decoded is not None:
        text, encoding = decoded
        updated = purge_text(text)
        if updated != text:
            backup_once(path)
            path.write_bytes(updated.encode(encoding))
            print(f"[TEXT] {rel(path)}")
            return True
        return False

    updated_raw = purge_binary(raw)
    if updated_raw != raw:
        backup_once(path)
        path.write_bytes(updated_raw)
        print(f"[BIN]  {rel(path)}")
        return True
    return False


def normalize_json_value(value: Any, key_hint: str = "") -> Any:
    key_lower = key_hint.lower()
    if isinstance(value, dict):
        return {k: normalize_json_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_json_value(v, key_hint) for v in value]
    if isinstance(value, str):
        updated = purge_text(value)
        if updated == OLD_PORT and any(word in key_lower for word in ("port", "cdp", "debug", "browser")):
            return NEW_PORT
        return updated
    if isinstance(value, int) and value == 9223 and any(
        word in key_lower for word in ("port", "cdp", "debug", "browser")
    ):
        return 9222
    return value


def purge_special_json(filename: str) -> bool:
    path = ROOT / filename
    if not path.exists():
        return False
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        normalized = normalize_json_value(data)
        rendered = json.dumps(normalized, indent=2) + "\n"
        rendered = purge_text(rendered)
        if rendered != raw:
            backup_once(path)
            path.write_text(rendered, encoding="utf-8")
            print(f"[JSON] {filename}")
            return True
        path.write_text(raw, encoding="utf-8")
        print(f"[JSON] {filename} checked")
        return False
    except Exception:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
            updated = purge_text(raw)
            if updated != raw:
                backup_once(path)
                path.write_text(updated, encoding="utf-8")
                print(f"[JSON] {filename}")
                return True
        except OSError as exc:
            print(f"[WARN] Could not normalize {filename}: {exc}")
    return False


def should_delete_cache_dir(path: Path) -> bool:
    name = path.name.lower()
    if name == "__pycache__":
        return True
    if name in {".playwright", "ms-playwright", "playwright-cache"}:
        return True
    if "playwright" in name and any(parent.name.lower() in {".cache", "cache", "caches"} for parent in path.parents):
        return True
    return False


def delete_caches() -> tuple[int, int]:
    deleted_dirs = 0
    deleted_files = 0

    for path in sorted(ROOT.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if ".git" in path.parts:
            continue
        try:
            if path.is_file() and path.suffix.lower() in {".pyc", ".pyo"}:
                path.unlink()
                deleted_files += 1
            elif path.is_dir() and should_delete_cache_dir(path):
                shutil.rmtree(path)
                deleted_dirs += 1
        except OSError as exc:
            print(f"[WARN] Could not delete cache {rel(path)}: {exc}")

    return deleted_dirs, deleted_files


def iter_files() -> list[Path]:
    files: list[Path] = []
    for current, dirs, filenames in os.walk(ROOT):
        current_path = Path(current)
        dirs[:] = [
            d for d in dirs
            if d not in SKIP_DIR_NAMES and not (current_path / d).is_symlink()
        ]
        for filename in filenames:
            path = current_path / filename
            if path.name == Path(__file__).name:
                continue
            if path.is_symlink() or ".git" in path.parts or ".nuclear_purge_backups" in path.parts:
                continue
            files.append(path)
    return files


def main() -> int:
    print("=" * 72)
    print("VcaniTrade Nuclear Purge")
    print("=" * 72)
    print(f"Root: {ROOT}")
    print(f"Backup folder: {BACKUP_DIR}")
    print()

    edited = 0
    scanned = 0
    for path in iter_files():
        scanned += 1
        if purge_regular_file(path):
            edited += 1

    for filename in ("trading_settings.json", "config_coordinates.json"):
        if purge_special_json(filename):
            edited += 1

    cache_dirs, cache_files = delete_caches()

    print()
    print("=" * 72)
    print("Purge complete")
    print("=" * 72)
    print(f"Files scanned:       {scanned}")
    print(f"Files edited:        {edited}")
    print(f"Cache dirs deleted:  {cache_dirs}")
    print(f"Bytecode deleted:    {cache_files}")
    print(f"Backups:             {BACKUP_DIR if backed_up else 'none needed'}")
    print()
    print("Next:")
    print("1. Close all running bot / Chrome debug processes.")
    print("2. Start Chrome with --remote-debugging-port=9222.")
    print("3. Run: python main.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
