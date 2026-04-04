from semipy import semiformal, semi, configure
from pathlib import Path

CACHE_DIR = '/Users/r4yen/Desktop/Research/semi-formal/repo/semipy-package/.semiformal-game-usecase'
_SESSION_SOURCE = str((Path(CACHE_DIR).resolve().parent / "examples").resolve())

configure(
    cache_dir=CACHE_DIR,
    session_source=_SESSION_SOURCE,
    verbose=True,
)

@semiformal
def play_chess_game(game_state: dict) -> dict:
    ... #< play the chess game
    ... #< return the game state