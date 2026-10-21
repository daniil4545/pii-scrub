"""Scrubbing measurement on the synthetic set: misses, false masks, refusals.

Run: make eval. Exit code 1 if an acceptance rule is broken.
Prints only kinds, forms and case numbers, never the texts of the set.
"""

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pii_scrub.scrub import LABEL, Scrubber, ScrubRefused, load_natasha

BLOCKING_KINDS = ("phone", "inn", "email")
NEGATIVE_LIMIT = 0.10
REFUSAL_LIMIT = 0.05
DEFAULT_DATA = Path("tests/data/synthetic.jsonl")

Scrub = Callable[[str], str]


@dataclass
class Report:
    secrets: dict[tuple[str, str], list[int]] = field(
        default_factory=lambda: defaultdict(lambda: [0, 0])
    )
    missed_ids: list[str] = field(default_factory=list)
    pii_texts: int = 0
    pii_refused: int = 0
    negative_texts: int = 0
    negative_bad: int = 0
    negative_bad_ids: list[str] = field(default_factory=list)

    def missed(self, kind: str) -> int:
        return sum(m for (k, _), (_, m) in self.secrets.items() if k == kind)

    def failures(self) -> list[str]:
        out = [
            f"пропуски {kind}: {self.missed(kind)}" for kind in BLOCKING_KINDS if self.missed(kind)
        ]
        if self.negative_texts and self.negative_bad / self.negative_texts > NEGATIVE_LIMIT:
            out.append(f"негативный набор испорчен: {self.negative_bad} из {self.negative_texts}")
        if self.pii_texts and self.pii_refused / self.pii_texts > REFUSAL_LIMIT:
            out.append(f"отказов на наборе с ПДн: {self.pii_refused} из {self.pii_texts}")
        return out


def load_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def evaluate(rows: list[dict], scrub: Scrub, only_visible: bool = False) -> Report:
    report = Report()
    for row in rows:
        if only_visible and row["deferred"]:
            continue
        negative = row["group"] == "negative"
        try:
            output = scrub(row["text"])
        except ScrubRefused:
            output = None
        if negative:
            report.negative_texts += 1
            if output is None or output != row["text"]:
                report.negative_bad += 1
                report.negative_bad_ids.append(row["id"])
            continue
        report.pii_texts += 1
        if output is None:
            report.pii_refused += 1
            continue
        left = LABEL.sub("", output)  # digits in label numbers are not a leak
        for secret in row["secrets"]:
            counts = report.secrets[(secret["kind"], secret["form"])]
            counts[0] += 1
            if any(part in left for part in secret["parts"]):
                counts[1] += 1
                report.missed_ids.append(f"{row['id']}:{secret['kind']}/{secret['form']}")
    return report


def default_scrub() -> Scrub:
    load_natasha()
    return lambda text: Scrubber().scrub(text, "")


def print_report(report: Report, title: str) -> None:
    print(f"== {title}")
    print(f"{'вид':10} {'форма':14} {'всего':>6} {'пропуск':>8}")
    for (kind, form), (total, missed) in sorted(report.secrets.items()):
        mark = "*" if kind in BLOCKING_KINDS else " "
        print(f"{kind:9}{mark} {form:14} {total:6} {missed:8}")
    print(
        f"* блокирует приёмку. Отказов на наборе с ПДн: {report.pii_refused} из {report.pii_texts}"
    )
    print(
        "Негативный набор испорчен (ложная маска или отказ): "
        f"{report.negative_bad} из {report.negative_texts}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Замер очистки на синтетике")
    parser.add_argument("data", nargs="?", default=str(DEFAULT_DATA))
    parser.add_argument(
        "--ids", action="store_true", help="печатать номера случаев с пропусками (без отложенных)"
    )
    args = parser.parse_args(argv)
    rows = load_rows(Path(args.data))
    scrub = default_scrub()
    visible = evaluate(rows, scrub, only_visible=True)
    full = evaluate(rows, scrub)
    print_report(visible, "видимая часть")
    print_report(full, "весь набор")
    if args.ids:
        print("пропуски:", ", ".join(visible.missed_ids) or "нет")
        print("негативные:", ", ".join(visible.negative_bad_ids) or "нет")
    failures = full.failures()
    for line in failures:
        print(f"ПРОВАЛ: {line}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
