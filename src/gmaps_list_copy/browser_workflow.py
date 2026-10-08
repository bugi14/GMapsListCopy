from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import quote, urljoin

_MAPS_HOME = "https://www.google.com/maps/?hl=en"
_GOOGLE_SIGN_IN = (
    "https://accounts.google.com/ServiceLogin?service=local&hl=en&continue="
    f"{quote(_MAPS_HOME, safe='')}"
)
_STATE_VERSION = 1
_PROFILE_DIR = Path(".gmaps-list-copy/chrome-profile")
_STATE_DIR = Path(".gmaps-list-copy/sessions")
_DIAGNOSTIC_DIR = Path(".gmaps-list-copy/diagnostics")
_LOG_DIR = Path(".gmaps-list-copy/logs")
_AUTH_MARKER = ".google-session-verified"


class WorkflowProblem(RuntimeError):
    """An actionable problem with the browser copy workflow."""


@dataclass(frozen=True, slots=True)
class MapsList:
    name: str
    href: str | None


@dataclass(frozen=True, slots=True)
class MapsPlace:
    name: str
    url: str


@dataclass(frozen=True, slots=True)
class CopySummary:
    source: str
    destination: str
    saved: int
    failed: int
    pending: int
    state_path: Path

    @property
    def complete(self) -> bool:
        return self.failed == 0 and self.pending == 0


def run_browser_copy(
    *,
    profile_path: Path = _PROFILE_DIR,
    state_path: Path | None = None,
    destination_suffix: str = " (copy)",
    input_fn: Callable[[str], str] = input,
    output: TextIO = sys.stdout,
) -> CopySummary:
    """Copy a selected Maps list using a visible, persistent browser session."""

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import Page, sync_playwright
    except ImportError as exc:
        raise WorkflowProblem(
            "Browser automation is not installed. Run 'python -m pip install -e .' "
            "and 'python -m playwright install chromium'."
        ) from exc

    profile_path.mkdir(parents=True, exist_ok=True)
    auth_marker = profile_path / _AUTH_MARKER
    endpoint = _open_regular_chrome(
        profile_path, auth_marker, input_fn=input_fn, output=output
    )
    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.connect_over_cdp(endpoint)
            except PlaywrightError as exc:
                raise WorkflowProblem(
                    "Could not attach automation to the open Google Chrome window."
                ) from exc

            if not browser.contexts:
                browser.close()
                raise WorkflowProblem("The open Chrome window has no browser context.")
            context = browser.contexts[0]
            page: Page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(15_000)
            try:
                _open_saved(page)
                _verify_authentication(page, auth_marker)
                available_lists = _discover_lists(page)
                source = _choose_list(available_lists, input_fn, output)
                _open_list(page, source)
                places = _discover_places(page)
                if not places:
                    raise WorkflowProblem(
                        f"No place links were found in '{source.name}'. "
                        "The list may be empty or the Maps interface may have changed."
                    )

                destination = f"{source.name}{destination_suffix}"
                session_path = state_path or _default_state_path(source.name)
                state = _load_or_create_state(session_path, source, destination, places)
                _ensure_destination(page, destination)

                results: dict[str, dict[str, Any]] = state["results"]
                for index, place in enumerate(places, start=1):
                    key = _place_key(place.url)
                    if results.get(key, {}).get("status") == "saved":
                        continue
                    print(f"[{index}/{len(places)}] Saving {place.name}", file=output)
                    try:
                        _save_place(page, place, destination)
                    except PlaywrightError as exc:
                        results[key] = {
                            "status": "failed",
                            "name": place.name,
                            "url": place.url,
                            "error": str(exc),
                        }
                        _write_state(session_path, state)
                        print(f"  failed: {exc}", file=output)
                        continue

                    results[key] = {
                        "status": "saved",
                        "name": place.name,
                        "url": place.url,
                    }
                    _write_state(session_path, state)

                summary = _summarize(source.name, destination, places, state, session_path)
                browser.close()
                return summary
            except (WorkflowProblem, PlaywrightError) as exc:
                diagnostic = _capture_diagnostic(page)
                browser.close()
                if isinstance(exc, WorkflowProblem):
                    message = str(exc)
                else:
                    message = f"Google Maps browser automation failed: {exc}"
                if diagnostic:
                    message += f" Diagnostic screenshot: {diagnostic}"
                raise WorkflowProblem(message) from exc
    except OSError as exc:
        raise WorkflowProblem(f"Could not create local browser state: {exc}") from exc


def print_summary(summary: CopySummary, output: TextIO = sys.stdout) -> None:
    print(file=output)
    print(f"Source: {summary.source}", file=output)
    print(f"Destination: {summary.destination}", file=output)
    print(f"Saved: {summary.saved}", file=output)
    print(f"Failed: {summary.failed}", file=output)
    print(f"Pending: {summary.pending}", file=output)
    print(f"Progress: {summary.state_path}", file=output)


def _open_saved(page: Any) -> None:
    page.goto(_MAPS_HOME, wait_until="domcontentloaded")
    _dismiss_consent(page)
    saved_button = page.get_by_role("button", name="Saved", exact=True)
    try:
        saved_button.first.wait_for(state="visible")
    except Exception as exc:
        raise WorkflowProblem(
            "Google Maps did not show its Saved navigation button. "
            "The Maps interface may have changed."
        ) from exc
    saved_button.first.click(no_wait_after=True)
    page.wait_for_timeout(1_000)


def _dismiss_consent(page: Any) -> None:
    for label in ("Reject all", "Accept all"):
        button = page.get_by_role("button", name=label, exact=True)
        if button.count() and button.first.is_visible():
            button.first.click()
            page.wait_for_timeout(500)
            return


def _verify_authentication(page: Any, auth_marker: Path) -> None:
    sign_in = page.get_by_text(re.compile(r"^Sign in$", re.IGNORECASE))
    if "accounts.google.com" not in page.url and not (
        sign_in.count() and sign_in.first.is_visible()
    ):
        auth_marker.touch()
        return

    auth_marker.unlink(missing_ok=True)
    raise WorkflowProblem(
        "The saved Google session is missing or expired. Run the command again to "
        "authenticate in a regular Chrome window."
    )


def _open_regular_chrome(
    profile_path: Path,
    auth_marker: Path,
    *,
    input_fn: Callable[[str], str],
    output: TextIO,
) -> str:
    port = _available_local_port()
    start_url = _MAPS_HOME if auth_marker.exists() else _GOOGLE_SIGN_IN
    command = _chrome_command(profile_path, port, start_url)
    if command is None:
        raise WorkflowProblem(
            "Google Chrome is required for authentication but was not found."
        )

    if not auth_marker.exists():
        print(
            "Opening a regular Google Chrome window for secure authentication. Sign "
            "in and wait until Google Maps shows your account avatar.",
            file=output,
        )
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _LOG_DIR / "chrome-auth.log"
    try:
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
    except OSError as exc:
        raise WorkflowProblem(f"Could not open Google Chrome: {exc}") from exc
    endpoint = f"http://127.0.0.1:{port}"
    _wait_for_debugging_endpoint(endpoint, process, log_path)
    if not auth_marker.exists():
        input_fn("Press Enter after Google Maps shows your account avatar: ")
    return endpoint


def _chrome_command(profile_path: Path, port: int, start_url: str) -> list[str] | None:
    arguments = [
        f"--user-data-dir={profile_path.resolve()}",
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-mode",
        "--enable-logging=stderr",
        start_url,
    ]
    if sys.platform == "darwin":
        application = Path("/Applications/Google Chrome.app")
        if application.is_dir():
            return [
                "/usr/bin/open",
                "-n",
                "-a",
                "Google Chrome",
                "--args",
                *arguments,
            ]
        return None

    candidates: list[Path] = []
    if sys.platform == "win32":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(variable)
            if base:
                candidates.append(
                    Path(base) / "Google/Chrome/Application/chrome.exe"
                )
    else:
        for name in ("google-chrome", "google-chrome-stable"):
            resolved = shutil.which(name)
            if resolved:
                candidates.append(Path(resolved))

    executable = next(
        (candidate for candidate in candidates if candidate.is_file()), None
    )
    return [str(executable), *arguments] if executable else None


def _available_local_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_debugging_endpoint(
    endpoint: str, process: subprocess.Popen[Any], log_path: Path
) -> None:
    deadline = time.monotonic() + 15
    version_url = f"{endpoint}/json/version"
    while time.monotonic() < deadline:
        if process.poll() not in (None, 0):
            raise WorkflowProblem(
                f"Google Chrome exited before automation could attach. Log: {log_path}"
            )
        try:
            with urllib.request.urlopen(version_url, timeout=1):
                return
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    raise WorkflowProblem(
        f"Google Chrome did not expose its local automation endpoint. Log: {log_path}"
    )


def _discover_lists(page: Any) -> tuple[MapsList, ...]:
    page.wait_for_timeout(1_000)
    selectors = ", ".join(
        (
            'a[href*="/maps/placelists/list/"]',
            '[role="button"][data-item-id*="saved"]',
            'button[data-item-id*="saved"]',
        )
    )
    candidates = page.locator(selectors)
    found: dict[str, MapsList] = {}
    for index in range(candidates.count()):
        candidate = candidates.nth(index)
        if not candidate.is_visible():
            continue
        text = _clean_list_name(candidate.inner_text())
        if not text or text.casefold() in {"saved", "new list"}:
            continue
        href = candidate.get_attribute("href")
        found.setdefault(text.casefold(), MapsList(text, href))

    list_metadata = page.get_by_text(
        re.compile(
            r"^(?:Private|Shared)\s*[·•]\s*[\d,]+\s+places?$",
            re.IGNORECASE,
        )
    )
    for index in range(list_metadata.count()):
        metadata = list_metadata.nth(index)
        if not metadata.is_visible():
            continue
        details = metadata.evaluate(
            """
            node => {
                const metadataText = (node.innerText || node.textContent || '').trim();
                let container = node.parentElement;
                for (let depth = 0; container && depth < 5; depth += 1) {
                    const lines = (container.innerText || '')
                        .split('\n')
                        .map(line => line.trim())
                        .filter(Boolean);
                    const metadataIndex = lines.indexOf(metadataText);
                    if (metadataIndex > 0 && lines.length <= 6) {
                        const link = container.closest('a[href]') ||
                            container.querySelector('a[href]');
                        return {
                            name: lines[metadataIndex - 1],
                            href: link ? link.getAttribute('href') : null,
                        };
                    }
                    container = container.parentElement;
                }
                return null;
            }
            """
        )
        if not isinstance(details, dict):
            continue
        name = _clean_list_name(str(details.get("name", "")))
        if not name:
            continue
        href = details.get("href")
        found.setdefault(
            name.casefold(),
            MapsList(name, str(href) if href else None),
        )

    if found:
        return tuple(sorted(found.values(), key=lambda item: item.name.casefold()))

    raise WorkflowProblem(
        "No saved lists were discovered. Confirm the browser is signed in and "
        "that Google Maps shows lists under Saved."
    )


def _choose_list(
    choices: tuple[MapsList, ...], input_fn: Callable[[str], str], output: TextIO
) -> MapsList:
    print("Available Google Maps lists:", file=output)
    for index, choice in enumerate(choices, start=1):
        print(f"  {index}. {choice.name}", file=output)
    while True:
        response = input_fn("Select the list to copy by number: ").strip()
        if response.isdigit() and 1 <= int(response) <= len(choices):
            return choices[int(response) - 1]


def _open_list(page: Any, source: MapsList) -> None:
    if source.href:
        page.goto(urljoin(_MAPS_HOME, source.href), wait_until="domcontentloaded")
    else:
        page.get_by_text(source.name, exact=True).first.click()
    page.wait_for_timeout(1_000)


def _discover_places(page: Any) -> tuple[MapsPlace, ...]:
    links = page.locator('a[href*="/maps/place/"]')
    stable_rounds = 0
    previous_count = -1
    for _ in range(100):
        count = links.count()
        if count:
            links.nth(count - 1).scroll_into_view_if_needed()
            page.wait_for_timeout(350)
        if count == previous_count:
            stable_rounds += 1
            if stable_rounds >= 3:
                break
        else:
            stable_rounds = 0
            previous_count = count

    found: dict[str, MapsPlace] = {}
    for index in range(links.count()):
        link = links.nth(index)
        href = link.get_attribute("href")
        if not href:
            continue
        url = urljoin(_MAPS_HOME, href)
        name = (link.get_attribute("aria-label") or link.inner_text() or "Saved place").strip()
        found.setdefault(_canonical_url(url), MapsPlace(name=name.splitlines()[0], url=url))
    return tuple(found.values())


def _ensure_destination(page: Any, destination: str) -> None:
    _open_saved(page)
    existing = page.get_by_text(destination, exact=True)
    if existing.count() and existing.first.is_visible():
        return

    new_list = page.get_by_role("button", name=re.compile(r"^New list$", re.IGNORECASE))
    if not new_list.count():
        new_list = page.get_by_text(re.compile(r"^New list$", re.IGNORECASE))
    new_list.first.click()

    name_input = page.get_by_role("textbox", name=re.compile(r"name", re.IGNORECASE))
    if not name_input.count():
        name_input = page.get_by_placeholder(re.compile(r"name", re.IGNORECASE))
    name_input.first.fill(destination)
    page.get_by_role("button", name="Create", exact=True).click()
    page.get_by_text(destination, exact=True).first.wait_for(state="visible")


def _save_place(page: Any, place: MapsPlace, destination: str) -> None:
    page.goto(place.url, wait_until="domcontentloaded")
    save_button = page.get_by_role(
        "button", name=re.compile(r"^(Save|Saved)$", re.IGNORECASE)
    )
    save_button.first.wait_for(state="visible")
    save_button.first.click()

    dialog = page.get_by_role("dialog")
    dialog.wait_for(state="visible")
    destination_label = dialog.get_by_text(destination, exact=True)
    destination_label.first.wait_for(state="visible")

    checkbox = destination_label.first.locator(
        "xpath=ancestor::*[@role='checkbox'][1]"
    )
    if checkbox.count():
        if checkbox.first.get_attribute("aria-checked") != "true":
            checkbox.first.click()
    else:
        destination_label.first.click()

    done = dialog.get_by_role("button", name="Done", exact=True)
    if done.count() and done.first.is_visible():
        done.first.click()
    else:
        page.keyboard.press("Escape")


def _load_or_create_state(
    path: Path, source: MapsList, destination: str, places: tuple[MapsPlace, ...]
) -> dict[str, Any]:
    expected = {
        "version": _STATE_VERSION,
        "source": source.name,
        "destination": destination,
    }
    if not path.exists():
        state: dict[str, Any] = {
            **expected,
            "places": [{"name": place.name, "url": place.url} for place in places],
            "results": {},
        }
        _write_state(path, state)
        return state

    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowProblem(f"Could not read progress file {path}: {exc}") from exc
    if not isinstance(state, dict) or not isinstance(state.get("results"), dict):
        raise WorkflowProblem(f"Progress file has an invalid structure: {path}")
    mismatched = [key for key, value in expected.items() if state.get(key) != value]
    if mismatched:
        raise WorkflowProblem(
            f"Progress file belongs to another session (mismatched: "
            f"{', '.join(mismatched)}): {path}"
        )
    return state


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _summarize(
    source: str,
    destination: str,
    places: tuple[MapsPlace, ...],
    state: dict[str, Any],
    state_path: Path,
) -> CopySummary:
    statuses = [result.get("status") for result in state["results"].values()]
    saved = statuses.count("saved")
    failed = statuses.count("failed")
    return CopySummary(
        source=source,
        destination=destination,
        saved=saved,
        failed=failed,
        pending=max(0, len(places) - saved - failed),
        state_path=state_path,
    )


def _capture_diagnostic(page: Any) -> Path | None:
    try:
        _DIAGNOSTIC_DIR.mkdir(parents=True, exist_ok=True)
        path = _DIAGNOSTIC_DIR / "latest-failure.png"
        page.screenshot(path=str(path), full_page=True)
        return path
    except Exception:
        return None


def _default_state_path(source: str) -> Path:
    return _STATE_DIR / f"{_slug(source) or 'list'}.json"


def _place_key(url: str) -> str:
    return hashlib.sha256(_canonical_url(url).encode("utf-8")).hexdigest()[:16]


def _canonical_url(url: str) -> str:
    return url.split("?", 1)[0].rstrip("/")


def _clean_list_name(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if not lines:
        return ""
    return re.sub(r"\s+\d+\s+places?$", "", lines[0], flags=re.IGNORECASE).strip()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
