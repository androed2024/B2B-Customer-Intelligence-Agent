#!/usr/bin/env python3
"""
Utility script to generate bcrypt password hashes for auth.yaml

Usage:
    python generate_hashes.py password1 password2 password3

Example:
    python generate_hashes.py alice123 bob123 admin123
"""

import sys
from streamlit_authenticator import Hasher

def main():
    if len(sys.argv) < 2:
        print("Usage: python generate_hashes.py password1 [password2 ...]")
        print("Example: python generate_hashes.py alice123 bob123 admin123")
        sys.exit(1)
    
    passwords = sys.argv[1:]
    hashes = Hasher(passwords).generate()
    
    print("Generated bcrypt hashes:")
    print("=" * 50)
    for i, (password, hash_value) in enumerate(zip(passwords, hashes)):
        print(f"Password: {password}")
        print(f"Hash:     {hash_value}")
        print()

if __name__ == "__main__":
    main()
