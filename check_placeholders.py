"""
check_placeholders.py — pre-deploy guard against a specific, recurring bug.

SQLite uses '?' for query placeholders; Postgres uses '%s'. db.py's _q()
helper translates between them, so every SQL string passed to cur.execute()
must be wrapped in _q(...). If one isn't, it works perfectly in local
SQLite testing and then crashes in production Postgres with:

    psycopg2.errors.SyntaxError: syntax error at or near "ORDER"

Local tests cannot catch this (they run on SQLite), so this script checks
the source code directly instead.

Usage (run from the project root, before pushing to GitHub):
    python check_placeholders.py

Exits with code 1 and lists every offender if any are found, 0 if clean.
"""

import glob
import re
import sys


def find_unwrapped():
    problems = []
    for path in glob.glob("**/*.py", recursive=True):
        if path.endswith("check_placeholders.py"):
            continue
        src = open(path, encoding="utf-8").read()
        for m in re.finditer(r"cur\.execute\(", src):
            start = m.end()
            depth, i = 1, start
            while i < len(src) and depth:
                if src[i] == "(":
                    depth += 1
                elif src[i] == ")":
                    depth -= 1
                i += 1
            call = src[start:i - 1]
            if "?" in call and not call.lstrip().startswith("_q("):
                line = src[:m.start()].count("\n") + 1
                problems.append((path, line, call.lstrip()[:70].replace("\n", " ")))
    return problems


if __name__ == "__main__":
    problems = find_unwrapped()
    if problems:
        print("FOUND SQL with a raw '?' placeholder not wrapped in _q():\n")
        for path, line, snippet in problems:
            print(f"  {path}:{line}  ->  {snippet}")
        print(f"\n{len(problems)} problem(s). These will crash on Postgres. Wrap each SQL string in _q(...).")
        sys.exit(1)
    print("OK: every cur.execute() with a '?' placeholder is wrapped in _q().")
    sys.exit(0)
