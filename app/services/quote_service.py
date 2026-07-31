"""Builds quotes from match results and renders them for WhatsApp.

Two small classes with one job each (Single Responsibility):
- QuoteBuilder: MatchResult[] -> Quote (pure arithmetic, trivially testable)
- WhatsAppQuoteRenderer: Quote -> message text (implements QuoteRenderer)
"""
from __future__ import annotations

from app.domain.models import Inquiry, MatchResult, Quote, QuoteLine


def jmd(amount: float) -> str:
    return f"J${amount:,.2f}"


class QuoteBuilder:
    def build(self, matches: list[MatchResult]) -> Quote:
        lines = [
            QuoteLine(
                requested_title=m.requested.title,
                quantity=m.requested.quantity,
                matched=m.best.item if m.best else None,
                confidence=m.best.score if m.best else 0.0,
            )
            for m in matches
        ]
        return Quote(lines=lines)


class WhatsAppQuoteRenderer:
    def __init__(self, store_name: str = "our bookstore"):
        self._store_name = store_name

    def render(self, inquiry: Inquiry, quote: Quote) -> str:
        greeting = f"Hello {inquiry.sender_name}! " if inquiry.sender_name else "Hello! "
        out: list[str] = [
            greeting + f"Here is your quote from {self._store_name}:",
            "",
        ]
        n = 0
        for line in quote.lines:
            if line.matched is None:
                continue
            n += 1
            item = line.matched
            if item.in_stock:
                out.append(
                    f"{n}. {item.title} - {jmd(item.price)}"
                    + (f" x{line.quantity} = {jmd(line.line_total)}"
                       if line.quantity > 1 else "")
                )
            else:
                out.append(f"{n}. {item.title} - *out of stock*")

        out += ["", f"*Total: {jmd(quote.total)}*"]

        if quote.unmatched:
            out += [
                "",
                "We couldn't find these on our shelf list - "
                "we'll check and get back to you:",
            ]
            out += [f"• {l.requested_title}" for l in quote.unmatched]

        if quote.out_of_stock:
            out += [
                "",
                "Out-of-stock titles are not included in the total. "
                "Reply here if you'd like us to source them for you.",
            ]

        out += [
            "", 
            "Reply *YES* to confirm your order.",
            "*(Please finish confirming this list before sending a new one to avoid mix-ups!)*"
        ]
        return "\n".join(out)
