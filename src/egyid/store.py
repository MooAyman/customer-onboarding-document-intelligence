from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from egyid.nid import nids_from_extraction

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "artifacts" / "egyid.sqlite"
DEFAULT_UPLOADS = ROOT / "artifacts" / "uploads"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class DuplicateSubmissionError(Exception):
    """Front image, back image, and/or national ID already belong to another application."""

    def __init__(self, kinds: list[str], nid: str = "") -> None:
        self.kinds = tuple(item for item in dict.fromkeys(kinds) if item in {"front", "back", "nid"})
        self.nid = nid
        super().__init__(",".join(self.kinds))


class DuplicateNidError(DuplicateSubmissionError):
    def __init__(self, nid: str = "") -> None:
        super().__init__(["nid"], nid=nid)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _dump(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _load(text: str | None, default: Any = None) -> Any:
    if not text:
        return default
    return json.loads(text)


class Store:
    def __init__(self, db_path: Path | None = None, upload_dir: Path | None = None) -> None:
        self.db_path = Path(db_path or DEFAULT_DB)
        self.upload_dir = Path(upload_dir or DEFAULT_UPLOADS)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS applications (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                stage TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                processed_at TEXT,
                reviewed_at TEXT,
                front_path TEXT NOT NULL,
                back_path TEXT NOT NULL,
                front_filename TEXT,
                back_filename TEXT,
                extraction_json TEXT NOT NULL,
                corrections_json TEXT,
                review_json TEXT,
                automated_decision TEXT,
                automated_reasons_json TEXT,
                reviewer_action TEXT,
                reviewer_reason TEXT,
                customer_message TEXT NOT NULL,
                customer_id TEXT
            )
            """
        )
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(applications)")}
        if "customer_id" not in columns:
            self._conn.execute("ALTER TABLE applications ADD COLUMN customer_id TEXT")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_applications_customer ON applications(customer_id)"
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS application_nids (
                nid TEXT PRIMARY KEY,
                application_id TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_application_nids_app ON application_nids(application_id)"
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS application_images (
                sha256 TEXT PRIMARY KEY,
                application_id TEXT NOT NULL,
                side TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_application_images_app ON application_images(application_id)"
        )
        self._backfill_nids()
        self._backfill_images()
        self._conn.commit()

    def _backfill_nids(self) -> None:
        existing = {
            row["nid"]: row["application_id"]
            for row in self._conn.execute("SELECT nid, application_id FROM application_nids")
        }
        rows = self._conn.execute("SELECT id, extraction_json FROM applications").fetchall()
        for row in rows:
            for nid in nids_from_extraction(_load(row["extraction_json"], {})):
                owner = existing.get(nid)
                if owner is None:
                    self._conn.execute(
                        "INSERT INTO application_nids (nid, application_id) VALUES (?, ?)",
                        (nid, row["id"]),
                    )
                    existing[nid] = row["id"]

    def _unique_nids(self, nids: list[str] | None) -> list[str]:
        found: list[str] = []
        for nid in nids or []:
            if nid and nid not in found:
                found.append(nid)
        return found

    def _backfill_images(self) -> None:
        existing = {
            row["sha256"]: row["application_id"]
            for row in self._conn.execute("SELECT sha256, application_id FROM application_images")
        }
        rows = self._conn.execute(
            "SELECT id, front_path, back_path FROM applications"
        ).fetchall()
        for row in rows:
            for side, column in (("front", "front_path"), ("back", "back_path")):
                path = Path(row[column] or "")
                if not path.is_file():
                    continue
                digest = sha256_hex(path.read_bytes())
                owner = existing.get(digest)
                if owner is None:
                    self._conn.execute(
                        "INSERT INTO application_images (sha256, application_id, side) VALUES (?, ?, ?)",
                        (digest, row["id"], side),
                    )
                    existing[digest] = row["id"]

    def _duplicate_kinds(
        self,
        nids: list[str],
        images: dict[str, str] | None,
        exclude_id: str | None,
    ) -> list[str]:
        kinds: list[str] = []
        images = images or {}
        for side in ("front", "back"):
            digest = images.get(side) or ""
            if not digest:
                continue
            row = self._conn.execute(
                "SELECT application_id FROM application_images WHERE sha256 = ?",
                (digest,),
            ).fetchone()
            if row and row["application_id"] != exclude_id:
                kinds.append(side)
        for nid in nids:
            row = self._conn.execute(
                "SELECT application_id FROM application_nids WHERE nid = ?",
                (nid,),
            ).fetchone()
            if row and row["application_id"] != exclude_id:
                kinds.append("nid")
                break
        return kinds

    def reject_duplicates(
        self,
        nids: list[str] | None,
        images: dict[str, str] | None,
        exclude_id: str | None,
    ) -> None:
        claimed = self._unique_nids(nids)
        with self._lock:
            kinds = self._duplicate_kinds(claimed, images, exclude_id)
            if kinds:
                raise DuplicateSubmissionError(kinds)

    def _put_nids(self, application_id: str, nids: list[str]) -> None:
        self._conn.execute(
            "DELETE FROM application_nids WHERE application_id = ?",
            (application_id,),
        )
        for nid in nids:
            try:
                self._conn.execute(
                    "INSERT INTO application_nids (nid, application_id) VALUES (?, ?)",
                    (nid, application_id),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateSubmissionError(["nid"], nid=nid) from exc

    def _put_images(self, application_id: str, images: dict[str, str] | None) -> None:
        self._conn.execute(
            "DELETE FROM application_images WHERE application_id = ?",
            (application_id,),
        )
        seen: set[str] = set()
        for side in ("front", "back"):
            digest = (images or {}).get(side) or ""
            if not digest or digest in seen:
                continue
            seen.add(digest)
            try:
                self._conn.execute(
                    "INSERT INTO application_images (sha256, application_id, side) VALUES (?, ?, ?)",
                    (digest, application_id, side),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateSubmissionError([side]) from exc

    def save_image(self, application_id: str, side: str, data: bytes, filename: str | None) -> Path:
        folder = self.upload_dir / application_id
        folder.mkdir(parents=True, exist_ok=True)
        suffix = Path(filename or f"{side}.png").suffix.lower() or ".png"
        if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}:
            suffix = ".png"
        path = folder / f"{side}{suffix}"
        path.write_bytes(data)
        return path

    def create(
        self,
        record: dict[str, Any],
        nids: list[str] | None = None,
        images: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        now = _now()
        record = {
            **record,
            "created_at": record.get("created_at") or now,
            "updated_at": now,
        }
        claimed = self._unique_nids(nids)
        with self._lock:
            try:
                kinds = self._duplicate_kinds(claimed, images, None)
                if kinds:
                    raise DuplicateSubmissionError(kinds)
                self._conn.execute(
                """
                INSERT INTO applications (
                    id, status, stage, created_at, updated_at, processed_at, reviewed_at,
                    front_path, back_path, front_filename, back_filename,
                    extraction_json, corrections_json, review_json,
                    automated_decision, automated_reasons_json,
                    reviewer_action, reviewer_reason, customer_message, customer_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["status"],
                    record["stage"],
                    record["created_at"],
                    record["updated_at"],
                    record.get("processed_at"),
                    record.get("reviewed_at"),
                    str(record["front_path"]),
                    str(record["back_path"]),
                    record.get("front_filename"),
                    record.get("back_filename"),
                    _dump(record["extraction"]),
                    _dump(record.get("corrections")),
                    _dump(record.get("review")),
                    record.get("automated_decision"),
                    _dump(record.get("automated_reasons") or []),
                    record.get("reviewer_action"),
                    record.get("reviewer_reason"),
                    record["customer_message"],
                    record.get("customer_id"),
                ),
            )
                self._put_nids(record["id"], claimed)
                self._put_images(record["id"], images)
                self._conn.commit()
            except DuplicateSubmissionError:
                self._conn.rollback()
                raise
        return self.get(record["id"])

    def update(
        self,
        application_id: str,
        nids: list[str] | None = None,
        images: dict[str, str] | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        mapping = {
            "status": "status",
            "stage": "stage",
            "processed_at": "processed_at",
            "reviewed_at": "reviewed_at",
            "front_path": "front_path",
            "back_path": "back_path",
            "front_filename": "front_filename",
            "back_filename": "back_filename",
            "extraction": "extraction_json",
            "corrections": "corrections_json",
            "review": "review_json",
            "automated_decision": "automated_decision",
            "automated_reasons": "automated_reasons_json",
            "reviewer_action": "reviewer_action",
            "reviewer_reason": "reviewer_reason",
            "customer_message": "customer_message",
            "customer_id": "customer_id",
        }
        assignments = ["updated_at = ?"]
        values: list[Any] = [_now()]
        json_keys = {"extraction", "corrections", "review", "automated_reasons"}
        for key, column in mapping.items():
            if key not in fields:
                continue
            value = fields[key]
            if key in {"front_path", "back_path"} and value is not None:
                value = str(value)
            if key in json_keys:
                value = _dump(value)
            assignments.append(f"{column} = ?")
            values.append(value)
        values.append(application_id)
        claimed = None if nids is None else self._unique_nids(nids)
        with self._lock:
            try:
                if claimed is not None or images is not None:
                    kinds = self._duplicate_kinds(claimed or [], images, application_id)
                    if kinds:
                        raise DuplicateSubmissionError(kinds)
                self._conn.execute(
                    f"UPDATE applications SET {', '.join(assignments)} WHERE id = ?",
                    values,
                )
                if claimed is not None:
                    self._put_nids(application_id, claimed)
                if images is not None:
                    self._put_images(application_id, images)
                self._conn.commit()
            except DuplicateSubmissionError:
                self._conn.rollback()
                raise
        found = self.get(application_id)
        if found is None:
            raise KeyError(application_id)
        return found

    def get(self, application_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM applications WHERE id = ?", (application_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list_all(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM applications WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM applications ORDER BY created_at DESC"
                ).fetchall()
        return [self._row(row) for row in rows]

    def _row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "status": row["status"],
            "stage": row["stage"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "processed_at": row["processed_at"],
            "reviewed_at": row["reviewed_at"],
            "front_path": row["front_path"],
            "back_path": row["back_path"],
            "front_filename": row["front_filename"],
            "back_filename": row["back_filename"],
            "extraction": _load(row["extraction_json"], {}),
            "corrections": _load(row["corrections_json"], {}),
            "review": _load(row["review_json"]),
            "automated_decision": row["automated_decision"],
            "automated_reasons": _load(row["automated_reasons_json"], []),
            "reviewer_action": row["reviewer_action"],
            "reviewer_reason": row["reviewer_reason"],
            "customer_message": row["customer_message"],
            "customer_id": row["customer_id"] if "customer_id" in row.keys() else None,
        }

    def list_for_customer(self, customer_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM applications WHERE customer_id = ? ORDER BY created_at DESC",
                (customer_id,),
            ).fetchall()
        return [self._row(row) for row in rows]


_STORE: Store | None = None


def get_store() -> Store:
    global _STORE
    if _STORE is None:
        _STORE = Store()
    return _STORE


def set_store(store: Store | None) -> None:
    global _STORE
    _STORE = store
