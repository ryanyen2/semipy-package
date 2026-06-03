"""Demo: a slot whose ADAPT introduced an unintended regression.

Open this file in VS Code (with the Semipy extension) to feel the squiggly
warning on the `to_iso` spec line, and the matching entry in the Problems panel.
The regression here is the classic US-vs-EU date ambiguity: adapting to accept
`DD-MM-YYYY` reinterpreted the already-working `MM/DD/YYYY` inputs.
"""
import os

from semipy import configure, semiformal

# Absolute cache dir next to this file, so the portal lands where the VS Code
# extension looks (on the path above the source file) regardless of the cwd.
configure(
    verbose=True,
    cache_dir=os.environ.get("RD_CACHE")
    or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".semiformal"),
)


@semiformal
def to_iso(raw: str) -> str:
    result = ''
    #< by: normalizing text, then trying ISO parsing before known date patterns
    #< unless: empty, invalid, or unmatched input yields empty result
    #> infer the date format and return an ISO-8601 date string for {raw}


if __name__ == "__main__":
    print(to_iso("03/14/2025"))
