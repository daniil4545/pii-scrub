"""Number words to digits, with an offset map back to the source text.

The projection is used only for searching. A span found in the projection is carried over to whole
source pieces,
so a mask always covers the words, not their digit image.
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
# Word class: a lower class may follow a higher one (сто двадцать пять), but not the other way
# round.
CLASSES = {
    **dict.fromkeys(HUNDREDS, 4),
    **dict.fromkeys(TENS, 3),
    **dict.fromkeys(TEENS, 2),
    **dict.fromkeys(UNITS, 1),
}
VALUES = {**UNITS, **TEENS, **TENS, **HUNDREDS}
# Acknowledgements inserted into dictation: «так», «да», «угу». Conjunctions («и») are not included:
# they would glue ordinary numbers of a sentence together.
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
# Marks inside a series of digit tokens: «89 12, 345-67»; a dot and a colon belong to dates and
# times.
DIGIT_JOINERS = {",", "-"}
# A sentence end breaks a number: «тридцать. Две тысячи» is not «тридцать две тысячи».
SENTENCE_END = {".", "!", "?", ";"}
# Projection indices in build_views.
VIEW_TIGHT, VIEW_RELAY, VIEW_SPLIT = 0, 1, 2


@dataclass(frozen=True)
class Segment:
    """One piece: a range in the source text and in the projection."""

    start: int
    stop: int
    proj_start: int
    proj_stop: int
    scaled: bool = False  # a number with «тысяча» or «миллион»: an amount or a quantity
    plain: bool = False  # a digit token of the source text, not words


@dataclass(frozen=True)
class View:
    # In text the digits of numbers with «тысяча» and «миллион» are replaced by «|»: amounts are
    # never ID numbers.
    text: str
    segments: list[Segment]
    chains: list[list[Segment]]
    starts: list[int]  # proj_start of every segment, for binary search


@dataclass(frozen=True)
class Item:
    """A number made of words, or a digit token; first and last are the indices of its first and
    last token."""

    digits: str
    first: int
    last: int
    scaled: bool = False
    plain: bool = False


@dataclass(frozen=True)
class Run:
    """Consecutive numbers: words and digit tokens."""

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
    """A word does not continue the number: «двадцать пять» is one number, «пять пять» is two."""
    return cls >= prev_class > 0 or prev_class == 2 or (prev_class == 3 and cls == 2)


def parse_run(tokens: list[tuple[str, int]], split_hundreds: bool = False) -> list[Item]:
    """Parses consecutive number words and digit tokens into numbers.

    split_hundreds: «двести семьдесят девять» is two numbers (200 and 79), not one (279)."""
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
    """Series of consecutive numbers. A sentence end breaks a series; between two digit tokens
    any mark except a comma and a hyphen breaks it: dates and times are not glued together."""
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
    """Three projections: tight series, series bridged by the other speaker's acknowledgements (the
    residue
    is counted on it) and the same series with split hundreds. Indices: VIEW_TIGHT, VIEW_RELAY,
    VIEW_SPLIT."""
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
    """Joins neighbouring series when acknowledgements or speaker labels sit between them."""
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
    """Between the series there are acknowledgements and speaker labels (at most MAX_FILLERS words)
    or a single
    punctuation mark if the series contain number words. Two digit series across a mark are a date
    or a time."""
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
    """Carries a projection span over to whole source pieces."""
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
