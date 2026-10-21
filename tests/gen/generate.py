"""Generator of call-style synthetic data: texts with personal data, a negative set, a long call.

Run: uv run python tests/gen/generate.py [--out tests/data/synthetic.jsonl]
Everything is invented: Faker ru_RU and templates. A set row: id, group, deferred, text, secrets.
Secret: kind, form, parts - the pieces that must not remain in the cleaned text.
"""

import argparse
import inspect
import json
import random
import re
import warnings
from pathlib import Path

import pymorphy2
from faker import Faker
from num2words import num2words

SEED = 20261019
PII_CASES = 240
NEGATIVE_CASES = 80
LONG_DIALOGS = 150
DEFERRED_EVERY = 5
ORDER_EVERY = 16
REPEAT_EVERY = 40

DIGIT = "ноль один два три четыре пять шесть семь восемь девять".split()
BRANDS = [
    "Ромашка",
    "Вектор",
    "Орион",
    "Мегаполис",
    "Спектр",
    "Рассвет",
    "Каскад",
    "Эверест",
    "Альтаир",
    "Меридиан",
    "Лидер",
    "Норд",
    "Прогресс",
    "Горизонт",
    "Сигма",
    "Титан",
]
MAIL_DOMAINS = [
    ("mail", "мэйл", "ru", "ру"),
    ("gmail", "джимейл", "com", "ком"),
    ("yandex", "яндекс", "ru", "ру"),
    ("bk", "бэка", "ru", "ру"),
]
GENERIC_LOCALS = [("info", "инфо"), ("sales", "сейлс"), ("zakaz", "заказ"), ("office", "офис")]
TRANSLIT = dict(
    zip(
        "абвгдезийклмнопрстуфхцы",
        "a b v g d e z i i k l m n o p r s t u f h c y".split(),
        strict=True,
    )
)
TRANSLIT.update(
    {
        "ж": "zh",
        "ч": "ch",
        "ш": "sh",
        "щ": "sch",
        "э": "e",
        "ю": "yu",
        "я": "ya",
        "ё": "e",
        "ь": "",
        "ъ": "",
    }
)
KIND_WEIGHTS = {
    "name": 25,
    "phone": 25,
    "inn": 10,
    "email": 15,
    "org": 10,
    "place": 8,
    "card": 3,
    "account": 2,
    "passport": 3,
    "snils": 3,
    "address": 6,
    "birthdate": 4,
}
FILLERS = [
    "Да, слушаю вас.",
    "Понял, сейчас проверю наличие.",
    "Хорошо, я запишу.",
    "Подскажите, пожалуйста, что именно вас интересует?",
    "Спасибо, всё ясно.",
    "Сейчас уточню у коллег и перезвоню.",
    "Отправлю вам предложение сегодня.",
    "Да, это возможно.",
    "Давайте так и сделаем.",
    "Одну минуту, посмотрю в системе.",
]
GREETINGS = [
    ("Менеджер", "Добрый день, компания на связи, чем могу помочь?"),
    ("Менеджер", "Здравствуйте, вы позвонили в отдел продаж."),
]
CLOSINGS = [("Менеджер", "Всего доброго, до свидания."), ("Клиент", "Спасибо, до связи.")]

# pymorphy2 calls inspect.getargspec, which Python 3.13 lacks; install a compatible replacement.
inspect.getargspec = lambda func: inspect.getfullargspec(func)[:4]  # type: ignore[attr-defined]
warnings.filterwarnings("ignore", message="pkg_resources is deprecated")
MORPH = pymorphy2.MorphAnalyzer()


def digits_words(digits: str) -> str:
    return " ".join(DIGIT[int(c)] for c in digits)


def pair_words(digits: str) -> str:
    """Pairs of digits in words: «05» is «ноль пять», «67» is «шестьдесят семь»."""
    out = []
    for i in range(0, len(digits), 2):
        pair = digits[i : i + 2]
        out.append(
            digits_words(pair)
            if pair[0] == "0" or len(pair) == 1
            else num2words(int(pair), lang="ru")
        )
    return " ".join(out)


def windows(words: list[str], size: int) -> list[str]:
    """Non-overlapping windows of size words; the tail is added as a window from the end."""
    out = [" ".join(words[i : i + size]) for i in range(0, len(words) - size + 1, size)]
    if len(words) % size:
        out.append(" ".join(words[-size:]))
    return out or [" ".join(words)]


def digit_groups(surface: str) -> list[str]:
    return [surface, *re.findall(r"\d{3,}", surface)]


def secret(kind: str, form: str, parts: list[str]) -> dict:
    return {"kind": kind, "form": form, "parts": list(dict.fromkeys(parts))}


def inflect(word: str, case: str) -> str:
    parses = MORPH.parse(word)
    named = [p for p in parses if {"Name", "Surn", "Patr"} & set(p.tag.grammemes)]
    form = (named or parses)[0].inflect({case})
    return form.word.capitalize() if form else word


def make_phone(rng: random.Random) -> tuple[str, dict]:
    code = rng.choice(["9" + str(rng.randint(10, 99)), "495", "499", "812"])
    rest = "".join(str(rng.randint(0, 9)) for _ in range(7))
    local = code + rest
    full = "8" + local
    form = rng.choice(["digits", "digit_words", "grouped_words", "relay", "mixed", "digits_relay"])
    groups = [local[:3], local[3:6], local[6:8], local[8:]]
    spoken = [num2words(int(g), lang="ru") if g[0] != "0" else digits_words(g) for g in groups]
    grouped = ["восемь", *spoken]
    if form == "digits":
        surface = rng.choice(
            [
                f"+7 ({local[:3]}) {local[3:6]}-{local[6:8]}-{local[8:]}",
                f"8 {local[:3]} {local[3:6]} {local[6:8]} {local[8:]}",
                full,
                f"+7{local}",
            ]
        )
        return surface, secret("phone", form, digit_groups(surface))
    if form == "digit_words":
        words = digits_words(full)
        return words, secret("phone", form, windows(words.split(), 3))
    if form == "grouped_words":
        return " ".join(grouped), secret("phone", form, [*spoken[:3], " ".join(spoken[2:])])
    if form == "relay":
        text = (
            f"восемь {spoken[0]} {spoken[1]}\nМенеджер: так\nКлиент: {spoken[2]}"
            f"\nМенеджер: да\nКлиент: {spoken[3]}"
        )
        return text, secret("phone", form, [f"{spoken[0]} {spoken[1]}", spoken[2], spoken[3]])
    if form == "digits_relay":
        text = (
            f"8 {local[:3]}\nМенеджер: так\nКлиент: {local[3:6]} {local[6:8]}"
            f"\nМенеджер: да\nКлиент: {local[8:]}"
        )
        return text, secret("phone", form, [f"8 {local[:3]}", f"{local[3:6]} {local[6:8]}"])
    text = f"восемь {spoken[0]} {local[3:6]} {local[6:8]} {local[8:]}"
    return text, secret("phone", form, [spoken[0], local[3:6]])


def make_inn(rng: random.Random, fake: Faker) -> tuple[str, dict]:
    number = fake.individuals_inn() if rng.random() < 0.7 else fake.businesses_inn()
    form = rng.choice(["digits", "digit_words", "pair_words", "digits_relay"])
    if form == "digits":
        return number, secret("inn", form, [number])
    if form == "digits_relay":
        chunks = [number[:4], number[4:8], number[8:]]
        text = f"{chunks[0]}\nМенеджер: так\nКлиент: {chunks[1]}\nМенеджер: да\nКлиент: {chunks[2]}"
        return text, secret("inn", form, [chunks[0], chunks[1]])
    words = digits_words(number) if form == "digit_words" else pair_words(number)
    return words, secret("inn", form, windows(words.split(), 3))


def translit(word: str) -> str:
    return "".join(TRANSLIT.get(c, c) for c in word.lower())


def make_email(rng: random.Random, fake: Faker) -> tuple[str, dict]:
    domain, domain_words, tld, tld_words = rng.choice(MAIL_DOMAINS)
    if rng.random() < 0.3:
        local, local_words = rng.choice(GENERIC_LOCALS)
        spoken_local, tokens = local_words, [local_words]
    else:
        first, last = fake.first_name_male().lower(), fake.last_name_male().lower()
        local = f"{translit(first)}.{translit(last)}"
        spoken_local, tokens = f"{first} точка {last}", [first, last]
    form = rng.choice(["ascii", "spoken", "spoken_latin", "latin_domain"])
    if form == "ascii":
        surface = f"{local}@{domain}.{tld}"
        return surface, secret("email", form, [surface, local])
    if form == "spoken_latin":
        return (
            f"{spoken_local} собака {domain} точка {tld}",
            secret("email", form, [*tokens, domain]),
        )
    if form == "latin_domain":
        return (
            f"{spoken_local} собака {domain}.{tld}",
            secret("email", form, [*tokens, f"{domain}.{tld}"]),
        )
    return (
        f"{spoken_local} собака {domain_words} точка {tld_words}",
        secret("email", form, [*tokens, domain_words]),
    )


def make_name(rng: random.Random, fake: Faker) -> tuple[dict, list[str]]:
    male = rng.random() < 0.5
    first = fake.first_name_male() if male else fake.first_name_female()
    last = fake.last_name_male() if male else fake.last_name_female()
    patr = fake.middle_name_male() if male else fake.middle_name_female()
    shape = rng.choice(["full", "full", "surname", "first", "first_patr"])
    cases = {"nom": "nomn", "gen": "gent", "dat": "datv", "ins": "ablt"}
    forms = {}
    for key, case in cases.items():
        if shape == "full":
            tokens = [inflect(first, case), inflect(last, case)]
        elif shape == "surname":
            tokens = [inflect(last, case)]
        elif shape == "first":
            tokens = [inflect(first, case)]
        else:
            tokens = [inflect(first, case), inflect(patr, case)]
        forms[key] = tokens
    return forms, [shape]


NAME_TEMPLATES = [
    ("nom", "Клиент", "Меня зовут {n}."),
    ("nom", "Клиент", "Это {n}, добрый день."),
    ("nom", "Менеджер", "Вас слушает {n}."),
    ("dat", "Клиент", "Передайте, пожалуйста, {n}, что я звонил."),
    ("ins", "Клиент", "Можно поговорить с {n}?"),
    ("gen", "Клиент", "Мне нужен контакт {n}."),
    ("dat", "Менеджер", "Я перезвоню {n} сегодня."),
]
OTHER_TEMPLATES = {
    "phone": [
        "Мой номер {x}.",
        "Запишите телефон {x}.",
        "Перезвоните мне на {x}.",
        "Запишите телефон {x}. Две тысячи рублей.",
    ],
    "inn": ["Наш ИНН {x}.", "Реквизиты такие, ИНН {x}.", "Запишите ИНН {x}."],
    "email": ["Почта у меня {x}.", "Отправьте на {x}, пожалуйста.", "Пишите на адрес {x}."],
    "card": ["Оплата с карты {x}."],
    "account": ["Расчётный счёт {x}."],
    "passport": ["Мои паспортные данные {x}."],
    "snils": ["СНИЛС {x}."],
}


def luhn_card(rng: random.Random) -> str:
    digits = [4, 2, 7, 6] + [rng.randint(0, 9) for _ in range(11)]
    total = sum(d * 2 - 9 if d * 2 > 9 else d * 2 for d in digits[::-2]) + sum(digits[-2::-2])
    return "".join(map(str, digits)) + str((10 - total % 10) % 10)


def snils_digits(rng: random.Random) -> str:
    body = [rng.randint(0, 9) for _ in range(9)]
    total = sum(d * w for d, w in zip(body, range(9, 0, -1), strict=True))
    check = total if total < 100 else (0 if total in (100, 101) else total % 101 % 100)
    return "".join(map(str, body)) + f"{check:02d}"


def make_secret(kind: str, rng: random.Random, fake: Faker) -> tuple[str, str, list[dict]]:
    """Returns the speaker, the line and the secrets."""
    if kind == "name":
        forms, _ = make_name(rng, fake)
        case, speaker, template = rng.choice(NAME_TEMPLATES)
        tokens = forms[case]
        return speaker, template.format(n=" ".join(tokens)), [secret("name", "text", tokens)]
    if kind in ("phone", "inn", "email"):
        maker = {
            "phone": lambda: make_phone(rng),
            "inn": lambda: make_inn(rng, fake),
            "email": lambda: make_email(rng, fake),
        }[kind]
        surface, sec = maker()
        return "Клиент", rng.choice(OTHER_TEMPLATES[kind]).format(x=surface), [sec]
    if kind == "org":
        brand = rng.choice(BRANDS)
        if rng.random() < 0.5:
            text = f"Я из компании ООО «{brand}»."
            return "Клиент", text, [secret("org", "quoted", [brand])]
        word = inflect(brand, "ablt")
        return "Клиент", f"Мы давно работаем с {word}.", [secret("org", "bare", [word])]
    if kind == "place":
        city = fake.city_name()
        form = inflect(city, "loct") if rng.random() < 0.5 else city
        template = "Звоню из города {x}." if form == city else "Мы находимся в {x}."
        if form != city:
            template = "Мы находимся в городе {x}."
        return "Клиент", template.format(x=form), [secret("place", "text", [form])]
    if kind == "card":
        number = luhn_card(rng)
        surface = " ".join(number[i : i + 4] for i in range(0, 16, 4))
        return (
            "Клиент",
            OTHER_TEMPLATES["card"][0].format(x=surface),
            [secret("card", "digits", digit_groups(surface))],
        )
    if kind == "account":
        number = "40702810" + "".join(str(rng.randint(0, 9)) for _ in range(12))
        return (
            "Клиент",
            OTHER_TEMPLATES["account"][0].format(x=number),
            [secret("account", "digits", [number])],
        )
    if kind == "passport":
        surface = f"{rng.randint(10, 99)}{rng.randint(10, 99)} {rng.randint(100000, 999999)}"
        return (
            "Клиент",
            OTHER_TEMPLATES["passport"][0].format(x=surface),
            [secret("passport", "digits", digit_groups(surface))],
        )
    if kind == "snils":
        n = snils_digits(rng)
        surface = f"{n[:3]}-{n[3:6]}-{n[6:9]} {n[9:]}"
        return (
            "Клиент",
            OTHER_TEMPLATES["snils"][0].format(x=surface),
            [secret("snils", "digits", digit_groups(surface))],
        )
    if kind == "address":
        street = fake.street_name()
        house, flat = rng.randint(1, 120), rng.randint(1, 200)
        if rng.random() < 0.3:
            text = f"Доставка на улицу {street}."
        else:
            text = f"Доставка на улицу {street}, дом {house}, квартира {flat}."
        return "Клиент", text, [secret("address", "text", [street])]
    day, year = rng.randint(1, 28), rng.randint(1960, 2000)
    months = ["января", "февраля", "марта", "апреля", "мая", "июня"]
    month = rng.choice(months)
    style = rng.random()
    if style < 0.35:
        surface = f"{day:02d}.{months.index(month) + 1:02d}.{year}"
    elif style < 0.7:
        surface = f"{day} {month} {year} года"
    else:
        surface = f"{num2words(day, lang='ru', to='ordinal')[:-2]}ого {month} {year} года"
    return "Клиент", f"Я родился {surface}.", [secret("birthdate", "text", [surface])]


def unique_parts(secrets: list[dict], text: str) -> list[dict]:
    """Keeps the pieces that occur in the text once: a common word does not prove a leak."""
    kept = []
    for item in secrets:
        parts = [p for p in item["parts"] if text.count(p) == 1]
        if parts:
            kept.append({**item, "parts": parts})
    return kept


def build_dialog(rng: random.Random, fake: Faker) -> tuple[str, list[dict]]:
    kinds = rng.choices(list(KIND_WEIGHTS), weights=KIND_WEIGHTS.values(), k=rng.randint(2, 4))
    speaker, greeting = rng.choice(GREETINGS)
    lines = [f"{speaker}: {greeting}"]
    secrets: list[dict] = []
    for kind in kinds:
        lines.append(f"{rng.choice(['Менеджер', 'Клиент'])}: {rng.choice(FILLERS)}")
        who, text, found = make_secret(kind, rng, fake)
        lines.append(f"{who}: {text}")
        secrets += found
    who, text = rng.choice(CLOSINGS)
    lines.append(f"{who}: {text}")
    return "\n".join(lines), secrets


def negative_text(rng: random.Random) -> str:
    amount = rng.choice([1200000, 345000, 89500, 15000, 2750000])
    qty = rng.choice([300, 500, 120, 45])
    day = rng.randint(1, 28)
    month = rng.choice(["марта", "апреля", "мая", "июня"])
    options = [
        f"Сумма заказа {amount:,} рублей.".replace(",", " "),
        f"Сумма получается {num2words(amount, lang='ru')} рублей.",
        f"Отгрузка {num2words(day, lang='ru', to='ordinal')[:-2]}ого {month} две тысячи двадцать пятого года.",
        f"Встреча {day:02d}.{rng.randint(1, 12):02d}.2025 в {rng.randint(9, 18)}:30.",
        f"Давайте созвонимся в десять сорок пять, а потом в {num2words(rng.randint(11, 18), lang='ru')} часов.",
        f"Нужно {qty} штук, артикул {rng.randint(1000, 9999)}.",
        f"Нужно {num2words(qty, lang='ru')} штук по {num2words(rng.randint(2, 9), lang='ru')} тысяч за штуку.",
        "Нашему МОПу нужно связаться с ЛПР, и РОП тоже в курсе.",
        "У клиента дома живёт собака, а точка продаж в центре.",
        "Скидка десять процентов, потом ещё пять, итого пятнадцать.",
        f"В системе стоит дата 2025-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d} 10:30:00.",
        f"Сумма {amount:,} тенге.".replace(",", " "),
        f"Договор до {rng.randint(1, 28):02d}-{rng.randint(1, 12):02d}-2026.",
    ]
    return rng.choice(options)


def build_negative(rng: random.Random, with_order_number: bool, with_repeat: bool) -> str:
    lines = [f"Менеджер: {rng.choice(FILLERS)}"]
    sentences = [negative_text(rng) for _ in range(rng.randint(2, 4))]
    if with_order_number:
        sentences.append(f"Заказ номер {rng.randint(1000000, 9999999)} уже в пути.")
    if with_repeat:
        # Acknowledged quantity: the pieces glue together like dictation and give a refusal.
        sentences.append("Сто?\nМенеджер: Да, сто пятьдесят.\nКлиент: Сто.")
    for sentence in sentences:
        lines.append(f"{rng.choice(['Менеджер', 'Клиент'])}: {sentence}")
        lines.append(f"{rng.choice(['Менеджер', 'Клиент'])}: {rng.choice(FILLERS)}")
    return "\n".join(lines)


def generate() -> list[dict]:
    Faker.seed(SEED)
    fake = Faker("ru_RU")
    rng = random.Random(SEED)
    rows = []
    for i in range(PII_CASES):
        text, secrets = build_dialog(rng, fake)
        rows.append(
            {
                "id": f"pii-{i:03d}",
                "group": "pii",
                "text": text,
                "secrets": unique_parts(secrets, text),
            }
        )
    for i in range(NEGATIVE_CASES):
        # A bare order number of 7 or more digits gives a refusal: every 16th text of the set has one.
        rows.append(
            {
                "id": f"neg-{i:03d}",
                "group": "negative",
                "text": build_negative(
                    rng, with_order_number=i % ORDER_EVERY == 0, with_repeat=i % REPEAT_EVERY == 5
                ),
                "secrets": [],
            }
        )
    texts, secrets = [], []
    for _ in range(LONG_DIALOGS):
        text, found = build_dialog(rng, fake)
        texts.append(text)
        secrets += found
    long_text = "\n".join(texts)
    rows.append(
        {
            "id": "long-000",
            "group": "long",
            "text": long_text,
            "secrets": unique_parts(secrets, long_text),
        }
    )
    for i, row in enumerate(rows):
        row["deferred"] = i % DEFERRED_EVERY == DEFERRED_EVERY - 1
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="tests/data/synthetic.jsonl")
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for row in generate():
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
