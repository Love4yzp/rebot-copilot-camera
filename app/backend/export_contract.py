"""Emit/check frontend API types without starting a server or opening CAN.

Run from app/: python -m backend.export_contract [--check].
Generation prints to stdout; --check compares the checked-in artifact.
"""

import argparse
import json
import logging
from pathlib import Path
import subprocess
import sys


def main():
    logging.disable(logging.CRITICAL)
    from .app import app

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", str(root / "frontend/contract/openapi.mjs")],
        input=json.dumps(app.openapi()),
        text=True,
        capture_output=True,
        check=True,
    )
    target = root / "frontend/src/generated/api.ts"
    if args.check:
        if not target.exists() or target.read_text() != result.stdout:
            raise SystemExit(
                "API types have drifted; regenerate with python -m backend.export_contract"
            )
    else:
        sys.stdout.write(result.stdout)


if __name__ == "__main__":
    main()
