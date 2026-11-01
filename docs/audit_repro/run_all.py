"""Run every audit reproduction script in an isolated state directory and print its verdicts.

Usage, from the repository root:  python docs/audit_repro/run_all.py

Each script gets a fresh NEUROCORE_STATE_DIR, so no settings, flows or databases in the working
tree are read or written. No network access and no LLM server are needed.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def main() -> int:
    confirmed = 0
    for script in sorted(HERE.glob("repro_*.py")):
        with tempfile.TemporaryDirectory(prefix="neurocore-audit-") as state_dir:
            env = {**os.environ, "NEUROCORE_STATE_DIR": state_dir}
            proc = subprocess.run([sys.executable, str(script)], cwd=ROOT, env=env,
                                  capture_output=True, text=True, timeout=300)
        verdicts = [line for line in proc.stdout.splitlines() if line.startswith("  VERDICT")
                    or line.startswith("VERDICT")]
        print(f"== {script.name} (exit {proc.returncode})")
        for line in verdicts or ["  (no verdict printed)\n" + proc.stderr[-800:]]:
            print("  " + line.strip())
        confirmed += sum("BUG CONFIRMED" in line for line in verdicts)
    print(f"\n{confirmed} confirmed defect verdict(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
