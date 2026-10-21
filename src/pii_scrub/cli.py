"""CLI scrub: arguments, JSON mode, files, summary, exit codes."""

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from pii_scrub.scrub import MAX_INPUT_BYTES, Scrubber, ScrubRefused

ESCAPE = re.compile(r"\\u[0-9a-fA-F]{4}")
# JSON is an object or an array of values; "[ИМЯ_1]" and "[Оператор]:" at the start of a text are
# text
JSON_START = re.compile(r'\s*(\{|\[\s*(?!\d+:\d)[{\["\]\d-])')
REPORT_MODE = 0o600
# The refusal reason decides the exit code: a residue needs a human, the rest is an input error or a
# breakdown.
EXIT_RESIDUE = 3
EXIT_INPUT = 2
EXIT_BROKEN = 1
RESIDUE = "residue"
INPUT_REASONS = {"input too large", "invalid character"}


class InputError(Exception):
    """Input or output error: the message carries no input text, exit code 2."""


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scrub", description="Replaces personal data in Russian text with stable labels."
    )
    parser.add_argument("input", nargs="?", help="file; omit or '-' for stdin")
    parser.add_argument("-o", "--output", help="output file; omit for stdout")
    parser.add_argument("--report", help="file for the originals (mode 0600)")
    return parser.parse_args(argv)


def same_file(first: Path, second: Path) -> bool:
    if first.resolve() == second.resolve():
        return True
    return first.exists() and second.exists() and first.samefile(second)


def check_paths(args: argparse.Namespace) -> None:
    source = Path(args.input) if args.input not in (None, "-") else None
    targets = {"-o": args.output, "--report": args.report}
    for name, target in targets.items():
        if source and target and same_file(source, Path(target)):
            raise InputError(f"{name} equals the input file")
    if args.output and args.report and same_file(Path(args.output), Path(args.report)):
        raise InputError("--report equals -o")


def read_input(path: str | None) -> str:
    try:
        raw = sys.stdin.buffer.read() if path in (None, "-") else Path(path).read_bytes()
    except OSError as err:
        raise InputError(f"cannot read input: {err.strerror}") from err
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as err:
        raise InputError("input is not valid UTF-8") from err


def scrub_json(value: Any, scrubber: Scrubber, path: str, refusals: list[str]) -> Any:  # noqa: ANN401
    """Cleans string values; structure, keys and other types stay unchanged.

    A refusal on one value does not stop the walk: the residue places are collected over the whole
    file."""
    if isinstance(value, str):
        if ESCAPE.search(value):
            raise InputError(f"leftover \\uXXXX escape in value {path}: NER cannot see such names")
        try:
            return scrubber.scrub(value, path)
        except ScrubRefused as refused:
            refusals.append(refused.reason)
            return value
    if isinstance(value, dict):
        return {
            key: scrub_json(item, scrubber, f"{path}.{key}" if path else key, refusals)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            scrub_json(item, scrubber, f"{path}[{i}]", refusals) for i, item in enumerate(value)
        ]
    return value


def scrub_text(text: str, scrubber: Scrubber) -> str:
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ScrubRefused("input too large")
    if not JSON_START.match(text):
        if ESCAPE.search(text):
            raise InputError("leftover \\uXXXX escape in text: NER cannot see such names")
        return scrubber.scrub(text, "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise InputError(f"invalid JSON: {err.msg}, line {err.lineno}, column {err.colno}") from err
    refusals: list[str] = []
    cleaned = scrub_json(data, scrubber, "", refusals)
    if refusals:
        reason = next((r for r in refusals if r != RESIDUE), RESIDUE)
        raise ScrubRefused(reason, scrubber.places)
    return json.dumps(cleaned, ensure_ascii=False)


def write_output(path: str | None, text: str) -> None:
    if path is None:
        sys.stdout.buffer.write(text.encode("utf-8"))
        sys.stdout.buffer.flush()
        return
    try:
        Path(path).write_bytes(text.encode("utf-8"))
    except OSError as err:
        raise InputError(f"cannot write output: {err.strerror}") from err


def write_report(path: str, scrubber: Scrubber) -> None:
    """The only place with originals: mode 0600 is set before any data is written."""
    unique = dict.fromkeys(scrubber.replacements)
    lines = [f"{label}\t{' '.join(original.split())}" for label, original in unique]
    lines += [f"RESIDUE\t{place}" for place in scrubber.places]
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, REPORT_MODE)
        os.fchmod(fd, REPORT_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as report:
            report.write("\n".join(lines) + "\n" if lines else "")
    except OSError as err:
        # No partial report with originals is left on disk.
        Path(path).unlink(missing_ok=True)
        raise InputError(f"cannot write report: {err.strerror}") from err


def summary(scrubber: Scrubber) -> str:
    """Replacement counters; the summary has no input text."""
    kinds = Counter(label[1 : label.rindex("_")] for label, _ in scrubber.replacements)
    detail = ", ".join(f"{kind} {count}" for kind, count in kinds.items())
    return f"scrub: {len(scrubber.replacements)} replacements" + (f" ({detail})" if detail else "")


def refusal_message(refused: ScrubRefused) -> str:
    """Refusal message: the reason and the residue places (lines or paths), without the input
    text."""
    if not refused.places:
        return f"scrub: refused, {refused.reason}"
    if all(re.fullmatch(r"\d+:\d+", place) for place in refused.places):
        lines = dict.fromkeys(place.split(":")[0] for place in refused.places)
        return f"scrub: refused, residue in lines {', '.join(lines)}"
    return f"scrub: refused, residue in {', '.join(refused.places)}"


def refusal_code(refused: ScrubRefused) -> int:
    if refused.reason == RESIDUE:
        return EXIT_RESIDUE
    return EXIT_INPUT if refused.reason in INPUT_REASONS else EXIT_BROKEN


def run(args: argparse.Namespace) -> None:
    check_paths(args)
    scrubber = Scrubber()
    try:
        cleaned = scrub_text(read_input(args.input), scrubber)
    except ScrubRefused as refused:
        # A human needs the residue places: the report is written on refusal too, there is no
        # output.
        if args.report and refused.reason == RESIDUE:
            write_report(args.report, scrubber)
        raise
    # the report goes before the output: on exit code 2 the output must not stay written
    if args.report:
        write_report(args.report, scrubber)
    try:
        write_output(args.output, cleaned)
    except InputError:
        if args.report:
            Path(args.report).unlink(missing_ok=True)
        raise
    print(summary(scrubber), file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        run(args)
    except InputError as err:
        print(f"scrub: {err}", file=sys.stderr)
        return EXIT_INPUT
    except ScrubRefused as refused:
        print(refusal_message(refused), file=sys.stderr)
        return refusal_code(refused)
    except Exception as err:  # noqa: BLE001 - process boundary: the error kind only, no input text
        print(f"scrub: internal error: {type(err).__name__}", file=sys.stderr)
        return EXIT_BROKEN
    return 0
