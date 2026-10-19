"""Поиск ПДн в тексте и замена сквозными метками."""

import re
import warnings
from bisect import bisect_left
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files

import phonenumbers
from natasha import Doc, MorphVocab, NewsEmbedding, NewsNERTagger, Segmenter
from natasha.span import Span
from stdnum import luhn
from stdnum.ru import inn, ogrn

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
}
NER_KINDS = {"PER": "PERSON", "LOC": "PLACE", "ORG": "ORG"}
PERSON_GRAMMEMES = {"Name", "Surn", "Patr"}
# Части речи, которые вместе со стоп-листом отсекают «Подскажите» и «Да» из спана имени.
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

LABEL = re.compile(r"\[[А-ЯЁA-Z]+_\d+\]")
# Длинная цифровая цепочка: 10 цифр и больше. Вид по длине, контрольная сумма - у валидатора.
DIGIT_RUN = re.compile(r"(?<!\d)\d{10,20}(?!\d)")
RUN_KINDS = {10: "INN", 12: "INN", 13: "OGRN", 15: "OGRN", 20: "ACCOUNT"}
VALIDATORS = {"INN": inn.is_valid, "OGRN": ogrn.is_valid}
CARD = re.compile(r"(?<!\d)\d{4}(?:[ -]?\d{4}){3}(?!\d)")
SNILS = re.compile(r"(?<!\d)\d{3}[- ]?\d{3}[- ]?\d{3}[- ]?\d{2}(?!\d)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
PASSPORT = re.compile(r"(?<!\d)\d{4} \d{6}(?!\d)")
NICK = re.compile(r"(?<![\w@])@[A-Za-z][A-Za-z0-9_]{2,}")
# Домен без списка зон дал бы «call.txt»: берём частые зоны.
SITE = re.compile(
    r"(?<![\w.-])(?:https?://)?(?:[\w-]+\.)+(?:ru|рф|su|com|net|org|info|biz|io|me|by|kz|ua)"
    r"(?![\w-])",
    re.IGNORECASE,
)
REGEX_KINDS = [("EMAIL", EMAIL), ("PASSPORT", PASSPORT), ("NICK", NICK), ("SITE", SITE)]

# Адрес: маркер улицы, название с заглавной, затем дом, корпус, квартира.
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
# Дата рождения: дата в той же фразе после «родился», «рождения».
BIRTH_TRIGGER = re.compile(r"(?i:родил\w*|рождени\w+)")
MONTHS = "января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря"
BIRTH_DATE = re.compile(
    rf"\d{{1,2}}[./-]\d{{1,2}}[./-]\d{{2,4}}|(?:[А-Яа-яЁё]+|\d{{1,2}})\s+(?:{MONTHS})"
    r"(?:\s+\d{4}(?:\s+года?)?|(?:\s+[а-яё]+){0,5}?\s+года?)?"
)
BIRTH_WINDOW = 80

# Почта словами: «иван точка петров собака мэйл точка ру».
WORD = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")
MAIL_AT = "собака"
MAIL_DOT = "точка"
MAIL_ZONES = {"ру", "ком", "нет", "орг", "юа", "бай", "кз", "инфо", "рф", "су", "ай", "ио"}
# Зона, написанная латиницей: «собака gmail точка com», «собака gmail.com».
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
# «собака», до трёх слов, «точка» и зона либо домен с точкой: остаток почты, продиктованной словами.
_ZONES = "|".join(sorted(MAIL_ZONES | MAIL_LATIN_ZONES))
MAIL_WORDS = re.compile(
    rf"\b{MAIL_AT}\b(?:(?:\W+\w+){{0,{MAIL_RIGHT}}}?\W+{MAIL_DOT}\W+(?:{_ZONES})\b"
    rf"|{MAIL_DOMAIN.pattern})",
    re.IGNORECASE,
)

# Остаток после масок: цифры, записанные цифрами или словами, и почта словами.
LONG_NUMBER = re.compile(r"\d[\d ()-]*\d")
LONG_NUMBER_DIGITS = 7
CURRENCY = re.compile(
    r"^[\s,.]*(?:[А-Яа-яЁё]+\s+){0,2}"
    r"(?:руб|₽|\$|€|р\b|тыс|млн|миллион|долл|евро|копе|тенге|юан|гривн|сом\b|лир|фунт|иен)",
    re.IGNORECASE,
)
# Даты цифрами с дефисом и временем: остатком не бывают.
DATE_ANY = re.compile(
    r"\b\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\b"
    r"|\b(?:0?[1-9]|[12]\d|3[01])-(?:0?[1-9]|1[0-2])-(?:\d{4}|\d{2})\b"
)
MAX_PLACES = 50
MAX_WINDOW_RUN = 40
BLANK = "\x00"
WINDOW_LENGTHS = (10, 11, 12)
WINDOW_RUN = re.compile(rf"(?<!\d)\d{{11,{MAX_WINDOW_RUN}}}(?!\d)")


class ScrubRefused(Exception):
    """Очистка отказала: текст нельзя передавать дальше. Сообщение без текста входа."""

    def __init__(self, reason: str, places: list[str] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.places = places or []


@dataclass(frozen=True)
class Finding:
    start: int
    stop: int
    kind: str  # PERSON, PLACE, ORG, PHONE, EMAIL, CARD, INN, OGRN, ACCOUNT, PASSPORT, SNILS, ...
    key: str  # ключ сквозной метки


@cache
def load_natasha() -> tuple[Segmenter, NewsNERTagger, MorphVocab]:
    with warnings.catch_warnings():
        # pymorphy2 импортирует pkg_resources; setuptools<81 закреплён в зависимостях.
        warnings.filterwarnings("ignore", message="pkg_resources is deprecated")
        return Segmenter(), NewsNERTagger(NewsEmbedding()), MorphVocab()


@cache
def stoplist() -> tuple[str, ...]:
    text = files("pii_scrub").joinpath("data/stoplist.txt").read_text(encoding="utf-8")
    return tuple(line.strip() for line in text.splitlines() if line.strip() and line[0] != "#")


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
    """Номера с контрольной суммой (ИНН, ОГРН, карта, СНИЛС) и счёт из 20 цифр."""
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
    """Окна по цепочке цифр: лишняя цифра рядом с номером не прячет сам номер."""
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
    """Числа словами и кусками: поиск по проекции, спаны переносятся на целые исходные куски."""
    found = []
    # Даты с дефисами в проекцию не идут: «2025-12-14 10:30» - не десять цифр подряд.
    dateless = DATE_ANY.sub(lambda m: BLANK * len(m.group()), text)
    for view in build_views(dateless):
        if not view.segments:
            continue
        for finding in [*find_numbers(view.text), *find_phones(view.text), *find_windows(view)]:
            span = map_span(view, finding.start, finding.stop)
            if span:
                found.append(Finding(span[0], span[1], finding.kind, finding.key))
    return found


def find_spoken_emails(text: str) -> list[Finding]:
    """Пара «собака ... точка зона» или «собака домен.зона» и до восьми слов слева от неё."""
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
    """Не имя только слово, у которого нет разбора с граммемами имени и все разборы - глаголы,
    частицы, местоимения. Редкая фамилия без разбора остаётся именем: утечка хуже лишней маски."""
    if is_stop(word):
        return False
    parses = vocab.parse(word)
    if any(PERSON_GRAMMEMES & set(p.tag.grammemes) for p in parses):
        return True
    return not all(p.tag.POS in NOT_NAME_POS or "Apro" in p.tag.grammemes for p in parses)


def span_key(tokens: list, kind: str, vocab: MorphVocab) -> str:
    """Нормальные формы слов: «Ивану Сергеевичу» и «Иван Сергеевич» дают один ключ."""
    words = []
    for token in tokens:
        parses = vocab.parse(token.text)
        if kind == "PER":
            # Слова без граммем Name, Surn, Patr в ключ не входят.
            parses = [p for p in parses if PERSON_GRAMMEMES & set(p.tag.grammemes)]
        if parses:
            words.append(parses[0].normal_form)
    return " ".join(words) or " ".join(t.text.lower() for t in tokens)


def person_tokens(span: Span, vocab: MorphVocab) -> list:
    """Из спана имени убирает глаголы и частицы по краям: «Подскажите» именем не считается."""
    flags = [is_name_token(t.text, vocab) for t in span.tokens]
    if True not in flags:
        return []
    return span.tokens[flags.index(True) : len(flags) - flags[::-1].index(True)]


def find_names(text: str) -> list[Finding]:
    try:
        segmenter, tagger, vocab = load_natasha()
        doc = Doc(text)
        doc.segment(segmenter)
        doc.tag_ner(tagger)
    except (OSError, ValueError, RuntimeError, IndexError, KeyError):
        raise ScrubRefused("сбой NER") from None
    found = []
    for span in doc.spans:
        tokens = person_tokens(span, vocab) if span.type == "PER" else span.tokens
        if not tokens or is_stop(" ".join(t.text for t in tokens)):
            continue
        found.append(
            Finding(
                tokens[0].start,
                tokens[-1].stop,
                NER_KINDS[span.type],
                span_key(tokens, span.type, vocab),
            )
        )
    return found


def merge(findings: list[Finding]) -> list[Finding]:
    """Пересекающиеся спаны в один; вид и ключ берутся у находки, найденной раньше."""
    ranked = sorted(enumerate(findings), key=lambda pair: (pair[1].start, pair[0]))
    groups: list[list[tuple[int, Finding]]] = []
    for pair in ranked:
        if groups and pair[1].start < max(f.stop for _, f in groups[-1]):
            groups[-1].append(pair)
        else:
            groups.append([pair])
    merged = []
    for group in groups:
        _, first = min(group, key=lambda pair: pair[0])
        merged.append(
            Finding(group[0][1].start, max(f.stop for _, f in group), first.kind, first.key)
        )
    return merged


def exempt(blanked: str, stop: int) -> bool:
    """Сумма с валютой не остаток."""
    return bool(CURRENCY.match(blanked[stop : stop + 30]))


def find_residue(blanked: str) -> list[int]:
    """Начала мест, где после масок остались цифры или почта словами. Работает по тексту с
    закрашенными находками, поэтому координаты совпадают с исходным текстом."""
    spans: list[tuple[int, int]] = []
    dateless = DATE_ANY.sub(lambda m: BLANK * len(m.group()), blanked)
    for match in LONG_NUMBER.finditer(dateless):
        digits = sum(c.isdigit() for c in match.group())
        if digits >= LONG_NUMBER_DIGITS and not exempt(blanked, match.end()):
            spans.append((match.start(), match.end()))
    for chain in build_views(dateless)[VIEW_RELAY].chains:
        # Числа с «тысяча» и «миллион» - суммы и количества, в остаток не входят.
        digits = sum(s.proj_stop - s.proj_start for s in chain if not s.scaled)
        amount = all(s.plain for s in chain) and exempt(blanked, chain[-1].stop)
        if digits >= LONG_NUMBER_DIGITS and not amount:
            spans.append((chain[0].start, chain[-1].stop))
    spans += [(m.start(), m.end()) for m in MAIL_WORDS.finditer(blanked)]
    starts: list[int] = []
    stop = -1
    for start, end in sorted(spans):
        if start >= stop:
            starts.append(start)
        stop = max(stop, end)
    return starts


@dataclass
class Scrubber:
    """Одна на файл: таблица меток общая для всех строк файла."""

    # (метка, оригинал); вне repr: трассировка и логи не должны нести оригиналы
    replacements: list[tuple[str, str]] = field(default_factory=list, repr=False)
    places: list[str] = field(default_factory=list)  # "строка:столбец" или "путь:строка:столбец"
    labels: dict[tuple[str, str], str] = field(default_factory=dict, repr=False)
    counts: dict[str, int] = field(default_factory=dict, repr=False)

    def scrub(self, text: str, where: str) -> str:
        if not text:
            return text
        if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
            raise ScrubRefused("вход слишком велик")
        if BLANK in text:
            raise ScrubRefused("недопустимый символ")
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
        merged = merge(findings)
        parts = []
        position = 0
        for finding in merged:
            label = self.label(finding)
            self.replacements.append((label, text[finding.start : finding.stop]))
            parts += [text[position : finding.start], label]
            position = finding.stop
        parts.append(text[position:])
        self.check_residue(text, merged, where)
        return "".join(parts)

    def check_residue(self, text: str, merged: list[Finding], where: str) -> None:
        blanked = list(text)
        for finding in merged:
            blanked[finding.start : finding.stop] = BLANK * (finding.stop - finding.start)
        starts = find_residue("".join(blanked))
        if not starts:
            return
        newlines = [i for i, char in enumerate(text) if char == "\n"]
        for start in starts[: max(MAX_PLACES - len(self.places), 0)]:
            line = bisect_left(newlines, start)
            column = start - (newlines[line - 1] if line else -1)
            place = f"{line + 1}:{column}"
            self.places.append(f"{where}:{place}" if where else place)
        raise ScrubRefused("остаток", self.places)

    def label(self, finding: Finding) -> str:
        known = (finding.kind, finding.key)
        if known not in self.labels:
            prefix = PREFIXES[finding.kind]
            self.counts[prefix] = self.counts.get(prefix, 0) + 1
            self.labels[known] = f"[{prefix}_{self.counts[prefix]}]"
        return self.labels[known]
