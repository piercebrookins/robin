from __future__ import annotations

import hashlib
from pathlib import Path

import fitz
import pandas as pd

from .config import WorkspaceConfig
from .schemas import FileIndexRecord


class WorkspaceViolation(ValueError):
    pass


class Workspace:
    def __init__(self, config: WorkspaceConfig):
        self.config = config
        self.root = config.root.resolve()
        self.source = self.root / config.source_dir
        self.generated = self.root / config.generated_dir
        self.sessions = self.root / config.sessions_dir
        for directory in (self.source, self.generated, self.sessions, self.root / "cache"):
            directory.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative: str | Path) -> Path:
        target = (self.root / relative).resolve()
        if not target.is_relative_to(self.root):
            raise WorkspaceViolation(f"Path escapes workspace: {relative}")
        return target

    def generated_task_dir(self, task_id: str) -> Path:
        path = self.generated / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def list_source_files(self) -> list[Path]:
        max_bytes = self.config.max_file_size_mb * 1024 * 1024
        files: list[Path] = []
        for path in self.source.rglob("*"):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(self.root):
                continue
            if path.suffix.lower() not in self.config.allowed_extensions:
                continue
            if path.stat().st_size <= max_bytes:
                files.append(path)
        return sorted(files)

    def index(self) -> list[FileIndexRecord]:
        return [self.inspect_file(path) for path in self.list_source_files()]

    def inspect_file(self, path: Path) -> FileIndexRecord:
        rel = path.relative_to(self.root).as_posix()
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        suffix = path.suffix.lower()
        columns: list[str] = []
        summary = ""
        if suffix == ".csv":
            df = pd.read_csv(path, nrows=20)
            columns = [str(c) for c in df.columns]
            summary = f"CSV with columns {', '.join(columns)}"
        elif suffix == ".xlsx":
            book = pd.ExcelFile(path)
            parts = []
            for sheet in book.sheet_names:
                df = pd.read_excel(path, sheet_name=sheet, nrows=5)
                sheet_cols = [str(c) for c in df.columns]
                columns.extend(sheet_cols)
                parts.append(f"{sheet}: {', '.join(sheet_cols)}")
            summary = "Workbook sheets: " + "; ".join(parts)
        elif suffix == ".pdf":
            doc = fitz.open(path)
            text = " ".join(page.get_text("text")[:500] for page in doc)
            summary = f"PDF with {doc.page_count} pages. {text[:800]}"
        return FileIndexRecord(
            relative_path=rel,
            file_type=suffix.removeprefix("."),
            sha256=digest,
            size_bytes=len(data),
            summary=summary,
            columns=sorted(set(columns)),
        )

    def search(self, query: str, records: list[FileIndexRecord]) -> list[FileIndexRecord]:
        tokens = [token.lower() for token in query.replace("_", " ").split() if len(token) > 2]

        def score(record: FileIndexRecord) -> int:
            haystack = f"{record.relative_path} {record.summary} {' '.join(record.columns)}".lower()
            return sum(1 for token in tokens if token in haystack)

        ranked = sorted(records, key=lambda record: (score(record), record.relative_path), reverse=True)
        return [record for record in ranked if score(record) > 0] or ranked
