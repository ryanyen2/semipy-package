"""
Simplified semiformal inference: parse → generate → validate → return.

No version control, no complex resolution, no UI overhead.
Perfect for testing code generation in isolated environments (docker, K8s, etc).
"""
from semipy_testbed.config import SemiConfig, configure, get_config
from semipy_testbed.inference import infer_semiformal
from semipy_testbed.types import SimpleInferenceResult

__all__ = [
    "SemiConfig",
    "configure",
    "get_config",
    "infer_semiformal",
    "SimpleInferenceResult",
]
