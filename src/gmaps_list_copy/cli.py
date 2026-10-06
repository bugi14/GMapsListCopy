from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .importer import ImportProblem, load_saved_lists
from .models import SavedList


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gmaps-list-copy",
        description="Inspect saved-place lists from a Google Takeout export.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List discovered saved collections.")
    list_parser.add_argument("input", type=Path, help="Takeout ZIP, directory, or CSV.")
    list_parser.add_argument("--json", action="store_true", help="Emit JSON output.")

    show_parser = subparsers.add_parser("show", help="Show the items in one collection.")
    show_parser.add_argument("input", type=Path, help="Takeout ZIP, directory, or CSV.")
    show_parser.add_argument("list_id", help="Identifier printed by the list command.")
    show_parser.add_argument("--json", action="store_true", help="Emit JSON output.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        saved_lists = load_saved_lists(args.input)
        if args.command == "list":
            _print_lists(saved_lists, as_json=args.json)
        else:
            selected = _select_list(saved_lists, args.list_id)
            _print_list(selected, as_json=args.json)
        return 0
    except ImportProblem as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _select_list(saved_lists: tuple[SavedList, ...], identifier: str) -> SavedList:
    for saved_list in saved_lists:
        if saved_list.identifier == identifier:
            return saved_list
    available = ", ".join(item.identifier for item in saved_lists)
    raise ImportProblem(f"Unknown list identifier '{identifier}'. Available: {available}")


def _print_lists(saved_lists: tuple[SavedList, ...], *, as_json: bool) -> None:
    if as_json:
        payload = [
            {
                "id": item.identifier,
                "name": item.name,
                "description": item.description,
                "source": item.source,
                "place_count": len(item.places),
                "warnings": list(item.warnings),
            }
            for item in saved_lists
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    for item in saved_lists:
        suffix = "place" if len(item.places) == 1 else "places"
        print(f"{item.identifier}\t{item.name}\t{len(item.places)} {suffix}")
        for warning in item.warnings:
            print(f"  warning: {warning}", file=sys.stderr)


def _print_list(saved_list: SavedList, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(saved_list.to_dict(), ensure_ascii=False, indent=2))
        return

    print(f"{saved_list.name} ({saved_list.identifier})")
    if saved_list.description:
        print(saved_list.description)
    print(f"Source: {saved_list.source}")
    print()

    for index, place in enumerate(saved_list.places, start=1):
        print(f"{index}. {place.title}")
        print(f"   {place.url}")
        if place.note:
            print(f"   Note: {place.note}")
        if place.tags:
            print(f"   Tags: {', '.join(place.tags)}")
        if place.comment:
            print(f"   Comment: {place.comment}")

    if not saved_list.places:
        print("No readable saved items were found.")
    for warning in saved_list.warnings:
        print(f"warning: {warning}", file=sys.stderr)
