"""Domain: document processing.

A back-office automation engineer extracts structured invoice data from the
noisy text an OCR step produced, then feeds it into an accounts-payable check.
Realistic: heterogeneous vendors, thousands-separators, currency symbols,
multi-line item blocks, and a downstream consumer that reads typed fields.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from semipy import configure, semiformal

configure(verbose=True, cache_dir=os.environ.get("DT_CACHE", "examples/.dt_cache"))


@dataclass
class Invoice:
    vendor: str
    invoice_number: str
    currency: str          # ISO 4217, e.g. "USD"
    total_cents: int       # grand total as integer cents
    line_items: list[str] = field(default_factory=list)


@semiformal
def parse_invoice(raw_text: str) -> Invoice:
    result = None
    #< intent: Extract invoice fields into surrounding result
    #< by: regex heuristics over stripped lines; because invoices vary without fixed structure
    #< unless: missing input yields blank fields
    #< unless: Invoice import unavailable raises ImportError before parsing
    #> extract the vendor, invoice number, ISO-4217 currency code, grand total in
    #> integer cents, and the list of line-item description strings from {raw_text}
    #< yields: result key containing parsed Invoice fields
    return result


def accounts_payable_gate(inv: Invoice) -> str:
    # Downstream consumer: reads typed fields, so generation must produce real ints.
    if inv.total_cents > 1_000_000:
        return f"ESCALATE {inv.vendor}: {inv.total_cents/100:.2f} {inv.currency}"
    return f"AUTO-APPROVE {inv.vendor}: {inv.total_cents/100:.2f} {inv.currency}"


SAMPLES = [
    """ACME  Industrial  Supply
Invoice  #:  INV-2025-0042
Bill currency: US Dollars
  - 12x  M8 hex bolts ................ $ 36.00
  - 4x   steel brackets ............... $ 128.50
  - shipping .......................... $ 15.00
TOTAL DUE:  $179.50
""",
    """GLOBEX GMBH
Rechnung Nr. 7781/DE
Betrag: EUR 2.450,00
Pos 1: Wartung Serverschrank  1.900,00
Pos 2: Anfahrt                  550,00
""",
]


if __name__ == "__main__":
    for raw in SAMPLES:
        inv = parse_invoice(raw)
        print("\nPARSED:", inv)
        print("GATE:  ", accounts_payable_gate(inv))
