from __future__ import annotations

import hashlib
import json
import os
import re
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from .models import SavedList, SavedPlace

_STATE_VERSION = 1
_SAVED_PAGE = "https://www.google.com/maps/saved"
_FINAL_STATUSES = frozenset({"saved", "skipped"})


class WorkflowProblem(ValueError):
    """An actionable problem with an assisted copy session."""


@dataclass(frozen=True, slots=True)
class CopySummary:
    saved: int
    skipped: int
    failed: int
    pending: int
    duplicates: int
    state_path: Path

    @property
    def complete(self) -> bool:
        return self.pending == 0 and self.failed == 0


def default_state_path(saved_list: SavedList, destination: str) -> Path:
    destination_slug = _slug(destination) or "destination"
    return Path(".gmaps-list-copy") / f"{saved_list.identifier}-{destination_slug}.json"


def run_assisted_copy(
    saved_list: SavedList,
    destination: str,
    state_path: Path,
    *,
    open_browser: bool = True,
    input_fn: Callable[[str], str] = input,
    output: TextIO,
) -> CopySummary:
    """Guide a user through saving every unique place and checkpoint progress."""

    destination = destination.strip()
    if not destination:
        raise WorkflowProblem("Destination list name cannot be empty.")

    unique_places, duplicate_count = _unique_places(saved_list.places)
    if not unique_places:
        raise WorkflowProblem("The selected source list contains no readable places to copy.")

    state = _load_or_create_state(state_path, saved_list, destination)
    results = state["results"]

    print(f"Source: {saved_list.name}", file=output)
    print(f"Destination: {destination}", file=output)
    print(f"Unique places: {len(unique_places)}", file=output)
    if duplicate_count:
        print(f"Duplicate source URLs ignored: {duplicate_count}", file=output)
    print(f"Progress file: {state_path}", file=output)
    print(file=output)
    print(
        "Google does not provide a supported saved-list write API. This assisted "
        "workflow keeps you in control of every save action.",
        file=output,
    )

    if not state["destination_confirmed"]:
        if open_browser:
            _open_url(_SAVED_PAGE)
        response = input_fn(
            f"Create a Google Maps list named '{destination}', then press Enter "
            "to continue (or type q to stop): "
        ).strip().casefold()
        if response == "q":
            _write_state(state_path, state)
            return _summary(unique_places, results, duplicate_count, state_path)
        state["destination_confirmed"] = True
        _write_state(state_path, state)

    for index, place in enumerate(unique_places, start=1):
        key = _place_key(place)
        existing = results.get(key, {})
        if existing.get("status") in _FINAL_STATUSES:
            continue

        print(file=output)
        print(f"[{index}/{len(unique_places)}] {place.title}", file=output)
        print(place.url, file=output)
        if place.note:
            print(f"Note: {place.note}", file=output)
        if place.tags:
            print(f"Tags: {', '.join(place.tags)}", file=output)
        if place.comment:
            print(f"Comment: {place.comment}", file=output)

        if open_browser:
            _open_url(place.url)

        action = _prompt_for_result(input_fn)
        if action == "quit":
            _write_state(state_path, state)
            return _summary(unique_places, results, duplicate_count, state_path)

        results[key] = {
            "status": action,
            "title": place.title,
            "url": place.url,
            "source_row": place.source_row,
        }
        _write_state(state_path, state)

    return _summary(unique_places, results, duplicate_count, state_path)


def print_summary(summary: CopySummary, output: TextIO) -> None:
    print(file=output)
    print("Copy summary", file=output)
    print(f"  Saved: {summary.saved}", file=output)
    print(f"  Skipped: {summary.skipped}", file=output)
    print(f"  Failed: {summary.failed}", file=output)
    print(f"  Pending: {summary.pending}", file=output)
    print(f"  Duplicate source URLs ignored: {summary.duplicates}", file=output)
    print(f"  Progress file: {summary.state_path}", file=output)


def _load_or_create_state(
    path: Path, saved_list: SavedList, destination: str
) -> dict[str, object]:
    expected = {
        "version": _STATE_VERSION,
        "source": saved_list.source,
        "list_id": saved_list.identifier,
        "destination": destination,
    }
    if not path.exists():
        return {
            **expected,
            "destination_confirmed": False,
            "results": {},
        }

    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowProblem(f"Could not read progress file {path}: {exc}") from exc

    if not isinstance(state, dict):
        raise WorkflowProblem(f"Progress file is not a JSON object: {path}")
    mismatches = [key for key, value in expected.items() if state.get(key) != value]
    if mismatches:
        fields = ", ".join(mismatches)
        raise WorkflowProblem(
            f"Progress file {path} belongs to a different copy session "
            f"(mismatched: {fields}). Choose another --state path."
        )
    if not isinstance(state.get("results"), dict):
        raise WorkflowProblem(f"Progress file has invalid results data: {path}")
    if not isinstance(state.get("destination_confirmed"), bool):
        raise WorkflowProblem(f"Progress file has invalid destination state: {path}")
    return state


def _write_state(path: Path, state: dict[str, object]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except OSError as exc:
        raise WorkflowProblem(f"Could not write progress file {path}: {exc}") from exc


def _unique_places(places: tuple[SavedPlace, ...]) -> tuple[tuple[SavedPlace, ...], int]:
    unique: list[SavedPlace] = []
    seen_urls: set[str] = set()
    for place in places:
        normalized_url = place.url.strip()
        if normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        unique.append(place)
    return tuple(unique), len(places) - len(unique)


def _place_key(place: SavedPlace) -> str:
    return hashlib.sha256(place.url.strip().encode("utf-8")).hexdigest()[:16]


def _prompt_for_result(input_fn: Callable[[str], str]) -> str:
    choices = {
        "s": "saved",
        "k": "skipped",
        "f": "failed",
        "q": "quit",
    }
    while True:
        response = input_fn(
            "Save this place to the destination list, then choose "
            "[s]aved, s[k]ipped, [f]ailed, or [q]uit: "
        ).strip().casefold()
        if response in choices:
            return choices[response]


def _summary(
    places: tuple[SavedPlace, ...],
    results: dict[str, object],
    duplicate_count: int,
    state_path: Path,
) -> CopySummary:
    counts = {"saved": 0, "skipped": 0, "failed": 0}
    for result in results.values():
        if not isinstance(result, dict):
            continue
        status = result.get("status")
        if status in counts:
            counts[status] += 1
    handled = counts["saved"] + counts["skipped"]
    pending = max(0, len(places) - handled - counts["failed"])
    return CopySummary(
        saved=counts["saved"],
        skipped=counts["skipped"],
        failed=counts["failed"],
        pending=pending,
        duplicates=duplicate_count,
        state_path=state_path,
    )


def _open_url(url: str) -> None:
    if not webbrowser.open(url, new=2):
        raise WorkflowProblem(f"Could not open a browser for: {url}")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
