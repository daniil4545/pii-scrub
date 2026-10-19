"""Числа словами в цифры с картой смещений к исходному тексту.

Проекция нужна только для поиска. Найденный спан проекции переносится на целые исходные куски,
поэтому маска всегда закрывает слова, а не их цифровой образ.
"""

import re
from bisect import bisect_left
from dataclasses import dataclass

UNITS = {
    "ноль": 0,
    "нуль": 0,
    "один": 1,
    "одна": 1,
    "одну": 1,
    "одно": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
}
TEENS = {
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "тринадцать": 13,
    "четырнадцать": 14,
    "пятнадцать": 15,
    "шестнадцать": 16,
    "семнадцать": 17,
    "восемнадцать": 18,
    "девятнадцать": 19,
}
TENS = {
    "двадцать": 20,
    "тридцать": 30,
    "сорок": 40,
    "пятьдесят": 50,
    "шестьдесят": 60,
    "семьдесят": 70,
    "восемьдесят": 80,
    "девяносто": 90,
}
HUNDREDS = {
    "сто": 100,
    "двести": 200,
    "триста": 300,
    "четыреста": 400,
    "пятьсот": 500,
    "шестьсот": 600,
    "семьсот": 700,
    "восемьсот": 800,
    "девятьсот": 900,
}
SCALES = {
    "тысяча": 1000,
    "тысячи": 1000,
    "тысяч": 1000,
    "миллион": 10**6,
    "миллиона": 10**6,
    "миллионов": 10**6,
}
# Класс слова: после старшего класса может идти младший (сто двадцать пять), но не наоборот.
CLASSES = {
    **dict.fromkeys(HUNDREDS, 4),
    **dict.fromkeys(TENS, 3),
    **dict.fromkeys(TEENS, 2),
    **dict.fromkeys(UNITS, 1),
}
VALUES = {**UNITS, **TEENS, **TENS, **HUNDREDS}
# Подтверждения, которые вставляют в диктовку: «так», «да», «угу». Союзы («и») сюда не входят:
# они склеивали бы обычные числа в предложении.
FILLERS = {
    "так",
    "да",
    "угу",
    "ага",
    "ну",
    "понял",
    "поняла",
    "записываю",
    "записал",
    "записала",
    "хорошо",
    "ладно",
    "стоп",
    "секунду",
    "секундочку",
    "повторите",
    "окей",
    "значит",
    "вот",
    "эм",
    "нет",
    "верно",
    "слушаю",
    "продолжайте",
}
SPEAKERS = {"менеджер", "клиент", "оператор", "абонент"}
MAX_FILLERS = 3
TOKEN = re.compile(r"[А-Яа-яЁё]+|\d+|\S")
# Знаки внутри серии цифровых токенов: «89 12, 345-67»; точка и двоеточие - даты и время.
DIGIT_JOINERS = {",", "-"}
# Конец предложения рвёт число: «тридцать. Две тысячи» - не «тридцать две тысячи».
SENTENCE_END = {".", "!", "?", ";"}
# Индексы проекций в build_views.
VIEW_TIGHT, VIEW_RELAY, VIEW_SPLIT = 0, 1, 2


@dataclass(frozen=True)
class Segment:
    """Один кусок: диапазон в исходном тексте и в проекции."""

    start: int
    stop: int
    proj_start: int
    proj_stop: int
    scaled: bool = False  # число с «тысяча» или «миллион»: сумма или количество
    plain: bool = False  # цифровой токен исходного текста, не слова


@dataclass(frozen=True)
class View:
    # В text цифры чисел с «тысяча» и «миллион» заменены на «|»: суммы номерами не бывают.
    text: str
    segments: list[Segment]
    chains: list[list[Segment]]
    starts: list[int]  # proj_start каждого сегмента, для двоичного поиска


@dataclass(frozen=True)
class Item:
    """Число из слов или цифровой токен; first и last - индексы первого и последнего токена."""

    digits: str
    first: int
    last: int
    scaled: bool = False
    plain: bool = False


@dataclass(frozen=True)
class Run:
    """Подряд идущие числа: слова и цифровые токены."""

    tokens: list[tuple[str, int]]
    first: int
    last: int

    @property
    def has_words(self) -> bool:
        return any(not word.isdigit() for word, _ in self.tokens)


def classify(word: str) -> str:
    if word.isdigit():
        return "digits"
    if word in VALUES or word in SCALES:
        return "number"
    if word in SPEAKERS:
        return "speaker"
    if word in FILLERS:
        return "filler"
    return "other"


def cannot_continue(prev_class: int, cls: int) -> bool:
    """Слово не продолжает число: «двадцать пять» - одно число, «пять пять» - два."""
    return cls >= prev_class > 0 or prev_class == 2 or (prev_class == 3 and cls == 2)


def parse_run(tokens: list[tuple[str, int]], split_hundreds: bool = False) -> list[Item]:
    """Разбирает подряд идущие слова-числа и цифровые токены на числа.

    split_hundreds: «двести семьдесят девять» - два числа (200 и 79), а не одно (279)."""
    items: list[Item] = []
    total = group = 0
    prev_class = 0
    first = -1
    last = -1
    scaled = False

    def flush() -> None:
        nonlocal total, group, prev_class, first, scaled
        if first >= 0:
            items.append(Item(str(total + group), first, last, scaled))
        total = group = prev_class = 0
        first = -1
        scaled = False

    for position, (word, index) in enumerate(tokens):
        if word.isdigit():
            flush()
            items.append(Item(word, index, index, plain=True))
            continue
        if word in SCALES:
            if first < 0:
                first = index
            total += (group or 1) * SCALES[word]
            scaled = True
            group = 0
            prev_class = 0
            last = index
            continue
        cls = CLASSES[word]
        zero = VALUES[word] == 0
        if first >= 0 and (zero or cannot_continue(prev_class, cls)):
            flush()
        if first < 0:
            first = index
        group += VALUES[word]
        prev_class = cls
        last = index
        next_word = tokens[position + 1][0] if position + 1 < len(tokens) else ""
        if zero or (split_hundreds and cls == 4 and next_word not in SCALES):
            flush()
    flush()
    return items


def runs_of(words: list[tuple[str, str]]) -> list[Run]:
    """Серии подряд идущих чисел. Серию рвёт конец предложения; между двумя цифровыми токенами
    рвёт любой знак, кроме запятой и дефиса: даты и время не склеиваются."""
    runs: list[Run] = []
    current: list[tuple[str, int]] = []
    pending: list[str] = []
    for index, (word, kind) in enumerate(words):
        if kind == "punct":
            pending.append(word)
            continue
        if kind in ("number", "digits"):
            both_digits = bool(current) and current[-1][0].isdigit() and word.isdigit()
            digit_break = both_digits and not set(pending) <= DIGIT_JOINERS
            if current and (digit_break or SENTENCE_END & set(pending)):
                runs.append(Run(current, current[0][1], current[-1][1]))
                current = []
            current.append((word, index))
        elif current:
            runs.append(Run(current, current[0][1], current[-1][1]))
            current = []
        pending = []
    if current:
        runs.append(Run(current, current[0][1], current[-1][1]))
    return runs


def build_views(text: str) -> list[View]:
    """Три проекции: плотные серии, серии через подтверждения собеседника (по ней считается
    остаток) и те же серии с раздельными сотнями. Индексы - VIEW_TIGHT, VIEW_RELAY, VIEW_SPLIT."""
    matches = list(TOKEN.finditer(text))
    words = []
    for match in matches:
        raw = match.group().lower()
        kind = "punct" if not raw[0].isalnum() else classify(raw)
        words.append((raw, kind))
    runs = runs_of(words)
    tight = [parse_run(run.tokens) for run in runs]
    split = [parse_run(run.tokens, split_hundreds=True) for run in runs]
    return [
        render(matches, tight),
        render(matches, join_runs(words, runs, tight)),
        render(matches, join_runs(words, runs, split)),
    ]


def join_runs(
    words: list[tuple[str, str]], runs: list[Run], parsed: list[list[Item]]
) -> list[list[Item]]:
    """Склеивает соседние серии, если между ними подтверждения или метки говорящего."""
    merged: list[list[Item]] = []
    current: list[Item] = []
    prev_last = -1
    previous = runs[0] if runs else None
    for run, items in zip(runs, parsed, strict=True):
        if current and gap_ok(words, prev_last, run.first, previous.has_words or run.has_words):
            current += items
        else:
            if current:
                merged.append(current)
            current = list(items)
        prev_last = run.last
        previous = run
    if current:
        merged.append(current)
    return merged


def gap_ok(words: list[tuple[str, str]], prev_last: int, first: int, has_words: bool) -> bool:
    """Между сериями подтверждения и метки говорящего (не больше MAX_FILLERS слов) либо одна
    пунктуация, если в сериях есть слова-числа. Две цифровые серии через знак - дата или время."""
    fillers = 0
    seen = has_words
    for _, kind in words[prev_last + 1 : first]:
        if kind == "filler":
            fillers += 1
            seen = True
        elif kind == "speaker":
            seen = True
        elif kind != "punct":
            return False
    return seen and fillers <= MAX_FILLERS


def render(matches: list[re.Match[str]], chains: list[list[Item]]) -> View:
    parts: list[str] = []
    segments: list[Segment] = []
    grouped: list[list[Segment]] = []
    position = 0
    for chain in chains:
        group: list[Segment] = []
        for item in chain:
            start, stop = matches[item.first].start(), matches[item.last].end()
            length = len(item.digits)
            group.append(Segment(start, stop, position, position + length, item.scaled, item.plain))
            parts.append("|" * length if item.scaled else item.digits)
            position += length
        segments += group
        grouped.append(group)
        parts.append(" | ")
        position += 3
    return View("".join(parts), segments, grouped, [s.proj_start for s in segments])


def map_span(view: View, start: int, stop: int) -> tuple[int, int] | None:
    """Переносит спан проекции на целые исходные куски."""
    low = bisect_left(view.starts, stop)
    hit = []
    for i in range(low - 1, -1, -1):
        segment = view.segments[i]
        if segment.proj_stop <= start:
            break
        hit.append(segment)
    if not hit:
        return None
    return min(s.start for s in hit), max(s.stop for s in hit)
