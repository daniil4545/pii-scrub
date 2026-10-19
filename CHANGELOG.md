# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
- MIT license, CI, snapshot script for the public repository.

### Changed

- Digits left after masking are no longer only flagged: the tool refuses with exit code 3.
- JSON input keeps collecting the places of residue over the whole file before refusing.
