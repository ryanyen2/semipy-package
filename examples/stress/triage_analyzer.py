"""Analyzer classes with @semiformal methods (OOP + inheritance + cross-module types)."""
from __future__ import annotations

from semipy import semiformal
from triage_domain import Priority, Ticket, Triage


class TicketAnalyzer:
    """Base analyzer. SLA and escalation policy come from instance state (`self`)."""

    def __init__(self, sla_hours: int = 24, auto_close_categories: tuple[str, ...] = ("how-to",)):
        self.sla_hours = sla_hours
        self.auto_close_categories = auto_close_categories

    @semiformal
    def triage(self, ticket: Ticket) -> Triage:
        result = None
        #< intent: Produce validated support ticket triage
        #< by: keyword scoring with tier escalation; because ticket text exposes triage signals
        #< unless: missing fields treated as empty text
        #> analyze the support {ticket} and produce a Triage: priority (one of
        #> low/medium/high/urgent), a short lowercase category slug, the suggested_team,
        #> whether it needs_human review, a one-line summary, and a few topical tags
        #< yields: result containing priority, category, team, review flag, summary, and tags
        return result

    @semiformal
    def first_reply(self, ticket: Ticket, triage: Triage) -> str:
        reply = ""
        #< intent: compose an empathetic customer first response
        #< given: ticket and triage expose fields as dict keys or attributes
        #< by: selecting issue, priority, team, and human-review details from triage
        #< unless: returns {'reply': ...} despite string-return scaffold
        #> draft a concise, empathetic first-response message to the customer for
        #> {ticket} given its {triage}; acknowledge the issue and state next steps
        return reply

    def handle(self, ticket: Ticket) -> dict:
        # Plain method composing two @semiformal methods.
        t = self.triage(ticket)
        reply = self.first_reply(ticket, t)
        within_sla = not t.needs_human or self.sla_hours <= 8
        return {
            "ticket": ticket.id,
            "priority": t.priority.value if isinstance(t.priority, Priority) else str(t.priority),
            "category": t.category,
            "team": t.suggested_team,
            "auto_closeable": t.category in self.auto_close_categories,
            "within_sla": within_sla,
            "summary": t.summary,
            "reply": reply,
        }


class EnterpriseAnalyzer(TicketAnalyzer):
    """Subclass: tighter SLA. Reuses the inherited @semiformal methods unchanged."""

    def __init__(self) -> None:
        super().__init__(sla_hours=4, auto_close_categories=())
