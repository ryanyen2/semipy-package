"""
Apache log classification example: data-driven inference with file I/O.

This example shows how to:
1. Load raw data from a file
2. Use it for generation context
3. Test gist execution with data
4. Handle dataclass types

Run with: python examples/apache_log_simple.py
"""
import os
import re
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import List

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from semipy_testbed import infer_semiformal


@dataclass
class EventTemplate:
    """Regex template for an event family."""

    family: str
    pattern: str
    fields: dict


def load_logs(path: Path) -> list[str]:
    """Load log lines from file."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    return [ln for ln in text.splitlines() if ln.strip()]


def extract_bodies(lines: list[str]) -> list[str]:
    """Extract body part of Apache error logs."""
    bodies = []
    pattern = re.compile(r"^\[.*?\]\s+\[.*?\]\s+(.*?)$")
    for line in lines:
        match = pattern.match(line.strip())
        if match:
            bodies.append(match.group(1))
    return bodies


def classify_logs_example():
    """Classify Apache error logs into event families."""
    print("\n" + "=" * 70)
    print("EXAMPLE: Apache Log Classification with Data Files")
    print("=" * 70)

    # Load sample data
    data_dir = Path(__file__).parent / "data"
    log_file = data_dir / "sample_logs.txt"

    if not log_file.exists():
        print(f"Data file not found: {log_file}")
        return

    logs = load_logs(log_file)
    print(f"Loaded {len(logs)} log lines from {log_file.name}")

    # Extract bodies
    bodies = extract_bodies(logs)
    print(f"Extracted {len(bodies)} event bodies")
    print("\nSample bodies:")
    for body in bodies[:3]:
        print(f"  - {body[:60]}...")

    # Create sample input for inference
    sample_bodies = bodies[:5]  # First 5 for generation context
    grouped = {}
    for body in sample_bodies:
        # Simple grouping by first keyword
        key = body.split()[0] if body else "unknown"
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(body)

    print(f"\nGrouped into {len(grouped)} families")
    for family, items in grouped.items():
        print(f"  - {family}: {len(items)} items")

    # Generate classifier
    user_source = f"""
# Load and classify Apache log events
logs = {repr(sample_bodies[:3])}

def classify_body(body: str) -> str:
    # Classify into event family
    pass
"""

    user_spec = (
        f"For these Apache error log bodies: {sample_bodies[:2]}, "
        "create a function that classifies each into a short snake_case family name. "
        "Return just the family name (e.g., 'connection', 'permission', 'timeout'). "
        "If you can't determine, return 'unknown'."
    )

    result = infer_semiformal(
        user_spec=user_spec,
        free_variables={"body": sample_bodies[0]},
        sample_input={"args": [sample_bodies[0]], "kwargs": {}},
        expected_type=str,
        free_variable_names=["body"],
        user_source_code=user_source,
        verbose=True,
    )

    print("\n--- Result ---")
    print(f"Success: {result.success}")

    if result.success:
        print("\nClassifying all extracted bodies:")
        families = {}
        for body in bodies:
            try:
                family = result.compiled_function(body)
                if family not in families:
                    families[family] = []
                families[family].append(body)
            except Exception as e:
                print(f"  ERROR on '{body[:40]}...': {e}")

        print(f"\nClassification summary ({len(families)} families):")
        for family in sorted(families.keys()):
            print(f"  {family:20} : {len(families[family]):2} items")
            if families[family]:
                print(f"    Example: {families[family][0][:55]}...")

    else:
        print(f"Generation failed: {result.error}")
        print(f"\nGenerated code:\n{result.source_code}")


def generate_patterns_example():
    """Generate regex patterns for event families."""
    print("\n" + "=" * 70)
    print("EXAMPLE: Generate Regex Patterns")
    print("=" * 70)

    data_dir = Path(__file__).parent / "data"
    log_file = data_dir / "sample_logs.txt"

    if not log_file.exists():
        print(f"Data file not found: {log_file}")
        return

    logs = load_logs(log_file)
    bodies = extract_bodies(logs)

    # Group by simple family
    families = {}
    for body in bodies:
        key = body.split()[0] if body else "unknown"
        if key not in families:
            families[key] = []
        families[key].append(body)

    print(f"Generating patterns for {len(families)} families")

    # Create sample data
    sample_data = {family: items[:2] for family, items in families.items()}

    user_spec = (
        f"Given these event families and samples: {sample_data}, "
        "create Python regex patterns that match each family's events. "
        "Return a list of dicts with keys: family (str), pattern (str), fields (dict). "
        "Make patterns general enough to match variations."
    )

    result = infer_semiformal(
        user_spec=user_spec,
        free_variables={"families": sample_data},
        sample_input={"args": [list(sample_data.keys())], "kwargs": {}},
        expected_type=list,
        free_variable_names=["families"],
        verbose=True,
    )

    print("\n--- Result ---")
    print(f"Success: {result.success}")

    if result.success:
        print("\nGenerated function works!")
        print("Gist that would execute:")
        print(result.gist_source[:500] + "...")
    else:
        print(f"Generation failed: {result.error}")


if __name__ == "__main__":
    # Make sure API key is set
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("ERROR: OPENROUTER_API_KEY not set in environment")
        print("Set it with: export OPENROUTER_API_KEY='your-key-here'")
        sys.exit(1)

    print("=" * 70)
    print("SEMIPY TESTBED - DATA-DRIVEN EXAMPLES")
    print("=" * 70)

    classify_logs_example()
    # generate_patterns_example()

    print("\n" + "=" * 70)
    print("Examples complete!")
    print("=" * 70)
