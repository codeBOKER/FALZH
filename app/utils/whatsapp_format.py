import re

_BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_HEADING_PATTERN = re.compile(r"(?m)^[ \t]*#{1,6}[ \t]+")


def to_whatsapp_markup(text: str) -> str:
    if not text:
        return text
    text = _HEADING_PATTERN.sub("", text)
    text = _BOLD_PATTERN.sub(r"*\1*", text)
    return text
