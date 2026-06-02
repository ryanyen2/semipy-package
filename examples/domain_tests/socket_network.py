"""Domain: networked side effect (on-call alerting over a webhook).

An SRE wires incident alerts to an on-call webhook. The send is a real-world,
non-shadowable, irreversible effect: it must be planned, surfaced for approval,
performed at-least-once, and deduplicated by an idempotency key so a retry never
double-pages. The generated code emits the call through `fx`; it never opens a
socket itself.
"""
from __future__ import annotations

import json
import os
import tempfile

from semipy import (
    ExternalArtifactBackend,
    configure,
    register_artifact_backend,
    semiformal,
)
from semipy.effects import EffectResult

WIRE: list[dict] = []  # what actually went over the network


def sender(effect) -> None:
    # The real network egress for one planned effect.
    payload = effect.payload or {}
    WIRE.append({"target": effect.target, "payload": payload})
    print(f"    >>> SENT to {effect.target}: {json.dumps(payload, default=str)[:160]}")


def approve(script) -> bool:
    # Human-in-the-loop: here we auto-approve, but show what would be sent.
    print(f"    [approval] {len(script)} external effect(s) requested; approving")
    return True


@semiformal
def page_oncall(incident: dict) -> EffectResult:
    result = None
    #< intent: page the on-call rotation
    #< by: POSTing JSON alert with idempotency_key equal to incident id
    #< unless: missing incident id, returns the unchanged effect script
    #> page the on-call rotation about {incident} by POSTing a JSON alert to the webhook
    #> 'https://hooks.example.com/oncall' containing the incident id, severity, and summary;
    #> set an idempotency_key equal to the incident id so a retry never double-pages
    #< yields: <class 'semipy.effects.models.EffectResult'> via result
    return result


if __name__ == "__main__":
    register_artifact_backend("https", ExternalArtifactBackend(sender, scheme="https"))
    configure(
        effects_enabled=True, effect_staging=True, effect_gate=True,
        effect_auto_apply=True, effect_require_approval_external=True,
        effect_approval_callback=approve, verbose=True,
        cache_dir=os.environ.get("DT_CACHE_NET", tempfile.mkdtemp(prefix="net_cache_")),
    )

    incident = {"id": "INC-4471", "severity": "SEV1", "summary": "API p99 latency > 5s in us-east-1"}

    print("\nfirst page:")
    r1 = page_oncall(incident)
    print(f"  applied={r1.applied} effect={r1.effect_script.summary()}")

    print("\nretry same incident (must NOT double-send):")
    r2 = page_oncall(incident)
    print(f"  applied={r2.applied} effect={r2.effect_script.summary()}")

    print(f"\nWIRE (actual network egress): {len(WIRE)} send(s)")
    for w in WIRE:
        print("  ", w)
