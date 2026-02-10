"""Merge-friendly session index: merge two indices by semicode_id (LWW, union usage_ids)."""
from __future__ import annotations

from semipy.types import SessionIndex, SemicodeEntry


def merge_session_indices(base: SessionIndex, incoming: SessionIndex) -> SessionIndex:
    """
    Merge two session indices into one. Same session_id/source_file required.
    For each semicode_id: keep one entry, merge usage_ids (union), LWW for
    implementation_id/function_name/generated_source (prefer entry with larger usage_count).
    """
    if base.session_id != incoming.session_id:
        return base
    by_id: dict[str, SemicodeEntry] = {}
    for e in base.semicodes:
        by_id[e.semicode_id] = SemicodeEntry(
            semicode_id=e.semicode_id,
            implementation_id=e.implementation_id,
            usage_ids=list(e.usage_ids),
            function_name=e.function_name,
            param_names=list(e.param_names),
            expected_type=e.expected_type,
            template_fingerprint=e.template_fingerprint,
            usage_count=e.usage_count,
            last_validated_at=e.last_validated_at,
            generated_source=e.generated_source,
        )
    for e in incoming.semicodes:
        if e.semicode_id not in by_id:
            by_id[e.semicode_id] = SemicodeEntry(
                semicode_id=e.semicode_id,
                implementation_id=e.implementation_id,
                usage_ids=list(e.usage_ids),
                function_name=e.function_name,
                param_names=list(e.param_names),
                expected_type=e.expected_type,
                template_fingerprint=e.template_fingerprint,
                usage_count=e.usage_count,
                last_validated_at=e.last_validated_at,
                generated_source=e.generated_source,
            )
        else:
            existing = by_id[e.semicode_id]
            merged_usage_ids = list(existing.usage_ids)
            for uid in e.usage_ids:
                if uid not in merged_usage_ids:
                    merged_usage_ids.append(uid)
            use_incoming = (e.usage_count or 0) >= (existing.usage_count or 0)
            by_id[e.semicode_id] = SemicodeEntry(
                semicode_id=e.semicode_id,
                implementation_id=e.implementation_id if use_incoming else existing.implementation_id,
                usage_ids=merged_usage_ids,
                function_name=e.function_name if use_incoming else existing.function_name,
                param_names=e.param_names if use_incoming else existing.param_names,
                expected_type=existing.expected_type,
                template_fingerprint=existing.template_fingerprint,
                usage_count=len(merged_usage_ids),
                last_validated_at=e.last_validated_at if use_incoming else existing.last_validated_at,
                generated_source=e.generated_source if use_incoming else existing.generated_source,
            )
    return SessionIndex(
        session_id=base.session_id,
        source_file=base.source_file,
        module_name=base.module_name,
        semicodes=list(by_id.values()),
        last_source_fingerprint=incoming.last_source_fingerprint or base.last_source_fingerprint,
    )
