"""
Boeing Data Hub — Documentation Generator
==========================================
Generates all 7 documentation .docx files.

Usage:
    cd docs
    python generate_docs.py
"""

import sys
import os

# Ensure the docs directory is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gen_user_manual
import gen_project_walkthrough
import gen_developer_guide
import gen_api_reference
import gen_architecture
import gen_onboarding
import gen_config_reference


GENERATORS = [
    ("User Manual", gen_user_manual),
    ("Project Walkthrough", gen_project_walkthrough),
    ("Developer Guide", gen_developer_guide),
    ("API Reference", gen_api_reference),
    ("Architecture Documentation", gen_architecture),
    ("Onboarding Guide", gen_onboarding),
    ("Configuration Reference", gen_config_reference),
]


def main():
    print("=" * 60)
    print("Boeing Data Hub — Documentation Generator")
    print("=" * 60)
    print()

    results = []
    for name, module in GENERATORS:
        try:
            print(f"  Generating {name}...", end=" ", flush=True)
            path = module.generate()
            print(f"OK  ->  {os.path.basename(path)}")
            results.append((name, path, True))
        except Exception as e:
            print(f"FAILED: {e}")
            results.append((name, str(e), False))

    print()
    print("-" * 60)
    successes = sum(1 for _, _, ok in results if ok)
    print(f"  {successes}/{len(GENERATORS)} documents generated successfully.")
    print()

    for name, path, ok in results:
        status = "OK" if ok else "FAILED"
        print(f"  [{status}] {name}")
        if ok:
            print(f"         {path}")
    print()


if __name__ == "__main__":
    main()
