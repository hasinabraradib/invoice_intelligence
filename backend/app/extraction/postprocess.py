"""Pure functions that check and parse what the LLM quoted. No LLM, no I/O.

The LLM only copies text; every number, date and currency is interpreted here, by code
that is tested and never guesses (CLAUDE.md rules 1-3).
"""

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

# ---------------------------------------------------------------- text and grounding

_QUOTES_DASHES = str.maketrans(
    {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "}
)
_SPACES = re.compile(r"\s+")


def normalise_text(text: str) -> str:
    """Lowercase, unify quotes/dashes and collapse all whitespace (incl. line breaks)."""
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES_DASHES).casefold()
    return _SPACES.sub(" ", text).strip()


def is_grounded(evidence: str | None, source_text: str) -> bool:
    """True if the quoted evidence really appears in the source text."""
    if not evidence or not evidence.strip():
        return False
    return normalise_text(evidence) in normalise_text(source_text)


_INJECTION = re.compile(
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|rules)"
    r"|disregard (all |the )?(previous|prior|above) "
    r"|you are now |system prompt|new instructions|act as (an? )?(ai|assistant|model)"
    r"|(set|change|make) the (total|amount|value)s? to",
    re.IGNORECASE,
)


def suspicious_lines(text: str) -> list[str]:
    """Lines that look like instructions aimed at an AI (possible prompt injection)."""
    return [line.strip() for line in text.splitlines() if _INJECTION.search(line)]


# ---------------------------------------------------------------- amounts

_BENGALI_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_CURRENCY_TOKENS = re.compile(
    r"(?i)\b(?:usd|eur|gbp|inr|bdt|jpy|cny|aud|cad|chf|sgd|aed|sar|pkr|tk|taka|rs)\b\.?"
    r"|[$€£₹৳¥]|us\$"
)
_WESTERN = re.compile(r"^\d{1,3}(,\d{3})+$")  # 1,320 / 1,234,567
_INDIAN = re.compile(r"^\d{1,2}(,\d{2})*,\d{3}$")  # 1,32,000 (lakh grouping, BDT/INR)
_DOT_GROUPS = re.compile(r"^\d{1,3}(\.\d{3})+$")  # 1.234.567 (European thousands)


def parse_amount(text: str | None) -> Decimal | None:
    """Parse a printed amount into a Decimal, or None when the meaning is not certain.

    Handles currency symbols/codes, "1,320.00", "1.320,00", "1 320,00", lakh grouping
    "1,32,000.00", Bengali digits, and negatives as "-5", "5-" or "(5)".
    Returns None for unclear inputs like "1.320" (1.32 or 1320?) instead of guessing.
    """
    if not text:
        return None
    s = _CURRENCY_TOKENS.sub("", text.translate(_BENGALI_DIGITS)).strip()
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1].strip()
    if s.startswith("-") or s.endswith("-"):
        negative, s = True, s.strip("-").strip()
    s = re.sub(r"(?<=\d)[ \u202f\u00a0'](?=\d{3}\b)", "", s)  # "1 234,5" -> "1234,5"
    if not re.fullmatch(r"\d[\d.,]*", s or "x"):
        return None  # letters or other symbols left: not a plain amount

    if "," in s and "." in s:
        decimal_sep = "," if s.rfind(",") > s.rfind(".") else "."
        group_sep = "." if decimal_sep == "," else ","
        whole, _, fraction = s.rpartition(decimal_sep)
        if decimal_sep in whole or not _valid_groups(whole, group_sep):
            return None
        number = whole.replace(group_sep, "") + "." + fraction
    elif "," in s:
        if _WESTERN.match(s) or _INDIAN.match(s):
            number = s.replace(",", "")  # thousands groups
        elif re.fullmatch(r"\d+,\d{1,2}", s):
            number = s.replace(",", ".")  # European decimals: "4,50"
        else:
            return None
    elif "." in s:
        if re.fullmatch(r"\d+\.\d{1,2}", s) or re.fullmatch(r"\d+\.\d{4,}", s):
            number = s
        elif _DOT_GROUPS.match(s) and s.count(".") > 1:
            number = s.replace(".", "")  # 1.234.567
        else:
            return None  # "1.320": thousands or 1.32? Do not guess.
    else:
        number = s
    try:
        value = Decimal(number)
    except InvalidOperation:
        return None
    return -value if negative else value


def _valid_groups(whole: str, sep: str) -> bool:
    if sep not in whole:
        return whole.isdigit()
    plain = whole.replace(sep, ",")
    return bool(_WESTERN.match(plain) or _INDIAN.match(plain))


def amounts_in(text: str) -> list[Decimal]:
    """Every amount that can be parsed from a piece of text (e.g. an evidence quote)."""
    found = []
    pattern = r"\(\d[\d.,\s]*\d\)|-?\d[\d.,\s]*\d|\d"  # brackets only if both
    for token in re.findall(pattern, text.translate(_BENGALI_DIGITS)):
        value = parse_amount(token.strip())
        if value is not None:
            found.append(value)
    return found


# ---------------------------------------------------------------- dates

_MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for name in names
}
# An optional time is ignored: spreadsheet date cells read as "2026-10-03 00:00:00".
_ISO = re.compile(
    r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ t]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?$"
)
_NUMERIC = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})$")
_DAY_MONTH_YEAR = re.compile(r"^(\d{1,2})[\s\-/.]+([a-z]+)\.?[\s\-/.,]+(\d{4})$")
_MONTH_DAY_YEAR = re.compile(r"^([a-z]+)\.?[\s\-/.]+(\d{1,2}),?[\s\-/.,]+(\d{4})$")


def parse_date(text: str | None) -> tuple[str | None, bool]:
    """Return (ISO date "YYYY-MM-DD" or None, ambiguous).

    For numeric dates like 03/04/2026 both "3 April" and "March 4" are valid, so we return
    (None, True) rather than silently picking one. 13/04/2026 is unambiguous (no month 13).
    """
    if not text:
        return None, False
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text.strip().casefold().translate(_BENGALI_DIGITS))
    s = _SPACES.sub(" ", s)
    if m := _ISO.match(s):
        return _iso(int(m[1]), int(m[2]), int(m[3])), False
    if m := _NUMERIC.match(s):
        first, second, year = int(m[1]), int(m[2]), int(m[3])
        year += 2000 if year < 100 else 0
        if first > 12:
            return _iso(year, second, first), False  # day/month
        if second > 12:
            return _iso(year, first, second), False  # month/day
        if first == second:
            return _iso(year, first, second), False
        return None, True
    if m := _DAY_MONTH_YEAR.match(s):
        month = _MONTHS.get(m[2])
        return (_iso(int(m[3]), month, int(m[1])) if month else None), False
    if m := _MONTH_DAY_YEAR.match(s):
        month = _MONTHS.get(m[1])
        return (_iso(int(m[3]), month, int(m[2])) if month else None), False
    return None, False


def _iso(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:  # e.g. 31/02/2026
        return None


# ---------------------------------------------------------------- currency and names

_CURRENCY_ALIASES = {
    "BDT": ["bdt", "tk", "tk.", "taka", "৳"],
    "USD": ["usd", "us$", "$"],  # a bare "$" is treated as USD (most common use)
    "EUR": ["eur", "euro", "euros", "€"],
    "GBP": ["gbp", "£", "pound sterling"],
    "INR": ["inr", "₹", "rs", "rs.", "rupees"],
    "JPY": ["jpy"],
    "CNY": ["cny", "rmb"],
    "AUD": ["aud", "a$"],
    "CAD": ["cad", "c$"],
    "CHF": ["chf"],
    "SGD": ["sgd", "s$"],
    "AED": ["aed"],
    "SAR": ["sar"],
    "PKR": ["pkr"],
}
_ALIAS_TO_CODE = {alias: code for code, aliases in _CURRENCY_ALIASES.items() for alias in aliases}


def normalise_currency(text: str | None) -> str | None:
    """Map "৳", "Tk", "usd", "€"... to an ISO 4217 code. Also finds the currency inside a
    longer text like "Total: Tk 5,000". None if unknown or if two currencies appear."""
    if not text:
        return None
    s = normalise_text(text)
    if s in _ALIAS_TO_CODE:
        return _ALIAS_TO_CODE[s]
    found = set()
    for alias, code in sorted(_ALIAS_TO_CODE.items(), key=lambda kv: -len(kv[0])):
        if alias[0].isalpha():
            if re.search(rf"(?<![a-z]){re.escape(alias.rstrip('.'))}(?![a-z])", s):
                found.add(code)
        elif alias in s and not (alias == "$" and re.search(r"[a-z]\$", s)):
            found.add(code)  # skip "$" when it is part of "us$", "a$", "c$"...
    return found.pop() if len(found) == 1 else None


_COMPANY_SUFFIXES = {
    "ltd",
    "limited",
    "inc",
    "incorporated",
    "llc",
    "plc",
    "gmbh",
    "co",
    "corp",
    "corporation",
    "company",
    "pvt",
    "private",
    "pte",
    "sa",
    "ag",
    "bv",
    "llp",
}


def normalise_invoice_number(number: str | None) -> str | None:
    """Key for duplicate detection: "inv 001", "INV-001", "INV/001" -> "INV001"."""
    if not number:
        return None
    s = unicodedata.normalize("NFKC", number).translate(_QUOTES_DASHES).upper()
    return re.sub(r"[\s\-/.]", "", s) or None


def normalise_supplier_key(name: str | None) -> str | None:
    """Key for duplicate detection: "ACME Supplies (Pvt.) Ltd." -> "acme supplies"."""
    if not name:
        return None
    s = normalise_text(name).replace("&", " and ")
    words = re.sub(r"[^\w\s]", " ", s).split()
    while words and words[-1] in _COMPANY_SUFFIXES:
        words.pop()
    if words and words[0] == "the":
        words.pop(0)
    return " ".join(words) or None


# ---------------------------------------------------------------- confidence


def field_confidence(
    *, grounded: bool, status: str, parsed_ok: bool | None, ocr_confidence: float | None
) -> float:
    """A simple, explainable 0-1 score (not a probability):

    start at 0.95 for "found" (0.5 for "uncertain", 0 for "missing"), then multiply by
    0.5 if the quote was not found in the document, 0.5 if the value could not be parsed,
    and by 0.5-1.0 according to the OCR confidence of the page it came from.
    """
    if status == "missing":
        return 0.0
    score = 0.95 if status == "found" else 0.5
    if not grounded:
        score *= 0.5
    if parsed_ok is False:
        score *= 0.5
    if ocr_confidence is not None:
        score *= 0.5 + 0.5 * max(0.0, min(ocr_confidence, 100.0)) / 100
    return round(score, 2)
