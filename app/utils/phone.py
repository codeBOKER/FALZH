import re

_PHONE_RE = re.compile(
    r"(?:\+|00)\d[\d\s\-\.\(\)]{6,18}\d"
)

_LOCAL_PHONE_RE = re.compile(
    r"(?<!\d)"
    r"(?:\d[\d\s\-\.\(\)]{7,18}\d)"
    r"(?!\d)"
)


def count_digits(text: str) -> int:
    return sum(1 for c in text if c.isdigit())


def strip_phone_numbers(text: str, replacement: str = "[رقم الهاتف]") -> str:
    if not text:
        return text

    def _replace(match: re.Match) -> str:
        if count_digits(match.group()) >= 7:
            return replacement
        return match.group()

    result = _PHONE_RE.sub(_replace, text)
    result = _LOCAL_PHONE_RE.sub(_replace, result)
    return result
