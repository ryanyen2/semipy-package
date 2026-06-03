"""Run the multi-file OOP triage app over a batch of realistic tickets."""
from __future__ import annotations

import os

from semipy import configure
from triage_analyzer import EnterpriseAnalyzer, TicketAnalyzer
from triage_domain import Ticket

configure(verbose=True, cache_dir=os.environ.get("STRESS_CACHE", "examples/stress/.cache"))


TICKETS = [
    Ticket(
        id="T-1001",
        customer_tier="enterprise",
        subject="Production API returning 500s since 14:00 UTC",
        body=(
            "Our checkout integration started failing ~30 min ago. Every POST to "
            "/v2/charge returns 500 with 'internal error'. This is affecting all "
            "live transactions. Trace id: 9f2c... Please escalate."
        ),
        attachments=1,
    ),
    Ticket(
        id="T-1002",
        customer_tier="free",
        subject="How do I export my data to CSV?",
        body="Hi, I can't find the export button. Is there a way to download everything as a spreadsheet?",
    ),
    Ticket(
        id="T-1003",
        customer_tier="pro",
        subject="Double charged this month",
        body="I was billed twice on the 3rd ($49 each). Please refund one and explain what happened.",
    ),
]


def main() -> None:
    base = TicketAnalyzer()
    ent = EnterpriseAnalyzer()

    print("=== base analyzer (sla=24h) ===")
    for tk in TICKETS:
        out = base.handle(tk)
        print(f"\n[{out['ticket']}] {out['priority'].upper()} / {out['category']} -> {out['team']}")
        print(f"  auto_closeable={out['auto_closeable']} within_sla={out['within_sla']}")
        print(f"  summary: {out['summary']}")
        print(f"  reply:   {out['reply'][:140]}")

    print("\n=== enterprise analyzer (sla=4h) reuses inherited methods ===")
    out = ent.handle(TICKETS[0])
    print(f"[{out['ticket']}] within_sla={out['within_sla']} team={out['team']}")


if __name__ == "__main__":
    main()
