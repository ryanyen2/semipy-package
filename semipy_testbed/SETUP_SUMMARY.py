#!/usr/bin/env python3
"""
SEMIPY TESTBED SETUP SUMMARY
============================

This file is an executable summary of what was created and how to use it.

What is the testbed?
- A simplified, self-contained code generation + gist execution environment
- No version control, no complex resolution logic, no UI overhead
- Perfect for testing generation ideas and working on infrastructure (docker, kernel gateway, etc.)

Quick Start:
    export OPENROUTER_API_KEY='sk-...'
    python semipy_testbed/examples/basic_semi.py

Full docs: semipy_testbed/README.md
"""

# Example: minimal inference
if __name__ == "__main__":
    import os
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    
    if not os.environ.get("OPENROUTER_API_KEY"):
        print(__doc__)
        print("\n" + "=" * 60)
        print("SETUP CHECK")
        print("=" * 60)
        print("✓ Testbed created at: semipy_testbed/")
        print("  - Core modules: config, types, inference, gist_builder, gist_executor, validator")
        print("  - Examples: basic_semi.py, apache_log_simple.py")
        print("  - Data: sample_logs.txt, sample.csv")
        print("  - Docker: Dockerfile.gist")
        print("\n✗ MISSING: OPENROUTER_API_KEY environment variable")
        print("\nNext steps:")
        print("  1. Set API key: export OPENROUTER_API_KEY='sk-...'")
        print("  2. Try example: python semipy_testbed/examples/basic_semi.py")
        print("  3. Read docs: cat semipy_testbed/README.md")
        sys.exit(1)
    
    # Run quick demo
    from semipy_testbed import infer_semiformal, configure
    
    configure(verbose=False)
    print("\nRunning quick demo (no verbose output)...")
    
    result = infer_semiformal(
        user_spec="Extract the domain from an email address (part after @)",
        free_variables={"email": "demo@example.com"},
        sample_input={"args": ["demo@example.com"], "kwargs": {}},
        expected_type=str,
        free_variable_names=["email"],
    )
    
    if result.success:
        print("✓ SUCCESS: Generated and validated function!")
        print(f"  Function name: {result.compiled_function.__name__}")
        test_email = "test@example.org"
        domain = result.compiled_function(test_email)
        print(f"  Test: {test_email} -> {domain}")
    else:
        print(f"✗ FAILED: {result.error}")
        sys.exit(1)
