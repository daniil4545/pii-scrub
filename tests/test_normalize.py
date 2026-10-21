import pytest

from pii_scrub.normalize import build_views, map_span


def tight(text: str) -> str:
    return build_views(text)[0].text


@pytest.mark.parametrize(
    ("text", "digits"),
    [
        (
            "восемь девятьсот двенадцать триста сорок пять шестьдесят семь восемьдесят девять",
            "89123456789",
        ),
        ("девять один два", "912"),
        ("ноль пять", "05"),
        ("две тысячи двадцать пять", "||||"),  # «тысяча» hides the digits: an amount
        ("сто двадцать пять", "125"),
        ("восемь девятьсот двенадцать 345 67 89", "89123456789"),
    ],
)
def test_number_words_become_digits(text: str, digits: str) -> None:
    assert tight(text).split(" | ")[0] == digits


def test_hundreds_can_be_split_in_the_third_view() -> None:
    views = build_views("двести семьдесят девять пятьдесят два")

    assert views[0].text.startswith("27952")
    assert views[2].text.startswith("2007952")


def test_hundreds_stay_with_scale_in_the_third_view() -> None:
    views = build_views("один миллион двести тысяч рублей")

    assert views[0].text.split(" | ")[0] == "|||||||"
    assert views[2].text.split(" | ")[0] == "|||||||"


def test_plain_digit_tokens_make_a_chain_but_dates_and_times_do_not_join() -> None:
    assert build_views("Заказ 12345 678")[0].text.split(" | ")[0] == "12345678"
    assert build_views("Звонил 12.10.2026 в 15:30")[0].text.count(" | ") == 5


def test_digit_pieces_join_across_replies() -> None:
    text = "Клиент: 8 912\nМенеджер: так\nКлиент: 345 67\nМенеджер: да\nКлиент: 89"

    assert build_views(text)[1].text == "89123456789 | "


def test_conjunction_does_not_join_numbers() -> None:
    assert build_views("три и пять")[1].text.count(" | ") == 2


def test_fillers_and_speaker_labels_join_runs_in_the_second_view() -> None:
    text = "восемь девятьсот двенадцать\nМенеджер: так\nКлиент: триста сорок пять"
    views = build_views(text)

    assert views[0].text.count(" | ") == 2
    assert views[1].text == "8912345 | "


def test_words_between_runs_that_are_not_fillers_keep_runs_apart() -> None:
    views = build_views("триста штук по пять тысяч")

    assert views[1].text.count(" | ") == 2


def test_span_starting_inside_a_chunk_covers_the_whole_chunk() -> None:
    text = "номер девятьсот двенадцать триста сорок пять"
    view = build_views(text)[0]

    # projection "912345": a span from the second digit «912» touches the whole piece «девятьсот двенадцать»
    start, stop = map_span(view, 1, 4)

    assert text[start:stop] == "девятьсот двенадцать триста сорок пять"


def test_span_outside_chains_maps_to_nothing() -> None:
    view = build_views("девять один два")[0]

    assert map_span(view, 50, 60) is None
