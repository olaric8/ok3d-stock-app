"""Generate per-person access codes and the hashed allowlist.

Codes are shown ONCE here, for handing to staff. Only SHA-256 hashes go into
secrets, so the repository, the Streamlit console, and anyone reading the code
never sees a usable code.

Run:  python make_access_codes.py
      python make_access_codes.py --names "A,B" --out access-codes.csv
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import secrets
import string
import sys

# Unambiguous alphabet: no O/0, I/1/L, S/5, so codes survive being read aloud or
# copied off a phone screen.
ALPHABET = "ACDEFGHJKMNPQRTUVWXY2346789"

DEFAULT_NAMES = ["Kunle", "Onyin", "Kike", "Chima", "Esther"]


def make_code() -> str:
    """OK3D-XXXX-XXXX -- long enough not to guess, short enough to type."""
    groups = [
        "".join(secrets.choice(ALPHABET) for _ in range(4)),
        "".join(secrets.choice(ALPHABET) for _ in range(4)),
    ]
    return f"OK3D-{groups[0]}-{groups[1]}"


def hash_code(code: str) -> str:
    """Hash exactly as the app does, so the two can never drift."""
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--names", default=",".join(DEFAULT_NAMES))
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("access-codes.csv"))
    parser.add_argument("--toml", type=pathlib.Path, default=pathlib.Path("access-block.txt"))
    args = parser.parse_args(argv)

    names = [n.strip() for n in args.names.split(",") if n.strip()]
    if not names:
        print("no names given")
        return 2

    rows = [(name, make_code()) for name in names]

    print("=" * 66)
    print("ACCESS CODES -- hand these out, then close this window")
    print("=" * 66)
    print()
    for name, code in rows:
        print(f"  {name:<10} {code}")
    print()
    print("  Codes are case-sensitive and do not expire.")
    print("  Removing someone = deleting their line from the allowlist.")
    print()

    # TOML block for Streamlit secrets. Hashes only -- no usable codes.
    print("=" * 66)
    print("PASTE THIS INTO STREAMLIT -> SETTINGS -> SECRETS")
    print("=" * 66)
    print()
    print("ok3d_users = \"\"\"")
    for name, code in rows:
        print(f"{name} = {hash_code(code)}")
    print('"""')
    print()

    # Files for safekeeping. NOT committed -- see .gitignore.
    args.out.write_text(
        "name,code\n" + "".join(f"{n},{c}\n" for n, c in rows), encoding="utf-8"
    )
    args.toml.write_text(
        "ok3d_users = \"\"\"\n"
        + "".join(f"{n} = {hash_code(c)}\n" for n, c in rows)
        + '"""\n',
        encoding="utf-8",
    )
    print(f"  codes written to : {args.out.resolve()}")
    print(f"  secrets block to : {args.toml.resolve()}")
    print("  both are git-ignored; keep the CSV, bin it once staff have their codes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
