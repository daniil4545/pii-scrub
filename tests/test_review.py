"""Reproductions of code review remarks: every dictation form and every leak gets a test."""

import re
import time

import pytest

from pii_scrub import scrub as scrub_module
from pii_scrub.cli import main
from pii_scrub.scrub import Scrubber, ScrubRefused


def clean(text: str) -> str:
    return Scrubber().scrub(text, "")


def test_digit_pieces_phone_across_replies_masked() -> None:
    text = "Клиент: 8 912\nМенеджер: так\nКлиент: 345 67\nМенеджер: да\nКлиент: 89"

    assert "[ТЕЛЕФОН_1]" in clean(text)


def test_digit_pieces_inn_across_replies_masked() -> None:
    text = "Клиент: ИНН 7707\nМенеджер: так\nКлиент: 083893"

    assert "[ИНН_1]" in clean(text)


def test_phone_words_before_an_amount_with_currency_still_masked() -> None:
    text = (
        "Номер восемь девятьсот двенадцать триста сорок пять шестьдесят семь восемьдесят девять. "
        "Две тысячи рублей."
    )

    result = clean(text)

    assert result == "Номер [ТЕЛЕФОН_1]. Две тысячи рублей."


@pytest.mark.parametrize(
    "text",
    [
        "Почта иван петров собака gmail точка com.",
        "Почта иван петров собака gmail.com.",
        "Почта иван точка петров точка девяносто пять собака мэйл точка ру.",
    ],
    ids=["latin_zone", "latin_domain", "long_local_part"],
)
def test_mixed_script_spoken_email_masked(text: str) -> None:
    result = clean(text)

    assert re.search(r"\[(EMAIL|САЙТ)_1\]", result)  # the earlier finding gives the label
    assert "собака" not in result
    assert "иван" not in result


@pytest.mark.parametrize(
    "text",
    [
        "Дата 2025-03-15 и 15-03-2025.",
        "Выгрузка 2025-03-15 10:30:00.",
        "Сумма сто тысяч тенге.",
        "Сумма сто тысяч белорусских рублей.",
        "Нужно сто пятьдесят штук.",
    ],
)
def test_iso_dates_and_other_currencies_not_refused(text: str) -> None:
    assert clean(text) == text


def test_address_without_house_does_not_swallow_the_next_line() -> None:
    result = clean("Клиент: Доставка на улицу Ленина.\nМенеджер: Хорошо.")

    assert result == "Клиент: Доставка на [АДРЕС_1].\nМенеджер: Хорошо."


def test_birth_date_with_numeric_day_and_month_word_masked() -> None:
    result = clean("Клиент: Я родился 12 марта 1985 года.")

    assert "1985" not in result
    assert "[РОЖДЕНИЕ_1]" in result


def test_ner_failure_refuses_without_input_text(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> None:
        raise OSError("не открылся файл с Иван Петров")

    monkeypatch.setattr(scrub_module, "load_natasha", broken)

    with pytest.raises(ScrubRefused) as refused:
        clean("Клиент: Иван Петров.")

    assert refused.value.reason == "сбой NER"
    assert "Иван" not in str(refused.value)
    assert refused.value.__suppress_context__


def test_ner_failure_exits_1_without_source_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken() -> None:
        raise OSError("секрет Иван")

    monkeypatch.setattr(scrub_module, "load_natasha", broken)
    source = tmp_path / "in.txt"
    source.write_text("Клиент: Иван Петров.", encoding="utf-8")

    code = main([str(source), "-o", str(tmp_path / "out.txt")])

    assert code == 1
    assert "Иван" not in capsys.readouterr().err


def test_repr_of_scrubber_carries_no_originals() -> None:
    scrubber = Scrubber()
    scrubber.scrub("Клиент: Меня зовут Мария Орлова.", "")

    assert "Орлова" not in repr(scrubber)


def test_nul_in_input_refused_as_input_error() -> None:
    with pytest.raises(ScrubRefused) as refused:
        clean("Код 12\x0034")

    assert refused.value.reason == "недопустимый символ"


def test_timestamp_in_brackets_is_text_not_json() -> None:
    text = "[00:01] Менеджер: Добрый день."

    assert clean(text) == text


def test_hostile_long_input_finishes_quickly() -> None:
    started = time.monotonic()

    with pytest.raises(ScrubRefused):
        clean("девять " * 20_000)

    assert time.monotonic() - started < 20


def test_places_are_capped() -> None:
    scrubber = Scrubber()

    with pytest.raises(ScrubRefused):
        scrubber.scrub("1234567 " * 500, "")

    assert len(scrubber.places) <= scrub_module.MAX_PLACES
