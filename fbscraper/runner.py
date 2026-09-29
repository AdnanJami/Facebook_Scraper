"""Runs several fbscraper commands one after another, for the GUI.

    python -m fbscraper.runner '[["scrape", "<url>", "--max-posts", "20"], ["process"]]'

Output goes to stdout (the GUI points it at a log file). The last line is "__DONE__ <exit code>",
so the GUI can tell a finished run from one that is still going or was stopped.
"""
import json
import subprocess
import sys


def main() -> int:
    steps = json.loads(sys.argv[1])
    code = 0
    for i, args in enumerate(steps, 1):
        print(f"=== Step {i}/{len(steps)}: {' '.join(args)}", flush=True)
        code = subprocess.call([sys.executable, "-u", "-m", "fbscraper", *args])
        if code != 0:
            print(f"=== Step {i} failed (exit code {code}); remaining steps skipped", flush=True)
            break
    print(f"__DONE__ {code}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
