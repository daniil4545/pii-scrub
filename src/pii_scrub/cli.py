"""CLI scrub: arguments, JSON mode, files, summary, exit codes."""

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from pii_scrub.scrub import MAX_INPUT_BYTES, Scrubber, ScrubRefused, parse_terms

ESCAPE = re.compile(r"\\u[0-9a-fA-F]{4}")
# JSON is an object or an array of values; "[ИМЯ_1]" and "[Оператор]:" at the start of a text are
# text
JSON_START = re.compile(r'\s*(\{|\[\s*(?!\d+:\d)[{\["\]\d-])')
REPORT_MODE = 0o600
# The refusal reason decides the exit code: an input error or a breakdown.
EXIT_INPUT = 2
EXIT_BROKEN = 1
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
    parser.add_argument("--keep", help="word list that is never masked (own company name)")
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


def scrub_json(value: Any, scrubber: Scrubber, path: str) -> Any:  # noqa: ANN401
    """Cleans string values; structure, keys and other types stay unchanged."""
    if isinstance(value, str):
        if ESCAPE.search(value):
            raise InputError(f"leftover \\uXXXX escape in value {path}: NER cannot see such names")
        return scrubber.scrub(value)
    if isinstance(value, dict):
        return {
            key: scrub_json(item, scrubber, f"{path}.{key}" if path else key)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub_json(item, scrubber, f"{path}[{i}]") for i, item in enumerate(value)]
    return value


def scrub_text(text: str, scrubber: Scrubber) -> str:
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ScrubRefused("input too large")
    if not JSON_START.match(text):
        if ESCAPE.search(text):
            raise InputError("leftover \\uXXXX escape in text: NER cannot see such names")
        return scrubber.scrub(text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise InputError(f"invalid JSON: {err.msg}, line {err.lineno}, column {err.colno}") from err
    return json.dumps(scrub_json(data, scrubber, ""), ensure_ascii=False)


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


def refusal_code(refused: ScrubRefused) -> int:
    return EXIT_INPUT if refused.reason in INPUT_REASONS else EXIT_BROKEN


def read_keep(path: str | None) -> tuple[str, ...]:
    if not path:
        return ()
    try:
        return parse_terms(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as err:
        raise InputError(f"cannot read keep list: {type(err).__name__}") from err


def run(args: argparse.Namespace) -> None:
    check_paths(args)
    scrubber = Scrubber(keep=read_keep(args.keep))
    cleaned = scrub_text(read_input(args.input), scrubber)
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
        print(f"scrub: refused, {refused.reason}", file=sys.stderr)
        return refusal_code(refused)
    except Exception as err:  # noqa: BLE001 - process boundary: the error kind only, no input text
        print(f"scrub: internal error: {type(err).__name__}", file=sys.stderr)
        return EXIT_BROKEN
    return 0
