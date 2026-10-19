# pii-scrub

Replace personal data in Russian text with stable labels, including phone numbers and e-mails spoken as words.

[![CI](https://github.com/daniil4545/pii-scrub/actions/workflows/ci.yml/badge.svg)](https://github.com/daniil4545/pii-scrub/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Built for call transcripts: a speech recognizer writes "восемь девятьсот двенадцать триста сорок пять" instead of digits, and most PII tools stop at digits.

```console
$ cat demo.txt
Менеджер: Назовите ваш телефон.
Клиент: Меня зовут Иван Петров, восемь девятьсот двенадцать триста сорок пять шестьдесят семь восемьдесят девять.
Клиент: Почта иван точка петров собака мэйл точка ру.
$ scrub demo.txt -o demo.clean.txt
scrub: замен 3 (ИМЯ 1, ТЕЛЕФОН 1, EMAIL 1)
$ cat demo.clean.txt
Менеджер: Назовите ваш телефон.
Клиент: Меня зовут [ИМЯ_1], [ТЕЛЕФОН_1].
Клиент: Почта [EMAIL_1].
```

When digits are left that no rule recognizes, the tool refuses instead of passing the text on:

```console
$ cat demo2.txt
Менеджер: Код 123 45 67 сохраните.
$ scrub demo2.txt -o demo2.clean.txt; echo "exit=$?"
scrub: отказ, остаток в строках 1
exit=3
```

Status: version 0.1, tested on generated text, not on real calls yet. See [Evaluation](#evaluation) and [Limitations](#limitations).

## Features

| What | How |
|---|---|
| Names, places, organizations | [Natasha](https://github.com/natasha/natasha) NER; a morphology filter and a stop list keep verbs and roles (МОП, ЛПР) unmasked |
| Phone numbers | digits, digit words, grouped words, pieces mixed with digits, dictation interrupted by replies ("так", "да") |
| INN, OGRN, bank cards, SNILS | checksum validators, digits or words |
| Accounts, passports, nicknames, sites | regular expressions |
| E-mail | `a@b.ru` and "иван точка петров собака мэйл точка ру" |
| Address, birth date | street with house and flat; a date after "родился" |
| Stable labels | one person gets one label in every case form: "Ивану Сергеевичу" and "Иван Сергеевич" are `[ИМЯ_1]` |
| Fail closed | 7 or more digits left after masking (outside an amount with a currency) means exit code 3 and no output |
| Text or JSON input | JSON keeps its structure, only string values are cleaned |
| Report | originals and the places of a refusal go to a separate file with mode 0600, never to the output or stderr |

## Install

Python 3.13 and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/daniil4545/pii-scrub
cd pii-scrub
uv sync
uv run scrub demo.txt -o demo.clean.txt --report demo.report
```

Exit codes: 0 done; 1 internal error or NER failure; 2 input or write error (not UTF-8, broken JSON, input over 1 MB); 3 refusal, digits left.

## Library

```python
from pii_scrub.scrub import Scrubber, ScrubRefused, load_natasha

load_natasha()  # once per process: loads the Natasha models
try:
    clean = Scrubber().scrub(text, "")  # a new Scrubber for every file
except ScrubRefused as refused:
    ...  # do not pass the text on; refused.reason and refused.places carry no input text
```

## How it works

```mermaid
flowchart LR
  text["Text or JSON"] --> norm["Number words to digits"]
  norm --> find["Find: checksum, regex, NER"]
  find --> stop["Stop list"]
  stop --> merge["Merge spans, stable labels"]
  merge --> check["Residue check"]
  check -->|"clean"| out["Clean text"]
  check -->|"digits left"| fail["Refusal, exit 3"]
  merge --> rep["Report, mode 0600"]
```

Number words become digits only for searching. A match is mapped back to whole original words, so the mask always covers what was said. Three projections cover the ambiguity of speech: "двести семьдесят девять" is 279 or 200 and 79.

## Evaluation

`make eval` runs a frozen synthetic set generated from a seed: 240 texts with personal data, 80 without, and one long call (57 thousand characters). Every fifth text is held back from debugging.

| Measure | Result | Limit |
|---|---|---|
| Missed phone, INN, e-mail (words, digits, pieces, across replies) | 0 | 0 |
| Texts without personal data that got a mask or a refusal | 7 of 80 | 10% |
| Refusals on texts with personal data | 0 of 241 | 5% |
| Missed names / places / organizations | 2 of 228 / 0 of 84 / 2 of 62 | reported |
| Missed addresses / birth dates | 2 of 77 / 0 of 37 | reported |

A long call of 98 KB is cleaned in 0.3 s on a laptop CPU.

The set is written by the author of the rules, so it proves consistency, not coverage of real speech. It was extended after an independent code review found forms the first set did not contain. Held-out discipline was not perfect: the long text and some diagnostics read the whole set. Order numbers (1 text in 16) and quantity confirmations (1 in 40) in the negative set are the author's guess of their frequency.

## Privacy

| Promise | Checked by |
|---|---|
| Originals appear only in the report file, which has mode 0600 | `test_report_holds_originals_with_0600_and_outputs_do_not` |
| Every detected kind is masked in the output and in stderr | `test_acceptance_every_kind_masked_in_output_and_stderr` |
| A refusal writes no output and no input text reaches stderr | `test_residue_refused_with_exit_3_line_and_no_output` |
| An input error or an unexpected failure writes no output | `test_input_errors_exit_2_and_write_nothing`, `test_unexpected_error_exits_1_with_kind_but_without_input_text` |
| The evaluator fails on a tool that masks nothing, everything, or refuses everything | `test_doing_nothing_fails_on_blocking_classes`, `test_masking_everything_fails_on_the_negative_set`, `test_refusing_everything_fails_on_both_limits` |
| The tests run with the network closed | `--block-network` in `make commit-check` |

## Limitations

- Not validated on real calls yet.
- A number dictated with anything but short confirmations ("так", "да", "угу", "значит") between the pieces is not joined; pieces under 7 digits pass.
- A bare number of 7 or more digits (an order number) causes a refusal; so can a quantity repeated across replies ("Сто?" "Да, сто пятьдесят." "Сто.").
- A capitalized common noun at the start of a line is sometimes taken for an organization by the NER model.
- A street in lower case is not found; JSON keys are not cleaned and appear in the refusal message.
- Names in lower case were not tested.
- Not detected: car plates, VIN, driver's licence, health insurance number, contract numbers, special categories (health, nationality, religion).
- Labels are numbered per file: the same person in two files gets different numbers.

## Development

```sh
make commit-check   # format, lint, fast tests
make eval           # synthetic evaluation
make ci-check       # plus pip-audit and gitleaks
```

## License

MIT, see [LICENSE](LICENSE).
