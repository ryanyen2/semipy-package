from __future__ import annotations

import pytest

from semipy.agents.config import configure


@pytest.fixture
def tmp_cache(tmp_path):
    configure(cache_dir=str(tmp_path / ".semiformal"), verbose=False)
    yield tmp_path / ".semiformal"
    configure(cache_dir=str(tmp_path / ".semiformal"), verbose=False)
