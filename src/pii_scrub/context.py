"""Call-context rules: personal data the NER misses in transcripts.

A name addressed at the start of a phrase, an address without «улица», a house number after a
place, a client name after «называется» or «комбинат», a hyphenated name and a one-word reply."""

import re

from natasha import MorphVocab

from pii_scrub.normalize import SPEAKERS, VALUES

# (start, stop, kind, key): scrub.py wraps it into a Finding.
Span = tuple[int, int, str, str]

TOKEN = re.compile(r"\[[А-ЯЁA-Z]+_\d+\]|[А-ЯЁа-яё0-9]+(?:-[А-ЯЁа-яё0-9]+)*|\S")
CAPITAL = re.compile(r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё][а-яё]*)*")
ACRONYM = re.compile(r"[А-ЯЁ]{2,6}")
SENTENCE_END = {".", "!", "?", "-", "—", ":", ";"}
# A quantity with a unit is neither a house number nor a number piece: «45 штук», «40 лет Октября».
# Endings are listed: «минутку» and «секундочку» are not units. Call as UNIT.match(text, pos).
UNIT = re.compile(
    r"[\s,.]*(?:штук[аи]?|шт|процент(?:а|ов)?|%|минут[аы]?|час(?:а|ов)?|секунд[аы]?|"
    r"дн(?:я|ей|и)|недел[еьиюя]|месяц(?:а|ев)?|лет|год(?:а|у)?|раз|метр(?:а|ов)?|м|км|кг|"
    r"тонн[аы]?|литр(?:а|ов)?|человек|позици[йия]|порт(?:а|ов)?|камер[аы]?|канал(?:а|ов)?|"
    r"устройств[оа]?|точ(?:ка|ки|ек)|тысяч\w*|миллион\w*|числ[ао]|звон(?:ок|ка|ков))(?!\w)",
    re.IGNORECASE,
)

_SMALL = "|".join(sorted((w for w, v in VALUES.items() if v < 1000), key=len, reverse=True))
# A house number: up to three digits with a letter or a number word below a thousand, then a
# building.
HOUSE = (
    rf"(?:\d{{1,3}}(?:-?[а-яА-Я](?![а-яА-Я])|/\d{{1,3}})?(?!\d)|(?:{_SMALL})(?:\s+(?:{_SMALL})){{0,2}})"
    rf"(?:,?\s*(?i:корпус|корп\.?|строение|стр\.?)\s*(?:\d{{1,3}}|{_SMALL}))?(?!\w)"
)
HOUSE_AFTER = re.compile(rf",?[ \t]*(?i:дом[ \t]*|д\.[ \t]*)?{HOUSE}")
STREET_WORD = re.compile(rf"(?P<street>[А-ЯЁ][а-яё]{{2,}}),?[ \t]+(?P<house>{HOUSE})")
BUILDING = re.compile(r"(?i:корпус|корп|строение|стр)")
ADDRESS_HINT = re.compile(r"(?i:адрес\w*|улиц\w*|находи\w+|расположен\w*|доставк\w*|объект\w*)")
HINT_WINDOW = 120
HINT_LINES = 2
NOT_STREET_POS = {"ADVB", "VERB", "INFN", "CONJ", "PRCL", "INTJ", "PREP", "NPRO", "PRED", "GRND"}
# A postcode: six digits near a city or an address, or after the word «индекс».
POSTCODE = re.compile(r"(?<!\d)[1-6]\d{5}(?!\d)")
POSTCODE_HINT = re.compile(r"(?i)индекс\w*[\s:,]*$")
POSTCODE_WINDOW = 20
PLACE_PREPOSITIONS = {"на", "по"}
PLACE_GRAMMEMES = {"Surn", "Geox"}
# Normal forms: «из компании Луговстрой», «для комбината Сосновка».
ORG_TRIGGERS = {
    "называться",
    "организация",
    "компания",
    "комбинат",
    "предприятие",
    "фирма",
    "подрядчик",
    "заказчик",
}
MAX_ORG_WORDS = 3
QUOTES = {"«": "»", '"': '"', "“": "”"}


def find_context(text: str, vocab: MorphVocab) -> list[Span]:
    tokens = list(TOKEN.finditer(text))
    return [
        *find_vocatives(text, tokens, vocab),
        *find_streets(text, vocab),
        *find_place_after_preposition(text, tokens, vocab),
        *find_orgs(text, tokens, vocab),
        *find_postcodes(text),
    ]


def phrase_start(text: str, tokens: list[re.Match[str]], i: int) -> bool:
    if i == 0 or "\n" in text[tokens[i - 1].end() : tokens[i].start()]:
        return True
    return tokens[i - 1].group() in SENTENCE_END


def has_grammeme(word: str, vocab: MorphVocab, grammemes: set[str]) -> bool:
    return any(grammemes & set(p.tag.grammemes) for p in vocab.parse(word))


def find_vocatives(text: str, tokens: list[re.Match[str]], vocab: MorphVocab) -> list[Span]:
    """«Пётр, здравствуйте!»: a name at the start of a phrase before a comma or an exclamation."""
    found = []
    for i, token in enumerate(tokens[:-1]):
        word = token.group()
        if (
            CAPITAL.fullmatch(word)
            and tokens[i + 1].group() in {",", "!"}
            and phrase_start(text, tokens, i)
            and has_grammeme(word, vocab, {"Name"})
        ):
            name = next(p for p in vocab.parse(word) if "Name" in p.tag.grammemes)
            found.append((token.start(), token.end(), "PERSON", name.normal_form))
    return found


def find_streets(text: str, vocab: MorphVocab) -> list[Span]:
    """«Сиреневая, 12, корпус 2», «Лесная 7»: a capitalized word and a house number without «улица».
    Needs a building, a street form (a feminine adjective, a surname) or the word «адрес» nearby."""
    found = []
    for match in STREET_WORD.finditer(text):
        street, stop = match.group("street"), match.end()
        if street.lower() in VALUES or UNIT.match(text, stop) or not_noun(street, vocab):
            continue
        shaped = any(
            ("ADJF" in p.tag and "femn" in p.tag) or {"Surn", "Name"} & set(p.tag.grammemes)
            for p in vocab.parse(street)
        )
        # A hint in this or the two previous replies: «Адрес?» - «Ремонт?» - «Лесная, семь».
        line_start = match.start()
        for _ in range(HINT_LINES + 1):
            line_start = text.rfind("\n", 0, max(line_start - 1, 0)) + 1
        window = max(match.start() - HINT_WINDOW, line_start)
        hint = ADDRESS_HINT.search(text, window, match.start())
        if shaped or hint or BUILDING.search(match.group("house")):
            found.append(
                (match.start(), stop, "ADDRESS", re.sub(r"\s+", " ", match.group().lower()))
            )
    return found


def not_noun(word: str, vocab: MorphVocab) -> bool:
    """«Сейчас, одну секунду»: an adverb, a verb or a pronoun is never a street."""
    return all(p.tag.POS in NOT_STREET_POS for p in vocab.parse(word))


def house_after(text: str, stop: int) -> int:
    """The end of a house number right after a place or an address, otherwise stop."""
    match = HOUSE_AFTER.match(text, stop)
    if not match or UNIT.match(text, match.end()):
        return stop
    return match.end()


def postcodes_near(text: str, start: int, stop: int) -> list[Span]:
    """A postcode within POSTCODE_WINDOW characters before or after a place."""
    found = []
    left = max(start - POSTCODE_WINDOW, 0)
    for match in POSTCODE.finditer(text, left, stop + POSTCODE_WINDOW):
        if match.end() <= start or match.start() >= stop:
            found.append((match.start(), match.end(), "ADDRESS", match.group()))
    return found


def find_postcodes(text: str) -> list[Span]:
    return [
        (m.start(), m.end(), "ADDRESS", m.group())
        for m in POSTCODE.finditer(text)
        if POSTCODE_HINT.search(text, max(m.start() - POSTCODE_WINDOW, 0), m.start())
    ]


def find_place_after_preposition(
    text: str, tokens: list[re.Match[str]], vocab: MorphVocab
) -> list[Span]:
    """«на Ломоносовской»: a capitalized surname or place name after «на», «по» inside a phrase."""
    found = []
    for i, token in enumerate(tokens[1:], start=1):
        word = token.group()
        if (
            tokens[i - 1].group() in PLACE_PREPOSITIONS
            and CAPITAL.fullmatch(word)
            and has_grammeme(word, vocab, PLACE_GRAMMEMES)
        ):
            found.append((token.start(), token.end(), "PLACE", vocab.parse(word)[0].normal_form))
    return found


def find_orgs(text: str, tokens: list[re.Match[str]], vocab: MorphVocab) -> list[Span]:
    """A client name without «ООО»: after «называется», «комбинат»; hyphenated; a reply of one
    word unknown to the dictionary."""
    found = []
    lone = lone_lines(text)
    for i, token in enumerate(tokens):
        word = token.group()
        if i + 1 < len(tokens) and vocab.parse(word)[0].normal_form in ORG_TRIGGERS:
            span = org_after(text, tokens, i + 1)
            if span:
                found.append(span)
        elif "-" in word and sum(part[:1].isupper() for part in word.split("-")) >= 2:
            found.append((token.start(), token.end(), "ORG", word.lower()))
        elif token.start() in lone and lone_unknown(word, vocab):
            found.append((token.start(), token.end(), "ORG", word.lower()))
    return found


def org_after(text: str, tokens: list[re.Match[str]], first: int) -> Span | None:
    opening = tokens[first].group()
    if opening in QUOTES:
        stop = text.find(QUOTES[opening], tokens[first].end())
        if stop < 0 or "\n" in text[tokens[first].end() : stop]:
            return None
        return (tokens[first].start(), stop + 1, "ORG", text[tokens[first].end() : stop].lower())
    words = []
    for token in tokens[first : first + MAX_ORG_WORDS]:
        word = token.group()
        if (
            "\n" in text[tokens[first - 1].end() : token.start()]
            or not (CAPITAL.fullmatch(word) or ACRONYM.fullmatch(word))
            or word.lower() in VALUES
        ):
            break
        words.append(token)
    if not words:
        return None
    key = " ".join(t.group().lower() for t in words)
    return (words[0].start(), words[-1].end(), "ORG", key)


def lone_lines(text: str) -> set[int]:
    """Starts of words that make a reply alone: «- Луговстрой.», «Клиент: Луговстрой.»."""
    starts = set()
    for line in re.finditer(r"[^\n]+", text):
        words = [m for m in TOKEN.finditer(line.group()) if m.group()[0].isalnum()]
        if words and words[0].group().lower() in SPEAKERS:
            words = words[1:]
        if len(words) == 1:
            starts.add(line.start() + words[0].start())
    return starts


def lone_unknown(word: str, vocab: MorphVocab) -> bool:
    """A capitalized word missing from the dictionary; «Да-да-да» and other repeats are not
    names."""
    return (
        CAPITAL.fullmatch(word) is not None
        and "-" not in word
        and len(word) >= 4
        and not vocab.word_is_known(word.lower())
    )
