#!/usr/bin/env python3
"""
Quick start script to test semipy_testbed.

Usage:
    export OPENAI_API_KEY='sk-...'
    python run_quick_test.py
"""
from semipy_testbed import infer_semiformal, configure
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
load_dotenv(override=False)


def main():
    """Run a quick test."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set")
        print("Set it with: export OPENAI_API_KEY='your-key'")
        return 1

    configure(verbose=True)

    print("=" * 60)
    print("QUICK TEST: Email Domain Extraction")
    print("=" * 60)

    result = infer_semiformal(
        user_spec="Extract domain from email (part after @). Return empty string if invalid.",
        free_variables={"email": "alice@company.co.uk"},
        sample_input={"args": ["alice@company.co.uk"], "kwargs": {}},
        expected_type=str,
        free_variable_names=["email"],
    )

    print("\n" + "=" * 60)
    print("RESULT")
    print("=" * 60)
    print(f"Success: {result.success}")

    if result.success:
        print(f"\nFunction: {result.compiled_function.__name__}")
        print(f"Module: {result.compiled_function.__module__}")

        tests = [
            "alice@example.com",
            "bob.smith@company.org",
            "invalid.email",
            "charlie@mail.sub.domain.co.uk",
        ]

        print("\nTesting:")
        for email in tests:
            try:
                domain = result.compiled_function(email)
                print(f"  {email:35} -> {domain}")
            except Exception as e:
                print(f"  {email:35} -> ERROR: {e}")

        return 0
    else:
        print(f"\nError: {result.error}")
        print(f"\nGenerated code:\n{result.source_code}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
