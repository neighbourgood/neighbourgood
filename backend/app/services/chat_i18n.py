"""Command words and reply texts for chat bots, in all 12 app languages.

Strings live in ``app/i18n/chat/<lang>.json``. Command words from every
language are accepted everywhere (a German speaker in a Dutch community can
still type ``suche``); replies use the member's language.
"""

import functools
import json
import unicodedata
from pathlib import Path

LOCALES = ("en", "ar", "de", "el", "es", "fa", "fr", "id", "nl", "su", "sw", "uk")
COMMAND_PREFIXES = ("!", "/")

_DIR = Path(__file__).resolve().parent.parent / "i18n" / "chat"

# Fallback language per country when nothing else is known about a sender
_COUNTRY_LOCALE = {
    "DE": "de", "AT": "de", "CH": "de", "LI": "de", "LU": "de",
    "FR": "fr", "BE": "fr", "MC": "fr", "SN": "fr", "CI": "fr", "CM": "fr",
    "ES": "es", "MX": "es", "AR": "es", "CO": "es", "CL": "es", "PE": "es",
    "NL": "nl", "SR": "nl",
    "GR": "el", "CY": "el",
    "UA": "uk",
    "ID": "id",
    "KE": "sw", "TZ": "sw", "UG": "sw",
    "IR": "fa", "AF": "fa",
    "EG": "ar", "SA": "ar", "AE": "ar", "JO": "ar", "LB": "ar", "MA": "ar",
    "DZ": "ar", "TN": "ar", "IQ": "ar", "SY": "ar", "PS": "ar", "QA": "ar",
}


def normalize(word: str) -> str:
    """Case- and accent-insensitive form of a command word (Arabic/Cyrillic untouched)."""
    decomposed = unicodedata.normalize("NFKD", word.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


@functools.lru_cache(maxsize=None)
def catalog(locale: str) -> dict:
    path = _DIR / f"{locale}.json"
    if locale not in LOCALES or not path.exists():
        path = _DIR / "en.json"
    return json.loads(path.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def alias_table() -> dict[str, tuple[str, str]]:
    """normalized alias → (command, locale). English is loaded first and wins ties."""
    table: dict[str, tuple[str, str]] = {}
    for locale in LOCALES:
        for command, aliases in catalog(locale)["commands"].items():
            for alias in aliases:
                table.setdefault(normalize(alias), (command, locale))
    return table


def t(locale: str, key: str, **values) -> str:
    """Reply text for ``key`` (section.key or a key in "text"), English fallback."""
    section, _, name = key.rpartition(".")
    section = section or "text"
    text = catalog(locale).get(section, {}).get(name)
    if text is None:
        text = catalog("en").get(section, {}).get(name, name)
    return text.format(**values) if values else text


def word(locale: str, command: str) -> str:
    """The main command word for ``command`` in ``locale``."""
    aliases = catalog(locale)["commands"].get(command) or catalog("en")["commands"][command]
    return aliases[0]


def parse(text: str, *, require_prefix: bool) -> tuple[str, list[str], str] | None:
    """Return (command, args, locale of the matched word), or None if ``text`` is no command."""
    stripped = text.strip()
    has_prefix = stripped[:1] in COMMAND_PREFIXES
    if require_prefix and not has_prefix:
        return None
    if has_prefix:
        stripped = stripped[1:]
    parts = stripped.split()
    if not parts:
        return None
    # "/help@NeighbourGoodBot" style mentions
    head = normalize(parts[0].split("@")[0])
    match = alias_table().get(head)
    if not match:
        return None
    command, locale = match
    return command, parts[1:], locale


def locale_for_country(country_code: str | None) -> str | None:
    return _COUNTRY_LOCALE.get((country_code or "").upper())
