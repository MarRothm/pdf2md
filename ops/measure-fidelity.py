#!/usr/bin/env python3
"""Measure conversion fidelity against the corpus manifest (SC-001, SC-002, FR-004).

Converts every document in `tests/fixtures/corpus/` through a deployed stack, compares
the Markdown against the expectations in `manifest.yaml`, and prints a report with the
figures the spec's success criteria are stated in.

    python3 ops/measure-fidelity.py --base-url http://10.0.0.19:8080

Runs against the real stack on purpose. The stub engine in the test suite proves the
plumbing; only the real engine says anything about fidelity.

Standard library only: this runs on the Mac mini, which installs nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "corpus"
MANIFEST = CORPUS_DIR / "manifest.yaml"

TERMINAL = {"succeeded", "succeeded_suspect", "already_converted", "failed", "timed_out"}
SUCCESSFUL = {"succeeded", "succeeded_suspect", "already_converted"}

# Gates from the spec.
FIRST_ATTEMPT_GATE = 0.90  # SC-001
HEADING_RECALL_GATE = 0.95  # SC-002
TABLE_RECALL_GATE = 0.90  # SC-002


@dataclass
class Expectation:
    file: str
    traits: list[str] = field(default_factory=list)
    pages: int | None = None
    headings: int = 0
    tables: int = 0
    figures: int = 0
    must_contain: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class Measurement:
    expectation: Expectation
    status: str = "not_run"
    seconds: float = 0.0
    headings_found: int = 0
    tables_found: int = 0
    figures_found: int = 0
    missing_strings: list[str] = field(default_factory=list)
    failure_reason: str | None = None

    @property
    def converted(self) -> bool:
        return self.status in SUCCESSFUL

    def ratio(self, found: int, expected: int) -> float | None:
        if expected == 0:
            return None
        return min(found / expected, 1.0)


# --- the manifest ---------------------------------------------------------
# A deliberately small YAML reader: the manifest is a fixed, flat shape and the Mac mini
# has no PyYAML. Anything it cannot parse is reported rather than guessed at.


def load_manifest(path: Path) -> list[Expectation]:
    documents: list[dict] = []
    current: dict | None = None
    list_key: str | None = None

    for raw in path.read_text().splitlines():
        line = raw.split(" #")[0].rstrip() if " #" in raw else raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()

        if stripped == "documents:":
            continue
        if stripped.startswith("- file:"):
            current = {"file": _scalar(stripped.split(":", 1)[1])}
            documents.append(current)
            list_key = None
            continue
        if current is None:
            continue

        if stripped.startswith("- "):
            if list_key:
                current.setdefault(list_key, []).append(_scalar(stripped[2:]))
            continue

        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if value in ("", "[]"):
            list_key = key
            current.setdefault(key, [])
        elif value.startswith("[") and value.endswith("]"):
            list_key = None
            current[key] = [_scalar(item) for item in value[1:-1].split(",") if item.strip()]
        else:
            list_key = None
            current[key] = _scalar(value)

    expectations = []
    for document in documents:
        expectations.append(
            Expectation(
                file=str(document["file"]),
                traits=[str(trait) for trait in document.get("traits", [])],
                pages=_int_or_none(document.get("pages")),
                headings=int(document.get("headings") or 0),
                tables=int(document.get("tables") or 0),
                figures=int(document.get("figures") or 0),
                must_contain=[str(item) for item in document.get("must_contain", [])],
                notes=str(document.get("notes", "")),
            )
        )
    return expectations


def _scalar(value: str) -> str:
    value = value.strip()
    if value[:1] in {'"', "'"} and value[-1:] == value[:1]:
        return value[1:-1]
    return value


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


# --- counting what the Markdown contains ----------------------------------

HEADING = re.compile(r"^#{1,6}\s+\S", re.M)
TABLE_DIVIDER = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", re.M)
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)|<!--\s*image\s*-->", re.I)


def count_headings(markdown: str) -> int:
    return len(HEADING.findall(markdown))


def count_tables(markdown: str) -> int:
    """A Markdown table is a header row followed by a divider row."""
    return len(TABLE_DIVIDER.findall(markdown))


def count_figures(markdown: str) -> int:
    return len(IMAGE.findall(markdown))


def figures_are_in_position(markdown: str) -> bool:
    """FR-004: a figure must sit in the flow, not be dumped at the end.

    A conversion that appends every image after the text leaves the last figure with no
    text following it — the cheap check that catches exactly that failure.
    """
    matches = list(IMAGE.finditer(markdown))
    if not matches:
        return True
    trailing = markdown[matches[-1].end() :]
    return bool(trailing.strip())


# --- talking to the stack -------------------------------------------------


class Stack:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def upload(self, path: Path) -> str:
        boundary = uuid.uuid4().hex
        body = b"".join(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="files"; filename="{path.name}"\r\n'.encode(),
                b"Content-Type: application/pdf\r\n\r\n",
                path.read_bytes(),
                f"\r\n--{boundary}--\r\n".encode(),
            ]
        )
        request = urllib.request.Request(
            f"{self.base_url}/api/uploads",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.load(response)
        if payload["rejected"]:
            raise RuntimeError(payload["rejected"][0]["reason"])
        return payload["accepted"][0]["job_id"]

    def job(self, job_id: str) -> dict:
        with urllib.request.urlopen(
            f"{self.base_url}/api/jobs/{job_id}", timeout=self.timeout
        ) as response:
            return json.load(response)

    def markdown(self, job_id: str) -> str:
        with urllib.request.urlopen(
            f"{self.base_url}/api/jobs/{job_id}/markdown", timeout=self.timeout
        ) as response:
            return response.read().decode("utf-8")

    def wait(self, job_id: str, deadline_seconds: float, poll_seconds: float = 3.0) -> dict:
        started = time.monotonic()
        while True:
            job = self.job(job_id)
            if job["status"] in TERMINAL:
                return job
            if time.monotonic() - started > deadline_seconds:
                job["status"] = "gave_up_waiting"
                return job
            time.sleep(poll_seconds)


# --- measuring ------------------------------------------------------------


def measure(stack: Stack, expectation: Expectation, deadline: float) -> Measurement:
    measurement = Measurement(expectation=expectation)
    path = CORPUS_DIR / expectation.file
    if not path.is_file():
        measurement.status = "missing_from_corpus"
        return measurement

    started = time.monotonic()
    try:
        job_id = stack.upload(path)
    except (urllib.error.URLError, RuntimeError) as error:
        measurement.status = "rejected"
        measurement.failure_reason = str(error)
        return measurement

    job = stack.wait(job_id, deadline_seconds=deadline)
    measurement.seconds = time.monotonic() - started
    measurement.status = job["status"]
    measurement.failure_reason = job.get("failure_reason")
    if not measurement.converted:
        return measurement

    markdown = stack.markdown(job_id)
    measurement.headings_found = count_headings(markdown)
    measurement.tables_found = count_tables(markdown)
    measurement.figures_found = count_figures(markdown)
    measurement.missing_strings = [
        needle
        for needle in expectation.must_contain
        if needle and not needle.startswith("REPLACE ME") and needle not in markdown
    ]
    if expectation.figures and not figures_are_in_position(markdown):
        measurement.missing_strings.append("<figures appear only after the text>")
    return measurement


def report(measurements: list[Measurement]) -> int:
    runnable = [m for m in measurements if m.status != "missing_from_corpus"]
    missing = [m for m in measurements if m.status == "missing_from_corpus"]

    print(f"\n{'document':<34} {'status':<18} {'head':>9} {'table':>9} {'fig':>7} {'secs':>7}")
    print("-" * 88)
    for m in measurements:
        expectation = m.expectation
        head = f"{m.headings_found}/{expectation.headings}" if expectation.headings else "-"
        table = f"{m.tables_found}/{expectation.tables}" if expectation.tables else "-"
        figure = f"{m.figures_found}/{expectation.figures}" if expectation.figures else "-"
        print(
            f"{expectation.file[:34]:<34} {m.status:<18} {head:>9} {table:>9} "
            f"{figure:>7} {m.seconds:>7.1f}"
        )
        if m.failure_reason:
            print(f"    reason: {m.failure_reason}")
        for needle in m.missing_strings:
            print(f"    missing: {needle}")

    if not runnable:
        print("\nNo corpus documents found. See tests/fixtures/corpus/README.md.")
        return 2

    converted = [m for m in runnable if m.converted]
    first_attempt = len(converted) / len(runnable)

    heading_expected = sum(m.expectation.headings for m in converted)
    heading_found = sum(min(m.headings_found, m.expectation.headings) for m in converted)
    table_expected = sum(m.expectation.tables for m in converted)
    table_found = sum(min(m.tables_found, m.expectation.tables) for m in converted)
    figure_expected = sum(m.expectation.figures for m in converted)
    figure_found = sum(min(m.figures_found, m.expectation.figures) for m in converted)
    with_missing = [m for m in converted if m.missing_strings]

    heading_recall = heading_found / heading_expected if heading_expected else None
    table_recall = table_found / table_expected if table_expected else None
    figure_recall = figure_found / figure_expected if figure_expected else None

    print("\n== results ==")
    print(f"documents measured        {len(runnable)}" + (f" ({len(missing)} missing)" if missing else ""))
    gates = [
        _gate("SC-001 first-attempt success", first_attempt, FIRST_ATTEMPT_GATE),
        _gate("SC-002 heading recall", heading_recall, HEADING_RECALL_GATE),
        _gate("SC-002 table recall", table_recall, TABLE_RECALL_GATE),
    ]
    for line, _ in gates:
        print(line)
    if figure_recall is not None:
        print(f"FR-004 figures present     {figure_recall:6.1%}")
    print(f"FR-004 figures in position {len(converted) - len(with_missing)}/{len(converted)} documents clean")
    if with_missing:
        print("\nDocuments with missing expected content:")
        for m in with_missing:
            print(f"  {m.expectation.file}: {', '.join(m.missing_strings)}")

    failed_gates = [name for _, name in gates if name]
    if failed_gates or missing:
        print("\nFAIL — " + "; ".join(failed_gates + ([f"{len(missing)} documents missing"] if missing else [])))
        return 1
    print("\nPASS — every gate met. Record these figures in deploy/README.md.")
    return 0


def _gate(label: str, value: float | None, gate: float) -> tuple[str, str | None]:
    if value is None:
        return f"{label:<26} n/a", None
    verdict = "ok" if value >= gate else "FAIL"
    return f"{label:<26} {value:6.1%}  (gate {gate:.0%})  {verdict}", (
        None if value >= gate else label
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8080", help="the deployed stack")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument(
        "--deadline",
        type=float,
        default=1800,
        help="seconds to wait for one document before giving up",
    )
    parser.add_argument("--only", help="measure a single document by filename")
    arguments = parser.parse_args()

    if not arguments.manifest.is_file():
        print(f"no manifest at {arguments.manifest}", file=sys.stderr)
        return 2

    expectations = load_manifest(arguments.manifest)
    if arguments.only:
        expectations = [e for e in expectations if e.file == arguments.only]
    if not expectations:
        print("manifest lists no documents", file=sys.stderr)
        return 2

    stack = Stack(arguments.base_url)
    print(f"measuring {len(expectations)} documents against {stack.base_url}")
    measurements = [measure(stack, expectation, arguments.deadline) for expectation in expectations]
    return report(measurements)


if __name__ == "__main__":
    sys.exit(main())
