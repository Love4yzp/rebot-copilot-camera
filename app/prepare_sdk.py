"""Apply the checked-in SDK extension patch to its exact, validated baseline.

Run before uv sync. Safe to repeat; refuses a different commit or overlapping
local edits. No network, staging, commits, or changes to calibration/assets.
"""

from pathlib import Path
import subprocess

BASELINE = "d54040596faa94bdc4f8ad93f3f06b33dfe3a1bf"
ROOT = Path(__file__).resolve().parent


def prepare():
    sdk = ROOT / "vendor/reBotArm_control_py"
    patch = ROOT / "vendor-patches/rebot-sdk.patch"

    def git(*args, check=False):
        if args and args[0] == "apply":
            args = ("apply", "--ignore-space-change", *args[1:])
        return subprocess.run(
            ["git", "-C", str(sdk), *args], capture_output=True, text=True, check=check
        )

    head = git("rev-parse", "HEAD", check=True).stdout.strip()
    if head != BASELINE:
        raise SystemExit(f"SDK baseline {head} is not validated; expected {BASELINE}")
    if git("apply", "--reverse", "--check", str(patch)).returncode == 0:
        return
    result = git("apply", "--check", str(patch))
    if result.returncode:
        raise SystemExit(
            "SDK has overlapping local edits; preserve them and reconcile manually.\n"
            + result.stderr
        )
    git("apply", str(patch), check=True)


if __name__ == "__main__":
    prepare()
