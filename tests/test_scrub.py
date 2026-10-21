import re

import pytest
from samples import (
    ALL_KINDS_CALL,
    KINDS,
    NOT_RESIDUE_TEXTS,
    OTHER_VALUES,
    PERSON_CASES,
    RESIDUE_TEXTS,
)

from pii_scrub.scrub import MAX_INPUT_BYTES, Scrubber, ScrubRefused


def labels(text: str, prefix: str) -> list[str]:
    return re.findall(rf"\[{prefix}_\d+\]", text)


@pytest.mark.parametrize(("prefix", "text", "span"), KINDS.values(), ids=KINDS.keys())
def test_each_kind_replaced_by_label_rest_intact(prefix: str, text: str, span: str) -> None:
    result = Scrubber().scrub(text, "1")

    assert result == text.replace(span, f"[{prefix}_1]")


def test_replacements_hold_label_and_original_for_report() -> None:
    scrubber = Scrubber()
    scrubber.scrub("Клиент: Меня зовут Мария Орлова, перезвоните.", "1")

    assert scrubber.replacements == [("[ИМЯ_1]", "Мария Орлова")]


@pytest.mark.parametrize(
    ("nominative", "dative"), PERSON_CASES, ids=["two_words", "three_words", "name_surname"]
)
def test_person_cases_get_one_label(nominative: str, dative: str) -> None:
    scrubber = Scrubber()
    first = scrubber.scrub(f"Клиент: {nominative}, добрый день.", "1")
    second = scrubber.scrub(f"Менеджер: {dative} всё передали.", "2")

    assert labels(first, "ИМЯ") == ["[ИМЯ_1]"]
    assert labels(second, "ИМЯ") == ["[ИМЯ_1]"]


def test_extra_word_in_span_is_masked_and_keyed_by_name() -> None:
    scrubber = Scrubber()
    first = scrubber.scrub("Клиент: Иван Сергеевич, добрый день.", "1")
    second = scrubber.scrub("Менеджер: Передам Ивану Сергеевичу, он перезвонит.", "2")

    assert "Ивану Сергеевичу" not in second
    assert labels(second, "ИМЯ") == labels(first, "ИМЯ")


def test_different_people_get_different_labels_by_first_appearance() -> None:
    text = (
        "Клиент: Мария Орлова, добрый день.\n"
        "Менеджер: Передайте Сергею Орлову.\n"
        "Клиент: Мария Орлова, это я.\n"
    )

    result = Scrubber().scrub(text, "1")

    assert labels(result, "ИМЯ") == ["[ИМЯ_1]", "[ИМЯ_2]", "[ИМЯ_1]"]


@pytest.mark.parametrize(
    "key",
    ["phone_mobile", "email", "card", "nick", "site", "inn10"],
)
def test_same_value_gets_same_label_different_value_other_label(key: str) -> None:
    prefix, text, original = KINDS[key]
    scrubber = Scrubber()

    first = scrubber.scrub(text, "1")
    again = scrubber.scrub(text, "2")
    different = scrubber.scrub(text.replace(original, OTHER_VALUES[key]), "3")

    assert labels(first, prefix) == labels(again, prefix) == [f"[{prefix}_1]"]
    assert labels(different, prefix) == [f"[{prefix}_2]"]


def test_overlapping_email_and_domain_merge_into_one_label() -> None:
    result = Scrubber().scrub("Менеджер: Пишите на ivan@romashka-test.ru.", "1")

    assert result == "Менеджер: Пишите на [EMAIL_1]."


def test_scrubbing_is_deterministic() -> None:
    first = Scrubber().scrub(ALL_KINDS_CALL, "1")
    second = Scrubber().scrub(ALL_KINDS_CALL, "1")

    assert first == second


def test_scrubbing_clean_text_changes_nothing() -> None:
    clean = Scrubber().scrub(ALL_KINDS_CALL, "1")

    assert Scrubber().scrub(clean, "1") == clean


@pytest.mark.parametrize("text", RESIDUE_TEXTS, ids=["spaces", "dashes", "parentheses", "words"])
def test_residue_digits_refuse_without_text_in_error(text: str) -> None:
    scrubber = Scrubber()

    with pytest.raises(ScrubRefused) as refused:
        scrubber.scrub(text, "1")

    assert refused.value.reason == "residue"
    assert len(refused.value.places) == 1
    assert not re.search(
        r"\d{3}", str(refused.value) + " ".join(refused.value.places).replace("1:1:", "")
    )


@pytest.mark.parametrize(
    "text",
    [*NOT_RESIDUE_TEXTS, KINDS["phone_mobile"][1], KINDS["card"][1]],
    ids=[
        "short",
        "date_time",
        "sum_digits",
        "sum_words",
        "date_words",
        "masked_phone",
        "masked_card",
    ],
)
def test_short_numbers_dates_sums_and_masked_values_not_refused(text: str) -> None:
    Scrubber().scrub(text, "1")


def test_empty_text_gives_empty_text() -> None:
    assert Scrubber().scrub("", "") == ""


def test_input_over_limit_refused_with_reason() -> None:
    with pytest.raises(ScrubRefused) as refused:
        Scrubber().scrub("а" * (MAX_INPUT_BYTES // 2 + 1), "")

    assert refused.value.reason == "input too large"


SPOKEN = [
    (
        "phone_words",
        "Клиент: Запишите восемь девятьсот двенадцать триста сорок пять шестьдесят семь восемьдесят девять.",
        "[ТЕЛЕФОН_1]",
    ),
    (
        "phone_digit_words",
        "Клиент: Номер восемь девять один два три четыре пять шесть семь восемь девять.",
        "[ТЕЛЕФОН_1]",
    ),
    ("phone_mixed", "Клиент: Номер восемь девятьсот двенадцать 345 67 89.", "[ТЕЛЕФОН_1]"),
    (
        "phone_relay",
        "Клиент: Номер восемь девятьсот двенадцать\nМенеджер: так\nКлиент: триста сорок пять\nМенеджер: да\nКлиент: шестьдесят семь восемьдесят девять.",
        "[ТЕЛЕФОН_1]",
    ),
    ("email_words", "Клиент: Почта иван точка петров собака мэйл точка ру.", "[EMAIL_1]"),
]


@pytest.mark.parametrize(
    ("text", "label"), [(t, label) for _, t, label in SPOKEN], ids=[n for n, _, _ in SPOKEN]
)
def test_spoken_forms_masked(text: str, label: str) -> None:
    result = Scrubber().scrub(text, "")

    assert label in result
    for word in ("девятьсот", "собака", "триста", "четыре"):
        assert word not in result


def test_mask_covers_whole_words_when_match_starts_inside_a_chunk() -> None:
    result = Scrubber().scrub(
        "Клиент: Номер восемь девятьсот двенадцать триста сорок пять шестьдесят семь восемьдесят девять и всё.",
        "",
    )

    assert result == "Клиент: Номер [ТЕЛЕФОН_1] и всё."


def test_extra_digit_next_to_phone_does_not_hide_the_phone() -> None:
    result = Scrubber().scrub(
        "Клиент: Номер восемь девятьсот двенадцать триста сорок пять шестьдесят семь восемьдесят девять",
        "",
    )

    assert "[ТЕЛЕФОН_1]" in result
    assert "восемь" not in result


@pytest.mark.parametrize(
    "text",
    [
        "Клиент: Подскажите, пожалуйста.",
        "Менеджер: Да, понял.",
        "Менеджер: Нашему МОПу нужно связаться с ЛПР и РОП тоже.",
        "Клиент: У нас собака, а точка продаж рядом.",
    ],
)
def test_roles_verbs_and_animals_not_masked(text: str) -> None:
    assert Scrubber().scrub(text, "") == text


def test_snils_with_valid_checksum_masked() -> None:
    assert Scrubber().scrub("Клиент: СНИЛС 112-233-445 95.", "") == "Клиент: СНИЛС [СНИЛС_1]."


def test_address_and_birth_date_masked() -> None:
    result = Scrubber().scrub(
        "Клиент: Доставка на улицу Ленина, дом 5, квартира 12.\nКлиент: Я родился 12.03.1985.", ""
    )

    assert "Ленина" not in result
    assert "1985" not in result
    assert "[АДРЕС_1]" in result
    assert "[РОЖДЕНИЕ_1]" in result


def test_date_without_birth_context_not_masked() -> None:
    text = "Клиент: Встреча 12.03.2025."

    assert Scrubber().scrub(text, "") == text
