"""Runtime semiformal system: @semiformal and semi() for semantically underspecified logic."""
from semipy.decorator import semiformal
from semipy.semi_fn import semi
from semipy.config import SemiConfig, configure, get_config
from semipy.types import SemiGenerationError

__all__ = [
    "semiformal",
    "semi",
    "SemiConfig",
    "configure",
    "get_config",
    "SemiGenerationError",
]
