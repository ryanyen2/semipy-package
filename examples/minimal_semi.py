"""Minimal test of semi() with DAG cache (no output_type)."""
from semipy import semiformal, semi


@semiformal
def check(value: str, condition: str) -> bool:
    return semi(f"does {repr(value)} satisfy '{condition}'?")


if __name__ == "__main__":
    print(check("hello", "is a greeting"))
    print(check("hello", "is a greeting"))  # REUSE
    print(check("error", "is an error"))     # ADVANCE or GENERATE
