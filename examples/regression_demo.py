"""Demo: a slot whose ADAPT introduced an unintended regression.

Open this file in VS Code (with the Semipy extension) to feel the squiggly
warning on the `to_iso` spec line, and the matching entry in the Problems panel.
The regression here is the classic US-vs-EU date ambiguity: adapting to accept
`DD-MM-YYYY` reinterpreted the already-working `MM/DD/YYYY` inputs.
"""
import os

from semipy import configure, semiformal

configure(verbose=True, cache_dir=os.environ.get("RD_CACHE", "examples/.semiformal"))


@semiformal
def to_iso(raw: str) -> str:
    result = ''
    #< given: raw may be None, string, or string-coercible
    #< by: normalizing whitespace, then matching supported date patterns
    #< unless: empty or invalid input stores empty result
    #> infer the date format and return an ISO-8601 date string for {raw}

    return result


if __name__ == "__main__":
    print(to_iso("03/14/2025"))
