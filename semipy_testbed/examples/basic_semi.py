"""
Basic example: standalone semi() for data processing.

This example shows how to use the testbed to generate a simple function
that processes email addresses.

Run with: python examples/basic_semi.py
"""
from semipy_testbed import infer_semiformal, SimpleInferenceResult
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
load_dotenv(override=False)


def extract_domain_example():
    """Extract domain from email address."""
    print("\n" + "=" * 60)
    print("EXAMPLE 1: Extract Email Domain")
    print("=" * 60)

    user_spec = (
        "Extract the domain name (part after @) from this email address. "
        "Return just the domain, not the full email. "
        "If input is not valid email, return empty string."
    )

    email = "Alice.Smith@company.co.uk"

    result = infer_semiformal(
        user_spec=user_spec,
        free_variables={"email": email},
        sample_input={"args": [email], "kwargs": {}},
        expected_type=str,
        free_variable_names=["email"],
        verbose=True,
    )

    print("\n--- Result ---")
    print(f"Success: {result.success}")
    if result.success:
        print(
            f"Function signature: {result.compiled_function.__code__.co_varnames}")
        test_emails = [
            "alice@example.com",
            "bob.jones@company.org",
            "invalid-email",
            "charlie@sub.domain.co.uk",
        ]
        print("\nTesting with various inputs:")
        for email_test in test_emails:
            try:
                domain = result.compiled_function(email_test) # type: ignore
                print(f"  {email_test:30} -> {domain}")
            except Exception as e:
                print(f"  {email_test:30} -> ERROR: {e}")
    else:
        print(f"Error: {result.error}")
        print(f"\nGenerated code:\n{result.source_code}")


def classify_string_example():
    """Classify a string into categories."""
    print("\n" + "=" * 60)
    print("EXAMPLE 2: Classify Text")
    print("=" * 60)

    user_spec = (
        "Classify the input text into one of these categories: "
        "'greeting', 'question', 'statement', or 'command'. "
        "Return only the category name in lowercase."
    )

    text_samples = [
        "Hello, how are you?",
        "What time is the meeting?",
        "The weather is nice today.",
        "Please submit your report.",
    ]

    result = infer_semiformal(
        user_spec=user_spec,
        free_variables={"text": text_samples[0]},
        sample_input={"args": [text_samples[0]], "kwargs": {}},
        expected_type=str,
        free_variable_names=["text"],
        verbose=True,
    )

    print("\n--- Result ---")
    print(f"Success: {result.success}")
    if result.success:
        print("\nClassifying sample texts:")
        for text in text_samples:
            try:
                category = result.compiled_function(text) # type: ignore
                print(f"  '{text:35}' -> {category}")
            except Exception as e:
                print(f"  '{text:35}' -> ERROR: {e}")
    else:
        print(f"Error: {result.error}")


def parse_json_example():
    """Parse structured data."""
    print("\n" + "=" * 60)
    print("EXAMPLE 3: Parse Structured Data")
    print("=" * 60)

    user_spec = (
        "Given a JSON string representing a person (with fields name, age, city), "
        "extract all three fields and return a dict with keys: name, age, city. "
        "Age should be an integer. "
        "If parsing fails, return {\"name\": \"\", \"age\": 0, \"city\": \"\"}."
    )

    sample_json = '{"name": "Alice", "age": 30, "city": "New York"}'

    result = infer_semiformal(
        user_spec=user_spec,
        free_variables={"json_str": sample_json},
        sample_input={"args": [sample_json], "kwargs": {}},
        expected_type=dict,
        free_variable_names=["json_str"],
        verbose=True,
    )

    print("\n--- Result ---")
    print(f"Success: {result.success}")
    if result.success:
        print("\nParsing JSON examples:")
        json_examples = [
            '{"name": "Bob", "age": 25, "city": "Boston"}',
            '{"name": "Carol", "age": 28, "city": "Chicago"}',
            'invalid json',
        ]
        for json_test in json_examples:
            try:
                parsed = result.compiled_function(json_test)
                print(f"  Result: {parsed}")
            except Exception as e:
                print(f"  Result: ERROR: {e}")
    else:
        print(f"Error: {result.error}")


if __name__ == "__main__":
    # Make sure API key is set
    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set in environment")
        print("Set it with: export OPENAI_API_KEY='your-key-here'")
        sys.exit(1)

    print("=" * 60)
    print("SEMIPY TESTBED - BASIC EXAMPLES")
    print("=" * 60)

    # Run examples
    extract_domain_example()
    # classify_string_example()
    # parse_json_example()

    print("\n" + "=" * 60)
    print("Examples complete!")
    print("=" * 60)
