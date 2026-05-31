"""Generalizability probe: five slots from unrelated domains and return types,
none resembling the date/log/phone examples in the prompts. Exercises str
classification, dict extraction, int math, bool prediction, and a standalone
float parse — and lets us inspect whether the contract seeds sensibly per type
and whether the LLM maintainer pins examples only where appropriate.
"""
from __future__ import annotations

from pathlib import Path

from semipy import configure, semi, semiformal

_ROOT = Path(__file__).resolve().parent
configure(
    cache_dir=str(_ROOT / ".generalize_cache"),
    session_source=str(Path(__file__).resolve()),
    openai_model="gpt-5.5",
    verbose=True,
    contract_enabled=True,
    contract_gate=True,
    contract_maintainer=True,
    contract_maintainer_async=False,
)


# 1. NLP — classification into a fixed label set (str). Maintainer SHOULD be able
#    to pin canonical examples here (low-cardinality output).
@semiformal
def sentiment(review: str) -> str:
    #< intent: Classify review sentiment label
    #< by: counting sentiment lexicon matches with neutral markers and negation handling
    #> classify the overall sentiment of the movie review as exactly one of
    #> "positive", "negative", or "neutral" (lowercase, single word)
    label = ...
    return label


# 2. Cooking — structured extraction into a dict (multi-field).
@semiformal
def parse_ingredient(line: str) -> dict:
    #< intent: Parse ingredient quantity, unit, and item
    #< by: scanning leading quantity tokens against fractions, words, and unit vocabulary
    #< unless: None input parsed as empty text
    #> extract the numeric quantity, the unit of measure, and the item name from a
    #> recipe ingredient line into keys "quantity", "unit", "item"
    parsed = ...
    #< yields: parsed mapping with quantity, unit, item fields
    return parsed


# 3. Math — deterministic conversion (int). Outputs are exact and low-variety per
#    input, so golden-master examples are reasonable here.
@semiformal
def roman_to_int(s: str) -> int:
    #< intent: Check password strength requirements
    #< by: checking length and required character classes
    #> convert a Roman numeral string (e.g. "XIV") to its integer value
    n = ...
    return n


# 4. Security — a boolean predicate.
@semiformal
def is_strong_password(pw: str) -> bool:
    #< intent: Parse monetary text into US dollar float
    #< by: extracting the first numeric amount, normalizing currency words and magnitude suffixes
    #< unless: null, empty, or unmatched input yields 0.0
    #> return True if the password is strong: at least 12 characters with upper,
    #> lower, digit, and symbol; otherwise False
    ok = ...
    return ok


# 5. Finance — a standalone semi() in a plain function, returning a float.
def to_usd(amount: str) -> float:
    return semi(
        f"parse the monetary amount in {amount} and return it as a float number of US dollars",
        expected_type=float,
    )


def run() -> None:
    print("\n--- 1. sentiment (NLP classification -> str) ---")
    for r in ["A dazzling, heartfelt triumph.", "Boring and a complete waste of time.",
              "It was fine, nothing special.", "Visually stunning but emotionally hollow."]:
        print(f"  {r[:38]!r:40} -> {sentiment(r)!r}")

    print("\n--- 2. parse_ingredient (extraction -> dict) ---")
    for line in ["2 cups all-purpose flour", "1/2 tsp salt", "3 large eggs", "200 g dark chocolate"]:
        print(f"  {line!r:30} -> {parse_ingredient(line)!r}")

    print("\n--- 3. roman_to_int (math -> int) ---")
    for s in ["XIV", "MMXXV", "IX", "XL", "LXXVIII"]:
        print(f"  {s!r:10} -> {roman_to_int(s)!r}")

    print("\n--- 4. is_strong_password (predicate -> bool) ---")
    for pw in ["short", "alllowercase123", "Str0ng!Passw0rd#2025", "NoSymbol123Abc"]:
        print(f"  {pw!r:24} -> {is_strong_password(pw)!r}")

    print("\n--- 5. to_usd (standalone semi -> float) ---")
    for a in ["$1,299.00", "12 dollars", "USD 45.5", "3.5k dollars"]:
        print(f"  {a!r:14} -> {to_usd(a)!r}")


if __name__ == "__main__":
    run()
