"""Call-context rules: NER misses found on reference calls, reproduced with synthetic text."""

import time

import pytest

from pii_scrub.context import find_context
from pii_scrub.scrub import LABEL, Scrubber, is_term, load_natasha, parse_terms


def clean(text: str) -> str:
    return Scrubber().scrub(text)


@pytest.mark.parametrize(
    ("text", "hidden"),
    [
        ("- Пётр, здравствуйте!", "Пётр"),
        ("- Это Садовая, 12, корпус 2.", "Садовая"),
        ("- Адрес такой. Надо Лесная 7.", "Мира"),
        ("- Какой адрес?\n- Ремонт?\n- Лесная, семь.", "Мира"),
        ("- Она на этом, на Ломоносовской.", "Ломоносовской"),
        ("- Наша организация называется ТЕКС.", "ТЕКС"),
        ("- Комбинат Сосновка, да.", "Сосновка"),
        ("- Альфа-Бета-Сервис,", "Альфа"),
        ("- Петростройка.", "Петростройка"),
        ("- Объект называется «Реконструкция склада в Тихорецке».", "Тихорецке"),
    ],
    ids=[
        "vocative",
        "street_building",
        "street_hint",
        "street_hint_previous_reply",
        "na_surname",
        "named",
        "kombinat",
        "hyphen",
        "lone_reply",
        "quoted_object",
    ],
)
def test_context_pii_masked(text: str, hidden: str) -> None:
    result = clean(text)

    assert hidden not in result
    assert LABEL.search(result)


def test_house_number_after_place_joins_its_label() -> None:
    assert clean("- Он живёт в Москве, 11.") == "- Он живёт в [МЕСТО_1]."


def test_name_repeated_later_in_file_gets_same_label() -> None:
    scrubber = Scrubber()
    scrubber.scrub("- Пётр, здравствуйте!")

    assert scrubber.scrub("- А получили запрос, Пётр?") == "- А получили запрос, [ИМЯ_1]?"


def test_glued_words_masked_at_exact_place() -> None:
    result = clean("- НаКамчатку уйдёт, потом вМоскву.")

    assert result == "- На[МЕСТО_1] уйдёт, потом в[МЕСТО_2]."


@pytest.mark.parametrize(
    "text",
    [
        "- Хорошо, давайте так.",
        "- Модель 24 портов нужна.",
        "- Нужно 24 штуки, на складе есть.",
        "- Да.",
        "- Отправьте по почте.",
    ],
    ids=["not_name", "model_number", "quantity", "short_reply", "lowercase"],
)
def test_ordinary_phrases_not_masked(text: str) -> None:
    assert clean(text) == text


@pytest.mark.parametrize(
    "text",
    ["- Адрес: 630099 Новосибирск, да.", "- Город Новосибирск, 630099.", "- Индекс 630099."],
    ids=["before_city", "after_city", "hint"],
)
def test_postcode_near_city_masked(text: str) -> None:
    assert "630099" not in clean(text)


@pytest.mark.parametrize(
    ("text", "hidden"),
    [
        ("Клиент: Луговстрой.", "Луговстрой"),
        ("- Звоню из компании Сосновка.", "Сосновка"),
        ("- Это Садовая, 15/2.", "/2"),
    ],
    ids=["speaker_lone_reply", "trigger_case", "house_fraction"],
)
def test_review_forms_masked(text: str, hidden: str) -> None:
    assert hidden not in clean(text)


def test_street_repeated_without_number_masked() -> None:
    scrubber = Scrubber()
    scrubber.scrub("- Адрес: Сиреневая, 12, корпус 2.")

    assert "Сиреневая" not in scrubber.scrub("- Да, Сиреневая, верно.")


def test_vocative_and_full_name_share_one_label() -> None:
    scrubber = Scrubber()
    first = scrubber.scrub("- Пётр, здравствуйте!")
    second = scrubber.scrub("- Это Пётр Иванов из отдела закупок.")

    assert first == "- [ИМЯ_1], здравствуйте!"
    assert "[ИМЯ_1]" in second
    assert "[ИМЯ_2]" not in second


@pytest.mark.parametrize(
    "text",
    [
        "Клиент: Да-да-да.",
        "- Объект большой. Коммутатор 48 портов.",
        "- Доставка завтра.\n- Ок.\n- Так.\n- Модель Коммутатор 3, без опций.",
        "- Адрес остановки скажу.\n- Сейчас, одну минутку.\n- Сейчас, сейчас.",
    ],
    ids=["repeat_reply", "ports", "hint_three_replies_back", "adverb_after_hint"],
)
def test_review_false_masks_absent(text: str) -> None:
    assert clean(text) == text


@pytest.mark.parametrize("unit", ["аБ", "Ааааа ", "Абв 1 "], ids=["glued", "words", "streets"])
def test_hostile_context_input_finishes_quickly(unit: str) -> None:
    started = time.monotonic()

    clean(unit * 20_000)

    assert time.monotonic() - started < 20


def test_org_trigger_does_not_cross_line() -> None:
    _, _, vocab = load_natasha()

    assert find_context("- Ваша компания\nКлиент: Алло, слушаю.", vocab) == []


@pytest.mark.parametrize(
    "text",
    [
        "- Мы официальный дилер завода Алтэк, сетевое оборудование.",
        "- Нужны КВ, GSM, LTE, Wi-Fi, NB и IOT.",
        "- Мы в реестре Минпромторга.",
        "- Ну, пойдёт.",
    ],
    ids=["factory_brand", "radio_terms", "registry", "verb_as_place"],
)
def test_brands_and_terms_kept(text: str) -> None:
    assert clean(text) == text


@pytest.mark.parametrize(
    ("word", "term"),
    [
        ("Элтексом", True),
        ("Алтэкс", True),
        ("Технологии", False),
        ("Технологиям", False),
        ("Отто", False),
        ("Отдел", False),
        ("Тао", False),
        ("Элтексовский", False),
    ],
)
def test_is_term_boundary(word: str, term: bool) -> None:
    assert is_term(word) is term


def test_own_company_kept_only_when_passed() -> None:
    text = "- Компания Лесовик Системс, добрый день."
    kept = Scrubber(keep=parse_terms("лесовик*\nсистемс")).scrub(text)

    assert kept == text
    assert "Лесовик" not in clean(text)


@pytest.mark.parametrize(
    ("text", "hidden"),
    [
        ("- Компания «Технологии», добрый день.", "Технологии"),
        ("- Звонил Иван Тао.\n- Да, Тао перезвонит.", "Тао"),
        ("- Звонил Отто Шмидт.", "Отто"),
    ],
    ids=["common_word_client", "name_homonym_repeat", "short_term_is_not_a_stem"],
)
def test_terms_do_not_unmask_pii(text: str, hidden: str) -> None:
    assert hidden not in clean(text)


@pytest.mark.parametrize(
    "text",
    [
        "- Мы находимся в городе Тверь.",
        "- Мы находимся в городе Мирный.",
        "- Доставка в Знаменск.",
        "- Работаем с Ромашкой.",
        "- Едем в Пойковский.",
    ],
)
def test_one_word_places_and_orgs_stay_masked(text: str) -> None:
    assert LABEL.search(clean(text))


def test_dot_is_never_a_name() -> None:
    assert "." in clean("- Да. Алло, Иван, здравствуйте!").split("[")[0]
