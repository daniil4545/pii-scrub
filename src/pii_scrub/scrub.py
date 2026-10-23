"""Finding personal data in text and replacing it with stable labels."""

import re
import warnings
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files

import phonenumbers
from natasha import Doc, MorphVocab, NewsEmbedding, NewsNERTagger, Segmenter
from natasha.span import Span
from stdnum import luhn
from stdnum.ru import inn, ogrn

from pii_scrub.context import CAPITAL, TOKEN, UNIT, find_context, house_after, postcodes_near
from pii_scrub.normalize import VIEW_RELAY, View, build_views, map_span

PREFIXES = {
    "PERSON": "ИМЯ",
    "PLACE": "МЕСТО",
    "ORG": "ОРГ",
    "PHONE": "ТЕЛЕФОН",
    "EMAIL": "EMAIL",
    "CARD": "КАРТА",
    "INN": "ИНН",
    "OGRN": "ОГРН",
    "ACCOUNT": "СЧЁТ",
    "PASSPORT": "ПАСПОРТ",
    "SNILS": "СНИЛС",
    "ADDRESS": "АДРЕС",
    "BIRTHDATE": "РОЖДЕНИЕ",
    "NICK": "НИК",
    "SITE": "САЙТ",
    "NUMBER": "ЧИСЛА",
}
NER_KINDS = {"PER": "PERSON", "LOC": "PLACE", "ORG": "ORG"}
PERSON_GRAMMEMES = {"Name", "Surn", "Patr"}
# Parts of speech that, together with the stop list, cut «Подскажите» and «Да» out of a name span.
NOT_NAME_POS = {
    "VERB",
    "INFN",
    "PRTF",
    "PRTS",
    "GRND",
    "CONJ",
    "PRCL",
    "INTJ",
    "PREP",
    "NPRO",
    "ADVB",
    "COMP",
    "PRED",
}
MAX_INPUT_BYTES = 1_000_000
STOP_SUFFIX = 2
TERM_SUFFIX = 4
TERM_WORD = re.compile(r"[a-zа-яё0-9]+")
# Kinds found by the NER: a brand or a term from data/terms.txt is not masked.
TERM_KINDS = {"PERSON", "PLACE", "ORG"}

LABEL = re.compile(r"\[[А-ЯЁA-Z]+_\d+\]")
# A long digit chain: 10 digits or more. The kind comes from the length, the checksum from the
# validator.
DIGIT_RUN = re.compile(r"(?<!\d)\d{10,20}(?!\d)")
RUN_KINDS = {10: "INN", 12: "INN", 13: "OGRN", 15: "OGRN", 20: "ACCOUNT"}
VALIDATORS = {"INN": inn.is_valid, "OGRN": ogrn.is_valid}
CARD = re.compile(r"(?<!\d)\d{4}(?:[ -]?\d{4}){3}(?!\d)")
SNILS = re.compile(r"(?<!\d)\d{3}[- ]?\d{3}[- ]?\d{3}[- ]?\d{2}(?!\d)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
PASSPORT = re.compile(r"(?<!\d)\d{4} \d{6}(?!\d)")
NICK = re.compile(r"(?<![\w@])@[A-Za-z][A-Za-z0-9_]{2,}")
# A domain without a zone list would match «call.txt»: only frequent zones are taken.
SITE = re.compile(
    r"(?<![\w.-])(?:https?://)?(?:[\w-]+\.)+(?:ru|рф|su|com|net|org|info|biz|io|me|by|kz|ua)"
    r"(?![\w-])",
    re.IGNORECASE,
)
REGEX_KINDS = [("EMAIL", EMAIL), ("PASSPORT", PASSPORT), ("NICK", NICK), ("SITE", SITE)]

# Address: a street marker, a capitalized name, then house, building, flat.
STREET = (
    r"(?i:улиц\w*|ул\.?|проспект\w*|пр-кт\.?|пр-т\.?|пр\.|переул\w*|пер\.|бульвар\w*|"
    r"бул\.|б-р|шосс?е\w*|ш\.|набережн\w*|наб\.|площад\w*|пл\.|аллея|алл\.|проезд\w*|"
    r"пр-д|тупик\w*)"
)
ADDRESS = re.compile(
    rf"(?<![\w.])(?:{STREET}[ \t]+)+(?:[А-ЯЁ][\w-]*[ \t]?){{1,3}}"
    r"(?:,?[ \t]*(?i:дом|д\.?)[ \t]*\d+[А-Яа-я]?(?:/\d+)?)?"
    r"(?:,?[ \t]*(?i:корпус|корп\.?|строение|стр\.?)[ \t]*\d+)?"
    r"(?:,?[ \t]*(?i:квартира|кв\.?|офис|оф\.?)[ \t]*\d+)?"
)
# Birth date: a date in the same phrase after «родился», «рождения».
BIRTH_TRIGGER = re.compile(r"(?i:родил\w*|рождени\w+)")
MONTHS = "января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря"
BIRTH_DATE = re.compile(
    rf"\d{{1,2}}[./-]\d{{1,2}}[./-]\d{{2,4}}|(?:[А-Яа-яЁё]+|\d{{1,2}})\s+(?:{MONTHS})"
    r"(?:\s+\d{4}(?:\s+года?)?|(?:\s+[а-яё]+){0,5}?\s+года?)?"
)
BIRTH_WINDOW = 80

# E-mail in words: «иван точка петров собака мэйл точка ру».
WORD = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")
MAIL_AT = "собака"
MAIL_DOT = "точка"
MAIL_ZONES = {"ру", "ком", "нет", "орг", "юа", "бай", "кз", "инфо", "рф", "су", "ай", "ио"}
# A zone written in Latin letters: «собака gmail точка com», «собака gmail.com».
MAIL_LATIN_ZONES = {"ru", "com", "net", "org", "info", "su", "ua", "by", "kz"}
MAIL_DOMAIN = re.compile(
    rf"\s+[\w-]+(?:\.[\w-]+)*\.(?:{'|'.join(sorted(MAIL_LATIN_ZONES))}|рф)\b", re.IGNORECASE
)
MAIL_LEFT_STOP = {
    "почта",
    "адрес",
    "мой",
    "моя",
    "у",
    "меня",
    "это",
    "на",
    "пишите",
    "отправьте",
    "такая",
    "такой",
    "имейл",
    "емейл",
    "мейл",
    "email",
    "почту",
    "адреса",
}
MAIL_LEFT = 8
MAIL_RIGHT = 3
# «собака», up to three words, «точка» and a zone or a domain with a dot: the rest of an e-mail
# dictated in words.
_ZONES = "|".join(sorted(MAIL_ZONES | MAIL_LATIN_ZONES))
MAIL_WORDS = re.compile(
    rf"\b{MAIL_AT}\b(?:(?:\W+\w+){{0,{MAIL_RIGHT}}}?\W+{MAIL_DOT}\W+(?:{_ZONES})\b"
    rf"|{MAIL_DOMAIN.pattern})",
    re.IGNORECASE,
)

# Residue after masking: digits written as digits or as words, and an e-mail in words.
LONG_NUMBER = re.compile(r"\d[\d ()-]*\d")
LONG_NUMBER_DIGITS = 7
CURRENCY = re.compile(
    r"^[\s,.]*(?:[А-Яа-яЁё]+\s+){0,2}"
    r"(?:руб|₽|\$|€|р\b|тыс|млн|миллион|долл|евро|копе|тенге|юан|гривн|сом\b|лир|фунт|иен)",
    re.IGNORECASE,
)
# Dates written with digits and hyphens, and times, are never a residue.
DATE_ANY = re.compile(
    r"\b\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\b"
    r"|\b(?:0?[1-9]|[12]\d|3[01])-(?:0?[1-9]|1[0-2])-(?:\d{4}|\d{2})\b"
)
# A service identifier (transcript_id) is neither a number nor a residue.
UUID = re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", re.IGNORECASE)
# Pieces of a dictation: a short number next to a masked number, an e-mail or a trigger word.
TAIL_KINDS = {"PHONE", "CARD", "INN", "OGRN", "ACCOUNT", "PASSPORT", "SNILS", "NUMBER"}
TAIL_KINDS |= {"EMAIL", "SITE"}
TAIL_TRIGGER = re.compile(r"(?i)(?<!\w)(?:инн|огрн|снилс|телефон\w*|паспорт\w*|почт\w*)(?!\w)")
# ASR glue «НаКамчатку»: the words are split by a space for the NER.
GLUED = re.compile(r"(?<=[а-яё])(?=[А-ЯЁ])")
# A repeat of a name found earlier: a capitalized person, place or organization in any line.
REPEAT_KINDS = {"PERSON", "PLACE", "ORG", "ADDRESS"}
REPEAT_GRAMMEMES = {"Name", "Surn", "Patr", "Geox", "Orgn"}
# A house number right after a place, an address or a street taken for a surname: «[МЕСТО_1], 11».
HOUSE_KINDS = {"PLACE", "ADDRESS", "PERSON"}
POSTCODE_KINDS = {"PLACE", "ADDRESS"}
# A single digit is a piece only right next to a number or to «собака» in the same line:
# «семь, [ЧИСЛА_1]», «[ЧИСЛА_1]: 3».
SINGLE_TRIGGER = re.compile(r"(?i)(?<!\w)собак\w*(?!\w)")
SINGLE_WINDOW = 60
TAIL_DIGITS = 2
TAIL_WINDOW = 300
TAIL_LINES = 3
MAX_WINDOW_RUN = 40
BLANK = "\x00"
WINDOW_LENGTHS = (10, 11, 12)
WINDOW_RUN = re.compile(rf"(?<!\d)\d{{11,{MAX_WINDOW_RUN}}}(?!\d)")


class ScrubRefused(Exception):
    """Scrubbing refused: the text must not be passed on. The message carries no input text."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Finding:
    start: int
    stop: int
    kind: str  # PERSON, PLACE, ORG, PHONE, EMAIL, CARD, INN, OGRN, ACCOUNT, PASSPORT, SNILS, ...
    key: str  # key of the stable label


@cache
def load_natasha() -> tuple[Segmenter, NewsNERTagger, MorphVocab]:
    with warnings.catch_warnings():
        # pymorphy2 imports pkg_resources; setuptools<81 is pinned in the dependencies.
        warnings.filterwarnings("ignore", message="pkg_resources is deprecated")
        return Segmenter(), NewsNERTagger(NewsEmbedding()), MorphVocab()


def parse_terms(text: str) -> tuple[str, ...]:
    """Lines of a word list: empty lines and «#» comments are skipped, case does not matter."""
    return tuple(
        line.strip().lower() for line in text.splitlines() if line.strip() and line[0] != "#"
    )


@cache
def wordlist(name: str) -> tuple[str, ...]:
    return parse_terms(files("pii_scrub").joinpath(f"data/{name}").read_text(encoding="utf-8"))


def stoplist() -> tuple[str, ...]:
    return wordlist("stoplist.txt")


def term_stem(word: str, term: str) -> bool:
    """An ending is allowed only for a stem with an asterisk: «элтекс*» gives «Элтексом»."""
    stem = term.removesuffix("*")
    return stem != term and word.startswith(stem) and len(word) <= len(stem) + TERM_SUFFIX


def is_term(text: str, keep: tuple[str, ...] = ()) -> bool:
    """A brand or a radio term from data/terms.txt, or a caller word (the own company): every
    word of the finding is on the list."""
    terms = (*wordlist("terms.txt"), *keep)
    words = TERM_WORD.findall(text.lower())
    return bool(words) and all(any(w == t or term_stem(w, t) for t in terms) for w in words)


def is_stop(text: str) -> bool:
    words = text.lower().split()
    return bool(words) and all(
        any(w.startswith(s) and len(w) <= len(s) + STOP_SUFFIX for s in stoplist()) for w in words
    )


def snils_valid(digits: str) -> bool:
    if len(digits) != 11:
        return False
    total = sum(int(d) * w for d, w in zip(digits[:9], range(9, 0, -1), strict=True))
    check = total if total < 100 else (0 if total in (100, 101) else total % 101 % 100)
    return check == int(digits[9:])


def find_numbers(text: str) -> list[Finding]:
    """Numbers with a checksum (INN, OGRN, card, SNILS) and a 20-digit account."""
    found = []
    for match in DIGIT_RUN.finditer(text):
        kind = RUN_KINDS.get(len(match.group()))
        if kind and VALIDATORS.get(kind, lambda _: True)(match.group()):
            found.append(Finding(match.start(), match.end(), kind, match.group()))
    for match in CARD.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if luhn.is_valid(digits):
            found.append(Finding(match.start(), match.end(), "CARD", digits))
    for match in SNILS.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if snils_valid(digits):
            found.append(Finding(match.start(), match.end(), "SNILS", digits))
    return found


def find_phones(text: str) -> list[Finding]:
    return [
        Finding(
            match.start,
            match.end,
            "PHONE",
            phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164),
        )
        for match in phonenumbers.PhoneNumberMatcher(text, "RU")
    ]


def phone_key(digits: str) -> str | None:
    try:
        number = phonenumbers.parse(digits, "RU")
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(number):
        return None
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def find_windows(view: View) -> list[Finding]:
    """Windows over a digit chain: an extra digit next to a number does not hide the number."""
    found = []
    for run in WINDOW_RUN.finditer(view.text):
        digits = run.group()
        for length in WINDOW_LENGTHS:
            for i in range(len(digits) - length + 1):
                kind, key = window_kind(digits[i : i + length])
                if kind:
                    found.append(Finding(run.start() + i, run.start() + i + length, kind, key))
    return found


def window_kind(digits: str) -> tuple[str, str]:
    if len(digits) in (10, 12) and inn.is_valid(digits):
        return "INN", digits
    if len(digits) == 11 and snils_valid(digits):
        return "SNILS", digits
    if len(digits) in (10, 11) and (key := phone_key(digits)):
        return "PHONE", key
    return "", ""


def find_spoken_numbers(text: str) -> list[Finding]:
    """Numbers in words and in pieces: search on the projection, spans are carried over to whole
    source pieces."""
    found = []
    for view in build_views(blank_service(text)):
        if not view.segments:
            continue
        for finding in [*find_numbers(view.text), *find_phones(view.text), *find_windows(view)]:
            span = map_span(view, finding.start, finding.stop)
            if span:
                found.append(Finding(span[0], span[1], finding.kind, finding.key))
    return found


def find_spoken_emails(text: str) -> list[Finding]:
    """A pair «собака ... точка зона» or «собака домен.зона» and up to eight words to the left of
    it."""
    words = list(WORD.finditer(text))
    lowered = [w.group().lower() for w in words]
    found = []
    for i, word in enumerate(lowered):
        if word != MAIL_AT:
            continue
        zone = next(
            (
                j
                for j in range(i + 1, min(i + MAIL_RIGHT + 3, len(words) - 1))
                if lowered[j] == MAIL_DOT and lowered[j + 1] in MAIL_ZONES | MAIL_LATIN_ZONES
            ),
            None,
        )
        domain = MAIL_DOMAIN.match(text, words[i].end())
        if zone is None and domain is None:
            continue
        stop_at = words[zone + 1].end() if zone is not None else domain.end()
        left = i
        while left > 0 and i - left < MAIL_LEFT and lowered[left - 1] not in MAIL_LEFT_STOP:
            if re.search(r"[.!?:,\n]", text[words[left - 1].end() : words[left].start()]):
                break
            left -= 1
        start, stop = words[left].start(), stop_at
        found.append(Finding(start, stop, "EMAIL", re.sub(r"\s+", " ", text[start:stop].lower())))
    return found


def find_address_and_birth(text: str) -> list[Finding]:
    found = [
        Finding(m.start(), m.end(), "ADDRESS", re.sub(r"\s+", " ", m.group().lower()))
        for m in ADDRESS.finditer(text)
    ]
    for trigger in BIRTH_TRIGGER.finditer(text):
        end = text.find("\n", trigger.end())
        window = text[
            trigger.end() : min(end if end >= 0 else len(text), trigger.end() + BIRTH_WINDOW)
        ]
        date = BIRTH_DATE.search(window)
        if date:
            start = trigger.end() + date.start()
            found.append(
                Finding(start, trigger.end() + date.end(), "BIRTHDATE", date.group().lower())
            )
    return found


def find_patterns(text: str) -> list[Finding]:
    found = []
    for kind, pattern in REGEX_KINDS:
        for match in pattern.finditer(text):
            key = re.sub(r"\D", "", match.group()) if kind == "PASSPORT" else match.group().lower()
            found.append(Finding(match.start(), match.end(), kind, key))
    return found


def is_name_token(word: str, vocab: MorphVocab) -> bool:
    """A word is not a name only if it has no parse with name grammemes and all parses are verbs,
    particles or pronouns. A rare surname without a parse stays a name: a leak is worse than an
    extra mask."""
    if is_stop(word) or not any(c.isalpha() for c in word):
        return False
    parses = vocab.parse(word)
    if any(PERSON_GRAMMEMES & set(p.tag.grammemes) for p in parses):
        return True
    return not all(p.tag.POS in NOT_NAME_POS or "Apro" in p.tag.grammemes for p in parses)


def span_key(tokens: list, kind: str, vocab: MorphVocab) -> str:
    """Normal forms of the words: «Ивану Сергеевичу» and «Иван Сергеевич» give one key."""
    words = []
    for token in tokens:
        parses = vocab.parse(token.text)
        if kind == "PER":
            # Words without the grammemes Name, Surn, Patr stay out of the key.
            parses = [p for p in parses if PERSON_GRAMMEMES & set(p.tag.grammemes)]
        if parses:
            words.append(parses[0].normal_form)
    return " ".join(words) or " ".join(t.text.lower() for t in tokens)


def person_tokens(span: Span, vocab: MorphVocab) -> list:
    """Removes verbs and particles at the edges of a name span: «Подскажите» is not a name."""
    flags = [is_name_token(t.text, vocab) for t in span.tokens]
    if True not in flags:
        return []
    return span.tokens[flags.index(True) : len(flags) - flags[::-1].index(True)]


def unglue(text: str) -> tuple[str, list[int]]:
    """The text with a space in glued words and the source position of each of its characters."""
    origin: list[int] = []
    position = 0
    for match in GLUED.finditer(text):
        origin += [*range(position, match.start()), match.start()]
        position = match.start()
    origin += range(position, len(text) + 1)
    return GLUED.sub(" ", text), origin


def find_names(text: str) -> list[Finding]:
    spaced, origin = unglue(text)
    try:
        segmenter, tagger, vocab = load_natasha()
        doc = Doc(spaced)
        doc.segment(segmenter)
        doc.tag_ner(tagger)
    except (OSError, ValueError, RuntimeError, IndexError, KeyError):
        raise ScrubRefused("NER failure") from None
    found = []
    for span in doc.spans:
        tokens = person_tokens(span, vocab) if span.type == "PER" else span.tokens
        if not tokens or is_stop(" ".join(t.text for t in tokens)):
            continue
        # «Пойдёт» is never a place: one NER word whose parses are all verbs or particles.
        if len(tokens) == 1 and not is_name_token(tokens[0].text, vocab):
            continue
        found.append(
            Finding(
                origin[tokens[0].start],
                origin[tokens[-1].stop - 1] + 1,
                NER_KINDS[span.type],
                span_key(tokens, span.type, vocab),
            )
        )
    found += [Finding(*span) for span in find_context(text, vocab)]
    return found


def find_houses(text: str, findings: list[Finding]) -> list[Finding]:
    """A house number right after a place or an address joins the same label."""
    found = []
    for f in findings:
        if f.kind in HOUSE_KINDS and (stop := house_after(text, f.stop)) > f.stop:
            found.append(Finding(f.start, stop, f.kind, f.key))
        if f.kind in POSTCODE_KINDS:
            found += [Finding(*span) for span in postcodes_near(text, f.start, f.stop)]
    return found


def repeat_words(
    text: str, finding: Finding, vocab: MorphVocab, keep: tuple[str, ...]
) -> list[str]:
    """Normal forms of the finding words that recognize a repeat. An address also gives a plain
    adjective: «Сиреневая»."""
    words = []
    for token in TOKEN.finditer(text, finding.start, finding.stop):
        word = token.group()
        if len(word) < 3 or not CAPITAL.fullmatch(word) or is_stop(word) or is_term(word, keep):
            continue
        parses = vocab.parse(word)
        street = finding.kind == "ADDRESS" and any("ADJF" in p.tag for p in parses)
        if (
            not street
            and vocab.word_is_known(word.lower())
            and not any(REPEAT_GRAMMEMES & set(p.tag.grammemes) for p in parses)
        ):
            continue
        words.append(parses[0].normal_form)
    return words


def merge(findings: list[Finding]) -> list[Finding]:
    """Overlapping spans become one; kind and key come from the finding that was found earlier."""
    ranked = sorted(enumerate(findings), key=lambda pair: (pair[1].start, pair[0]))
    groups: list[list[tuple[int, Finding]]] = []
    stop = -1  # end of the current group: the merge stays linear without rescanning the group
    for pair in ranked:
        if groups and pair[1].start < stop:
            groups[-1].append(pair)
        else:
            groups.append([pair])
        stop = max(stop, pair[1].stop)
    merged = []
    for group in groups:
        _, first = min(group, key=lambda pair: pair[0])
        merged.append(
            Finding(group[0][1].start, max(f.stop for _, f in group), first.kind, first.key)
        )
    return merged


def blank_service(text: str) -> str:
    """Dates with hyphens and UUIDs stay out of the projection: «2025-12-14 10:30» is not ten
    digits."""
    for pattern in (DATE_ANY, UUID):
        text = pattern.sub(lambda m: BLANK * len(m.group()), text)
    return text


def exempt(blanked: str, stop: int) -> bool:
    """An amount with a currency is not a residue."""
    return bool(CURRENCY.match(blanked[stop : stop + 30]))


def find_residue(blanked: str) -> list[Finding]:
    """Residue after masking: digits written as digits or words, and an e-mail in words. Works on
    the text with the findings painted over, so the coordinates match the source text."""
    found = []
    dateless = blank_service(blanked)
    for match in LONG_NUMBER.finditer(dateless):
        digits = re.sub(r"\D", "", match.group())
        if len(digits) >= LONG_NUMBER_DIGITS and not exempt(blanked, match.end()):
            found.append(Finding(match.start(), match.end(), "NUMBER", digits))
    view = build_views(dateless)[VIEW_RELAY]
    for chain in view.chains:
        # Numbers with «тысяча» and «миллион» are amounts and quantities, not a residue.
        digits = "".join(view.text[s.proj_start : s.proj_stop] for s in chain if not s.scaled)
        amount = all(s.plain for s in chain) and exempt(blanked, chain[-1].stop)
        if len(digits) >= LONG_NUMBER_DIGITS and not amount:
            found.append(Finding(chain[0].start, chain[-1].stop, "NUMBER", digits))
    found += [
        Finding(m.start(), m.end(), "EMAIL", re.sub(r"\s+", " ", m.group().lower()))
        for m in MAIL_WORDS.finditer(blanked)
    ]
    return found


def find_tails(blanked: str, anchors: list[tuple[int, int]]) -> list[Finding]:
    """Pieces of a dictation: «...и последние сорок два» a few replies before or after a masked
    number, e-mail or the word «ИНН». Amounts, quantities with a unit and numbers with «тысяча»
    stay."""
    view = build_views(blank_service(blanked))[VIEW_RELAY]
    pieces = []
    for chain in view.chains:
        stop = chain[-1].stop
        if exempt(blanked, stop) or UNIT.match(blanked, stop):
            continue
        # «семь тысяч семьсот семь ноль восемь...»: the part with «тысяча» is not counted but
        # masked.
        digits = "".join(view.text[s.proj_start : s.proj_stop] for s in chain if not s.scaled)
        if digits:
            pieces.append(Finding(chain[0].start, stop, "NUMBER", digits))
    wide = bounds([*anchors, *(m.span() for m in TAIL_TRIGGER.finditer(blanked))])
    found = [
        p
        for p in pieces
        if len(p.key) >= TAIL_DIGITS
        and near(blanked, p.start, p.stop, wide, (TAIL_WINDOW, TAIL_LINES))
    ]
    # A single digit right next to a number, including a piece found above.
    singles = [*anchors, *((f.start, f.stop) for f in found)]
    singles += [m.span() for m in SINGLE_TRIGGER.finditer(blanked)]
    close = bounds(singles)
    return found + [
        p
        for p in pieces
        if len(p.key) < TAIL_DIGITS and near(blanked, p.start, p.stop, close, (SINGLE_WINDOW, 0))
    ]


def bounds(spans: list[tuple[int, int]]) -> tuple[list[int], list[int]]:
    return sorted(s[0] for s in spans), sorted(s[1] for s in spans)


def near(
    text: str, start: int, stop: int, edges: tuple[list[int], list[int]], limit: tuple[int, int]
) -> bool:
    """The nearest anchor on the left and on the right: farther ones have no smaller window or line
    count.
    limit is (window in characters, lines between)."""
    starts, stops = edges
    window, lines = limit
    gaps = []
    if (i := bisect_right(stops, stop)) > 0:
        gaps.append((min(stops[i - 1], start), start))
    if (j := bisect_left(starts, start)) < len(starts):
        gaps.append((stop, max(starts[j], stop)))
    return any(
        right - left <= window and text.count("\n", left, right) <= lines for left, right in gaps
    )


def blank(text: str, findings: list[Finding]) -> str:
    chars = list(text)
    for finding in findings:
        chars[finding.start : finding.stop] = BLANK * (finding.stop - finding.start)
    return "".join(chars)


@dataclass
class Scrubber:
    """One per file: the label table is shared by all lines of the file."""

    # Words outside data/terms.txt that are never masked: the own company name (terms format)
    keep: tuple[str, ...] = ()
    # (label, original); outside repr: tracebacks and logs must not carry originals
    replacements: list[tuple[str, str]] = field(default_factory=list, repr=False)
    labels: dict[tuple[str, str], str] = field(default_factory=dict, repr=False)
    counts: dict[str, int] = field(default_factory=dict, repr=False)
    # normal form of a person, place or organization word - (kind, key) of the first finding
    known: dict[str, tuple[str, str]] = field(default_factory=dict, repr=False)

    def scrub(self, text: str) -> str:
        if not text:
            return text
        if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
            raise ScrubRefused("input too large")
        if BLANK in text:
            raise ScrubRefused("invalid character")
        findings = [
            *find_numbers(text),
            *find_phones(text),
            *find_patterns(text),
            *find_address_and_birth(text),
            *find_spoken_numbers(text),
            *find_spoken_emails(text),
            *find_names(text),
        ]
        existing = [match.span() for match in LABEL.finditer(text)]
        findings = [
            f
            for f in findings
            if not any(f.start < stop and start < f.stop for start, stop in existing)
        ]
        findings = [
            f
            for f in findings
            if f.kind not in TERM_KINDS or not is_term(text[f.start : f.stop], self.keep)
        ]
        findings += find_houses(text, findings)
        findings = self.link(text, findings)
        merged = merge(findings)
        # A residue is not a refusal: it gets a label, and the dictation pieces follow it.
        residue = find_residue(blank(text, merged))
        merged = merge([*merged, *residue])
        anchors = [(f.start, f.stop) for f in [*findings, *residue] if f.kind in TAIL_KINDS]
        merged = merge([*merged, *find_tails(blank(text, merged), anchors)])
        parts = []
        position = 0
        for finding in merged:
            label = self.label(finding)
            self.replacements.append((label, text[finding.start : finding.stop]))
            parts += [text[position : finding.start], label]
            position = finding.stop
        parts.append(text[position:])
        return "".join(parts)

    def link(self, text: str, findings: list[Finding]) -> list[Finding]:
        """One label per person and name across the file: «Пётр, здравствуйте!» and later
        «Пётр Иванов» get one label; a repeat without context («Пётр, а ...») gets the same."""
        _, _, vocab = load_natasha()
        linked = []
        for f in findings:
            words = repeat_words(text, f, vocab, self.keep) if f.kind in REPEAT_KINDS else []
            # A one-word key (a first name without a surname) moves to the full finding with it.
            early = next(
                (
                    self.known[w]
                    for w in words
                    if w in self.known
                    and self.known[w][0] == f.kind
                    and " " not in self.known[w][1]
                ),
                None,
            )
            f = Finding(f.start, f.stop, *early) if early else f
            for word in words:
                self.known.setdefault(word, (f.kind, f.key))
            linked.append(f)
        if not self.known:
            return linked
        for token in TOKEN.finditer(text):
            word = token.group()
            if CAPITAL.fullmatch(word) and len(word) >= 3:
                normal = vocab.parse(word)[0].normal_form
                if normal in self.known:
                    linked.append(Finding(token.start(), token.end(), *self.known[normal]))
        return linked

    def label(self, finding: Finding) -> str:
        known = (finding.kind, finding.key)
        if known not in self.labels:
            prefix = PREFIXES[finding.kind]
            self.counts[prefix] = self.counts.get(prefix, 0) + 1
            self.labels[known] = f"[{prefix}_{self.counts[prefix]}]"
        return self.labels[known]
