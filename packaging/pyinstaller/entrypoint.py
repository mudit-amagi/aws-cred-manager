"""Thin launcher outside the aws_cred_manager package.

Pointing PyInstaller's Analysis directly at a file *inside* the package
(e.g. src/aws_cred_manager/main.py) breaks the package's relative imports
once frozen ("attempted relative import with no known parent package").
Keeping the real entry point as a normal package import from a launcher
file outside the package avoids that entirely.
"""
from aws_cred_manager.main import main

if __name__ == "__main__":
    main()
