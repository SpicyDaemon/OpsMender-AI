"""Telegram rendering boundary (M1-121).

OpsMender formats chat text in one small dialect for every platform: a
phrase in *asterisks* is bold, a phrase in `backticks` is code, three
backticks fence a block, and everything else, incident titles, summaries,
model replies and command arguments included, is plain text. Telegram used
to receive that text as legacy Markdown, which refuses the whole message
when a dynamic value holds an unmatched ``*``, ``_``, backtick or ``[``: an
incident page, a completion notice or a co-pilot reply was then lost.

Every Telegram send goes through :func:`render`, which turns the dialect
into Telegram HTML. A pair of delimiters on one line becomes a tag, any
other delimiter stays literal text, a bare URL becomes a link and ``<``,
``>`` and ``&`` are escaped. Nothing the renderer produces can be refused
for its formatting, whatever the dynamic text holds. Slack and the other
platforms keep the dialect as it is.
"""

from __future__ import annotations

import html
import re

PARSE_MODE = "HTML"

_URL = re.compile(r"https?://[^\s<>\"']+")
# Punctuation that ends a sentence around a URL is not part of it.
_URL_TRAIL = ".,;:!?)]}'\""


def escape(text: str) -> str:
    """Literal text for a Telegram HTML message."""
    return html.escape(text, quote=False)


def render(text: str) -> str:
    """The dialect rendered as Telegram HTML; never refused for its content."""
    out: list[str] = []
    fence: list[str] | None = None
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            if fence is None:
                fence = []
            else:
                out.append(f"<pre>{escape(chr(10).join(fence))}</pre>")
                fence = None
            continue
        if fence is not None:
            fence.append(line)
            continue
        out.append(_render_line(line))
    if fence is not None:
        # An unclosed fence is literal text, opening line included.
        out.append(escape("```"))
        out.extend(escape(line) for line in fence)
    return "\n".join(out)


def _render_line(line: str) -> str:
    # A headline is the whole line in asterisks. A title inside may hold an
    # asterisk of its own (an odd count), and then the outer pair wins; an
    # even count inside is several bold phrases on one line.
    if len(line) > 2 and line.startswith("*") and line.endswith("*") and line[1] != "*":
        inner = line[1:-1]
        if (
            inner.strip()
            and inner.count("*") % 2 == 1
            or "*" not in inner
            and inner.strip()
        ):
            return f"<b>{_render_spans(inner, bold=False)}</b>"
    return _render_spans(line, bold=True)


def _render_spans(line: str, *, bold: bool) -> str:
    out: list[str] = []
    literal: list[str] = []
    i = 0
    n = len(line)

    def flush() -> None:
        if literal:
            out.append(_render_text("".join(literal)))
            literal.clear()

    while i < n:
        ch = line[i]
        if ch == "`":
            end = line.find("`", i + 1)
            if end > i + 1:
                flush()
                out.append(f"<code>{escape(line[i + 1 : end])}</code>")
                i = end + 1
                continue
        elif ch == "*" and bold:
            marker = "**" if line.startswith("**", i) else "*"
            start = i + len(marker)
            end = line.find(marker, start)
            if end > start:
                flush()
                out.append(f"<b>{_render_spans(line[start:end], bold=False)}</b>")
                i = end + len(marker)
                continue
        literal.append(ch)
        i += 1
    flush()
    return "".join(out)


def _render_text(text: str) -> str:
    """Escaped literal text with bare URLs as links."""
    out: list[str] = []
    last = 0
    for match in _URL.finditer(text):
        url = match.group(0).rstrip(_URL_TRAIL)
        out.append(escape(text[last : match.start()]))
        out.append(f'<a href="{html.escape(url, quote=True)}">{escape(url)}</a>')
        last = match.start() + len(url)
    out.append(escape(text[last:]))
    return "".join(out)
