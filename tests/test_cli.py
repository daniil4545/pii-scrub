import json
import re
import stat
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import Result
from samples import ALL_KINDS_CALL, ALL_KINDS_LABELS, ALL_KINDS_ORIGINALS, PHONES

from pii_scrub.scrub import Scrubber

Run = Callable[..., Result]
WriteInput = Callable[[str | bytes], Path]

HAPPY = f"Клиент: Меня зовут Мария Орлова, я из Казани.\nМенеджер: Ваш номер {PHONES[0]}?\n"
HAPPY_ORIGINALS = ["Мария Орлова", "Казани", PHONES[0]]

REPORT_JSON = {
    "status": "200",
    "task_data": {
        "report_status": "done",
        "transcript": "Клиент: Меня зовут Мария Орлова.\nМенеджер: Мария Орлова, добрый день.",
        "call_report": {
            "Итог разговора": "Менеджер поговорил с клиентом Марией Орловой.",
            "Оценка вероятности продажи": 7,
            "Продажа оценка": None,
            "Факты": ["Клиент недоволен"],
            "Закрыто": True,
        },
    },
}


def name_labels(text: str) -> list[str]:
    return re.findall(r"\[ИМЯ_\d+\]", text)


def test_happy_path_replaces_pii_and_summarises_without_text(
    run: Run, write_input: WriteInput, tmp_path: Path
) -> None:
    out = tmp_path / "call.clean.txt"

    result = run([str(write_input(HAPPY)), "-o", str(out)])

    cleaned = out.read_text(encoding="utf-8")
    assert result.code == 0
    assert result.out == ""
    assert [o for o in HAPPY_ORIGINALS if o in cleaned + result.err] == []
    assert re.search(r"\[ИМЯ_\d+\]", cleaned)
    assert re.search(r"\[МЕСТО_\d+\]", cleaned)
    assert re.search(r"\[ТЕЛЕФОН_\d+\]", cleaned)
    assert cleaned.startswith("Клиент: Меня зовут ")
    assert re.search(r"\b3\b", result.err)


def test_acceptance_every_kind_masked_in_output_and_stderr(
    run: Run, write_input: WriteInput, tmp_path: Path
) -> None:
    out = tmp_path / "call.clean.txt"

    result = run([str(write_input(ALL_KINDS_CALL)), "-o", str(out)])

    cleaned = out.read_text(encoding="utf-8")
    assert result.code == 0
    assert [o for o in ALL_KINDS_ORIGINALS if o in cleaned + result.err + result.out] == []
    assert [k for k in ALL_KINDS_LABELS if not re.search(rf"\[{k}_\d+\]", cleaned)] == []


def test_input_file_is_not_modified(run: Run, write_input: WriteInput, tmp_path: Path) -> None:
    path = write_input(HAPPY)

    run([str(path), "-o", str(tmp_path / "out.txt")])

    assert path.read_bytes() == HAPPY.encode("utf-8")


def test_same_input_gives_same_bytes(run: Run, write_input: WriteInput, tmp_path: Path) -> None:
    path = write_input(ALL_KINDS_CALL)

    run([str(path), "-o", str(tmp_path / "a.txt")])
    run([str(path), "-o", str(tmp_path / "b.txt")])

    assert (tmp_path / "a.txt").read_bytes() == (tmp_path / "b.txt").read_bytes()


def test_cleaned_file_passes_through_unchanged(
    run: Run, write_input: WriteInput, tmp_path: Path
) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    run([str(write_input(ALL_KINDS_CALL)), "-o", str(first)])

    result = run([str(first), "-o", str(second)])

    assert result.code == 0
    assert second.read_bytes() == first.read_bytes()


@pytest.mark.parametrize("stdin_args", [[], ["-"]], ids=["no_input", "dash"])
def test_stdin_to_stdout_matches_file_mode(
    run: Run, write_input: WriteInput, tmp_path: Path, stdin_args: list[str]
) -> None:
    out = tmp_path / "file.txt"
    run([str(write_input(HAPPY)), "-o", str(out)])

    result = run(stdin_args, stdin=HAPPY.encode("utf-8"))

    assert result.code == 0
    assert result.out == out.read_text(encoding="utf-8")


def test_empty_input_gives_empty_output(run: Run, write_input: WriteInput, tmp_path: Path) -> None:
    out = tmp_path / "out.txt"

    result = run([str(write_input("")), "-o", str(out)])

    assert result.code == 0
    assert out.read_bytes() == b""


def test_person_in_transcript_and_call_report_gets_one_label(
    run: Run, write_input: WriteInput, tmp_path: Path
) -> None:
    out = tmp_path / "out.json"
    raw = json.dumps(REPORT_JSON)  # ensure_ascii: в файле только \uXXXX
    assert "\\u" in raw

    result = run([str(write_input(raw)), "-o", str(out)])

    text = out.read_text(encoding="utf-8")
    data = json.loads(text)
    transcript = data["task_data"]["transcript"]
    summary = data["task_data"]["call_report"]["Итог разговора"]
    assert result.code == 0
    assert "Орлов" not in text + result.err
    assert len(set(name_labels(transcript) + name_labels(summary))) == 1
    assert len(name_labels(transcript)) == 2
    assert "\\u" not in text


def test_json_structure_and_non_string_values_unchanged(
    run: Run, write_input: WriteInput, tmp_path: Path
) -> None:
    out = tmp_path / "out.json"

    run([str(write_input(json.dumps(REPORT_JSON))), "-o", str(out)])

    data = json.loads(out.read_text(encoding="utf-8"))
    report = data["task_data"]["call_report"]
    assert list(data) == list(REPORT_JSON)
    assert list(report) == list(REPORT_JSON["task_data"]["call_report"])
    assert data["status"] == "200"
    assert report["Оценка вероятности продажи"] == 7
    assert report["Продажа оценка"] is None
    assert report["Закрыто"] is True
    assert report["Факты"] == ["Клиент недоволен"]


def test_top_level_json_array_is_walked(run: Run, write_input: WriteInput, tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    raw = json.dumps(
        ["Клиент: Мария Орлова, добрый день.", {"note": "Менеджер: Мария Орлова, слушаю."}],
        ensure_ascii=False,
    )

    result = run([str(write_input(raw)), "-o", str(out)])

    data = json.loads(out.read_text(encoding="utf-8"))
    assert result.code == 0
    assert "Орлов" not in out.read_text(encoding="utf-8")
    assert name_labels(data[0]) == name_labels(data[1]["note"]) != []


@pytest.mark.parametrize("raw", ['{"a": ', "[1, 2", "{broken}"])
def test_broken_json_exits_2_and_writes_nothing(
    run: Run, write_input: WriteInput, tmp_path: Path, raw: str
) -> None:
    out = tmp_path / "out.json"

    result = run([str(write_input(raw)), "-o", str(out)])

    assert result.code == 2
    assert result.err != ""
    assert result.out == ""
    assert not out.exists()


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps({"task_data": json.dumps({"transcript": "Клиент: Иван Сергеевич"})}),
        "Клиент: \\u0418\\u0432\\u0430\\u043d Сергеевич",
    ],
    ids=["double_encoded_json", "plain_text"],
)
def test_leftover_unicode_escape_exits_2_and_writes_nothing(
    run: Run, write_input: WriteInput, tmp_path: Path, raw: str
) -> None:
    out = tmp_path / "out.txt"

    result = run([str(write_input(raw)), "-o", str(out)])

    assert result.code == 2
    assert "Сергеевич" not in result.err
    assert not out.exists()


@pytest.mark.parametrize("missing", ["file", "bytes", "same_as_input"])
def test_input_errors_exit_2_and_write_nothing(
    run: Run, write_input: WriteInput, tmp_path: Path, missing: str
) -> None:
    out = tmp_path / "out.txt"
    path = write_input(b"\xff\xfe\xd0" if missing == "bytes" else HAPPY)
    argv = {
        "file": [str(tmp_path / "nope.txt"), "-o", str(out)],
        "bytes": [str(path), "-o", str(out)],
        "same_as_input": [str(path), "-o", str(path)],
    }[missing]

    result = run(argv)

    assert result.code == 2
    assert result.err != ""
    assert not out.exists() or missing == "same_as_input"
    if missing == "same_as_input":
        assert path.read_text(encoding="utf-8") == HAPPY


def test_unexpected_error_exits_1_with_kind_but_without_input_text(
    run: Run, write_input: WriteInput, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out.txt"

    def boom(self: Scrubber, text: str, where: str) -> str:
        raise RuntimeError(f"failed on {text}")

    monkeypatch.setattr(Scrubber, "scrub", boom)

    result = run([str(write_input(HAPPY)), "-o", str(out)])

    assert result.code == 1
    assert "RuntimeError" in result.err
    assert "Орлова" not in result.err + result.out
    assert not out.exists()


def test_residue_refused_with_exit_3_line_and_no_output(
    run: Run, write_input: WriteInput, tmp_path: Path
) -> None:
    text = "Клиент: Добрый день.\nМенеджер: Код 123 45 67 сохраните.\n"
    out = tmp_path / "out.txt"

    result = run([str(write_input(text)), "-o", str(out)])

    assert result.code == 3
    assert result.err.strip() == "scrub: отказ, остаток в строках 2"
    assert not out.exists()


def test_residue_in_json_refused_with_path_of_every_value(
    run: Run, write_input: WriteInput, tmp_path: Path
) -> None:
    raw = json.dumps({"call_report": {"Факты": "Код 123 45 67 сохраните."}, "t": "Код 765 43 21"})

    result = run([str(write_input(raw)), "-o", str(tmp_path / "out.json")])

    assert result.code == 3
    assert "call_report.Факты:1:" in result.err
    assert "t:1:" in result.err
    assert "45 67" not in result.err


def test_residue_report_written_with_originals_and_places_when_requested(
    run: Run, write_input: WriteInput, tmp_path: Path
) -> None:
    text = "Клиент: Меня зовут Мария Орлова.\nМенеджер: Код 123 45 67 сохраните.\n"
    report = tmp_path / "r.tsv"

    result = run([str(write_input(text)), "-o", str(tmp_path / "out.txt"), "--report", str(report)])

    assert result.code == 3
    assert report.stat().st_mode & 0o777 == 0o600
    body = report.read_text(encoding="utf-8")
    assert "Мария" in body
    assert "ОСТАТОК\t2:" in body
    assert not (tmp_path / "out.txt").exists()


def test_too_large_input_exits_2_without_output(
    run: Run, write_input: WriteInput, tmp_path: Path
) -> None:
    out = tmp_path / "out.txt"

    result = run([str(write_input("а" * 600_000)), "-o", str(out)])

    assert result.code == 2
    assert "вход слишком велик" in result.err
    assert not out.exists()


def test_report_holds_originals_with_0600_and_outputs_do_not(
    run: Run, write_input: WriteInput, tmp_path: Path
) -> None:
    report = tmp_path / "call.report"
    out = tmp_path / "out.txt"

    result = run([str(write_input(HAPPY)), "-o", str(out), "--report", str(report)])

    held = report.read_text(encoding="utf-8")
    assert result.code == 0
    assert stat.S_IMODE(report.stat().st_mode) == 0o600
    assert [o for o in HAPPY_ORIGINALS if o not in held] == []
    assert re.search(r"\[ИМЯ_\d+\]", held)
    assert [o for o in HAPPY_ORIGINALS if o in out.read_text(encoding="utf-8") + result.err] == []


def test_no_report_file_without_flag(run: Run, write_input: WriteInput, tmp_path: Path) -> None:
    run([str(write_input(HAPPY)), "-o", str(tmp_path / "out.txt")])

    assert sorted(p.name for p in tmp_path.iterdir()) == ["call.txt", "out.txt"]


@pytest.mark.parametrize(
    "raw",
    ["[ИМЯ_1]: добрый день, это Мария Орлова.", "[Оператор]: добрый день, это Мария Орлова."],
    ids=["label_first", "speaker_tag_first"],
)
def test_text_starting_with_bracket_is_text_not_json(
    run: Run, write_input: WriteInput, raw: str
) -> None:
    result = run([str(write_input(raw))])

    assert result.code == 0
    assert "Мария Орлова" not in result.out


def test_report_equal_to_output_rejected_for_new_paths(
    run: Run, write_input: WriteInput, tmp_path: Path
) -> None:
    same = tmp_path / "same.txt"

    result = run([str(write_input(HAPPY)), "-o", str(same), "--report", str(same)])

    assert result.code == 2
    assert not same.exists()


def test_report_write_failure_leaves_no_output(
    run: Run, write_input: WriteInput, tmp_path: Path
) -> None:
    out = tmp_path / "out.txt"
    report = tmp_path / "missing_dir" / "call.report"

    result = run([str(write_input(HAPPY)), "-o", str(out), "--report", str(report)])

    assert result.code == 2
    assert not out.exists()
