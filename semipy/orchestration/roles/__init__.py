"""Orchestration roles: the named stages of the generation pipeline.

Each role is a focused callable that produces a typed artifact. Deterministic
roles (router projection, executor) are plain functions; LLM-backed roles (coder,
verifier alignment, surfacer) degrade to a deterministic default when no API key
is configured, so the pipeline -- and the unit suite -- run offline.
"""
from __future__ import annotations
