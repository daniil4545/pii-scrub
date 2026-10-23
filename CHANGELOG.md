# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-10-23

### Changed

- Residue is masked, not refused: 7 or more digits left after masks become `[ЧИСЛА_N]`, spoken e-mail residue becomes `[EMAIL_N]`. Exit code 3 and `ScrubRefused.places` are removed.
- `Scrubber.scrub` takes only the text.

### Added

- `Scrubber(keep=...)` and `scrub --keep FILE`: caller words that are never masked, such as the own company name, in the format of `data/terms.txt`.
- Call-context rules for misses of the NER on reference calls (`context.py`): a name addressed at the start of a phrase ("Пётр, здравствуйте!"), a street without "улица" before a house number, a surname or place after "на", "по", a house number after a place, a client name after "называется", "комбинат" and similar words, a hyphenated capitalized name, a one-word reply unknown to the dictionary.
- A name, place or organization found once is masked with the same label wherever it repeats in the file.
- Words glued by ASR ("НаКамчатку") are split for the NER.
- Pieces of a dictated number: a number of 2 or more digits within 3 lines of a masked number or e-mail, or of the words "ИНН", "телефон", "почта", becomes `[ЧИСЛА_N]`. Amounts with a currency and quantities with a unit stay.

### Fixed

- Equipment brands, the industry registry and radio terms from `data/terms.txt` are no longer masked as names, places or organizations. Entries match whole words; only entries marked with `*` are stems.
- A one-word NER place or organization that can only be a verb or particle ("Пойдёт") is not masked; punctuation is never a name.
- UUID service identifiers (`transcript_id`) are not residue.

## [0.1.1] - 2026-10-21

### Changed

- CLI messages, help texts and refusal reasons are in English; labels in the output stay Russian.
- README: demo recording, quick start with real output, install with `uv tool install`.

### Added

- `docs/demo.tape`: the VHS script that records `assets/demo.gif`.
- CI runs `make eval` and `uv build` besides the format, lint and test checks.

## [0.1.0] - 2026-10-19

### Added

- CLI `scrub` and library `Scrubber` that replace personal data in Russian text with stable labels.
- Detection of names, places, organizations (Natasha), phone numbers, INN, OGRN, bank cards, SNILS, accounts, passports, e-mails, nicknames, sites, addresses and birth dates.
- Phone numbers, INN, SNILS and e-mails spoken as words, in pieces of words or digits, or interrupted by replies; e-mails with a Latin zone ("собака gmail.com").
- Stop list of roles and a morphology filter against false names.
- Fail-closed residue check: `ScrubRefused` and exit code 3 when 7 or more digits are left outside an amount with a currency.
- Size limit of 1 MB per input; NUL in the input is rejected.
- Report file with the originals and the places of a refusal (mode 0600); the output, stderr and exceptions never contain them.
- `make eval`: evaluation on a frozen synthetic set from `tests/gen/generate.py`, with false-mask and refusal limits.
- MIT license and CI.

### Changed

- Digits left after masking are no longer only flagged: the tool refuses with exit code 3.
- JSON input keeps collecting the places of residue over the whole file before refusing.

[Unreleased]: https://github.com/daniil4545/pii-scrub/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/daniil4545/pii-scrub/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/daniil4545/pii-scrub/releases/tag/v0.1.0
