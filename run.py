#!/usr/bin/env python3
"""Start PULSE + R4ISE Office together using this project's existing .env."""
import os
from pathlib import Path
import sys

if __name__ == '__main__':
    root = Path(__file__).resolve().parent
    os.chdir(root)
    sys.path.insert(0, str(root / 'src'))
    from sme.office.server import main
    main()
