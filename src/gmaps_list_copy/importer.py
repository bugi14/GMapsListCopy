from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .models import SavedList, SavedPlace

_MAX_CSV_BYTES = 50 * 1024 * 1024
_REQUIRED_FIELDS = frozenset({"title", "item_content_url"})
_KNOWN_FIELDS = frozenset(
    {
        "collection_description",
        "title",
        "note",
        "item_content_url",
        "tags",
        "comment",
    }
)


class ImportProblem(ValueError):
    """An actionable problem with the supplied Takeout input."""


@dataclass(frozen=True, slots=True)
class _CsvSource:
    name: str
    data: bytes


def load_saved_lists(input_path: str | Path) -> tuple[SavedList, ...]:
    """Load saved collections from a Takeout directory, ZIP, or CSV file."""

    path = Path(input_path).expanduser()
    if not path.exists():
        raise ImportProblem(f"Takeout input does not exist: {path}")

    sources = tuple(_iter_csv_sources(path))
    if not sources:
        raise ImportProblem(
            "No CSV files were found. In Google Takeout, select the 'Saved' "
            "product and provide its downloaded ZIP or extracted directory."
        )

    lists: list[SavedList] = []
    rejected: list[str] = []
    for source in sorted(sources, key=lambda item: item.name.casefold()):
        try:
            lists.append(_parse_collection(source))
        except ImportProblem as exc:
            rejected.append(f"{source.name}: {exc}")

    if not lists:
        detail = "\n".join(f"- {message}" for message in rejected)
        raise ImportProblem(f"No valid Saved collection CSV files were found:\n{detail}")

    return tuple(lists)


def _iter_csv_sources(path: Path) -> Iterator[_CsvSource]:
    if path.is_dir():
        for candidate in path.rglob("*.csv"):
            if candidate.is_file():
                size = candidate.stat().st_size
                _check_size(candidate.as_posix(), size)
                yield _CsvSource(candidate.relative_to(path).as_posix(), candidate.read_bytes())
        return

    if path.suffix.casefold() == ".zip":
        try:
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    if member.is_dir() or not member.filename.casefold().endswith(".csv"):
                        continue
                    _check_size(member.filename, member.file_size)
                    yield _CsvSource(member.filename, archive.read(member))
        except zipfile.BadZipFile as exc:
            raise ImportProblem(f"Not a valid ZIP archive: {path}") from exc
        return

    if path.suffix.casefold() == ".csv":
        size = path.stat().st_size
        _check_size(path.name, size)
        yield _CsvSource(path.name, path.read_bytes())
        return

    raise ImportProblem("Input must be a Google Takeout ZIP, directory, or CSV file.")


def _check_size(name: str, size: int) -> None:
    if size > _MAX_CSV_BYTES:
        raise ImportProblem(
            f"CSV is larger than the supported 50 MiB safety limit: {name}"
        )


def _parse_collection(source: _CsvSource) -> SavedList:
    try:
        text = source.data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportProblem("file is not UTF-8 text") from exc

    rows = list(csv.reader(io.StringIO(text, newline="")))
    header_index = _find_header(rows)
    if header_index is None:
        raise ImportProblem(
            "could not find a header containing 'title' and 'item_content_url'"
        )

    description = _description_before_header(rows[:header_index])
    header = [_normalize_field(value) for value in rows[header_index]]
    duplicates = _duplicates(header)
    if duplicates:
        raise ImportProblem(f"duplicate CSV columns: {', '.join(sorted(duplicates))}")

    warnings: list[str] = []
    unknown = [field for field in header if field and field not in _KNOWN_FIELDS]
    if unknown:
        warnings.append(f"Ignored unrecognized columns: {', '.join(unknown)}")

    places: list[SavedPlace] = []
    for row_number, values in enumerate(rows[header_index + 1 :], start=header_index + 2):
        if not any(value.strip() for value in values):
            continue
        record = {
            field: values[index].strip() if index < len(values) else ""
            for index, field in enumerate(header)
            if field
        }
        title = record.get("title", "")
        url = record.get("item_content_url", "")
        if not title or not url:
            missing = "title" if not title else "item_content_url"
            warnings.append(f"Skipped row {row_number}: missing {missing}")
            continue
        places.append(
            SavedPlace(
                title=title,
                url=url,
                note=record.get("note", ""),
                tags=_split_tags(record.get("tags", "")),
                comment=record.get("comment", ""),
                source_row=row_number,
            )
        )

    if not places:
        warnings.append("This collection contains no readable saved items.")

    source_path = PurePosixPath(source.name)
    name = source_path.stem.strip() or "Untitled list"
    return SavedList(
        identifier=_identifier(name, source.name),
        name=name,
        description=description,
        source=source.name,
        places=tuple(places),
        warnings=tuple(warnings),
    )


def _find_header(rows: Iterable[list[str]]) -> int | None:
    for index, row in enumerate(rows):
        normalized = {_normalize_field(value) for value in row}
        if _REQUIRED_FIELDS <= normalized:
            return index
    return None


def _description_before_header(rows: Iterable[list[str]]) -> str:
    values = [cell.strip() for row in rows for cell in row if cell.strip()]
    return "\n".join(values)


def _normalize_field(value: str) -> str:
    return re.sub(r"[\s-]+", "_", value.strip().casefold())


def _duplicates(values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if not value:
            continue
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def _split_tags(value: str) -> tuple[str, ...]:
    return tuple(tag.strip() for tag in value.split(";") if tag.strip())


def _identifier(name: str, source: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "list"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"
