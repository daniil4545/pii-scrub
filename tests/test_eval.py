from pathlib import Path

import pytest
from gen.generate import generate

from pii_scrub.eval import DEFAULT_DATA, evaluate, load_rows
from pii_scrub.scrub import ScrubRefused

DATA = Path(__file__).parent / "data" / "synthetic.jsonl"


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    return load_rows(DATA)


def oracle(rows: list[dict]):
    """A known-good output: removes exactly the secrets of the set from the text."""
    secrets = {row["text"]: row["secrets"] for row in rows}

    def scrub(text: str) -> str:
        for secret in secrets[text]:
            for part in secret["parts"]:
                text = text.replace(part, "[X_1]")
        return text

    return scrub


def test_good_output_passes(rows: list[dict]) -> None:
    assert evaluate(rows, oracle(rows)).failures() == []


def test_doing_nothing_fails_on_blocking_classes(rows: list[dict]) -> None:
    failures = evaluate(rows, lambda text: text).failures()

    assert any("phone" in f for f in failures)
    assert any("email" in f for f in failures)


def test_masking_everything_fails_on_the_negative_set(rows: list[dict]) -> None:
    failures = evaluate(rows, lambda text: "[X_1]").failures()

    assert any("негативный набор" in f for f in failures)


def test_refusing_everything_fails_on_both_limits(rows: list[dict]) -> None:
    def refuse(_: str) -> str:
        raise ScrubRefused("остаток")

    failures = evaluate(rows, refuse).failures()

    assert any("отказов" in f for f in failures)
    assert any("негативный набор" in f for f in failures)


def test_default_data_path_points_at_the_committed_set() -> None:
    assert (Path(__file__).parent.parent / DEFAULT_DATA).exists()


def test_committed_set_is_frozen_output_of_the_generator(rows: list[dict]) -> None:
    assert generate() == rows


def test_set_has_deferred_part_and_long_text(rows: list[dict]) -> None:
    assert any(row["deferred"] for row in rows)
    assert any(row["group"] == "long" and len(row["text"]) > 50_000 for row in rows)
    assert not any(row["group"] == "negative" and row["secrets"] for row in rows)
