"""Exercise the access gate end to end.

Four cases:
  1. no allowlist configured      -> app must NOT load (fail closed)
  2. wrong code                   -> app must NOT load, error shown
  3. correct code                 -> app loads, name carried to "Handled by"
  4. after sign-in, reload        -> stays signed in (session persists)

The allowlist lives in .streamlit/secrets.toml, so this swaps in a test copy and
restores the real one afterwards.
"""

from __future__ import annotations

import hashlib
import shutil
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

HERE = Path(".").resolve()
SECRETS = HERE / ".streamlit" / "secrets.toml"
BACKUP = HERE / ".streamlit" / "secrets.real.bak"

CODE = "OK3D-TEST-CODE"
DIGEST = hashlib.sha256(CODE.encode("utf-8")).hexdigest()
OTHER = hashlib.sha256(b"OK3D-WRONG-CODE").hexdigest()

ALLOWLIST = f'ok3d_users = """\nKunle = {DIGEST}\nOnyin = {OTHER}\n"""\n'


def run_app(secrets_text: str):
    """Write secrets, run the app fresh, return the AppTest handle."""
    from streamlit.testing.v1 import AppTest

    SECRETS.write_text(secrets_text, encoding="utf-8")
    return AppTest.from_file(str(HERE / "app.py"), default_timeout=180).run()


def restored() -> None:
    if BACKUP.exists():
        shutil.move(str(BACKUP), str(SECRETS))


results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))


if SECRETS.exists():
    shutil.copy(str(SECRETS), str(BACKUP))

try:
    # ---- 1. no allowlist: must fail closed --------------------------------- #
    print("\n=== 1. no allowlist configured ===")
    at = run_app("[server]\nheadless = true\n")
    body = " ".join(m.value for m in at.markdown) + " ".join(e.value for e in at.error)
    check("app does not load without an allowlist",
          "no access list" in body.lower(), body[:160])
    # Text matching against the markdown is unreliable here: the app emits its
    # own CSS as markdown, and that stylesheet contains the words "Products"
    # and "stMetric". Count real elements instead -- that is what "the app did
    # not load" actually means.
    check("no KPI cards are rendered before sign-in",
          len(list(at.metric)) == 0, f"{len(list(at.metric))} metric(s)")
    check("no stock table is rendered before sign-in",
          len(list(at.dataframe)) == 0, f"{len(list(at.dataframe))} dataframe(s)")
    check("no tabs are rendered before sign-in",
          len(list(at.tabs)) == 0, f"{len(list(at.tabs))} tab group(s)")

    # ---- 2. wrong code ------------------------------------------------------ #
    print("\n=== 2. wrong code ===")
    at = run_app(ALLOWLIST)
    at.selectbox[0].set_value("Kunle")
    at.text_input[0].set_value("OK3D-NOT-THE-CODE")
    at.button[0].click().run()
    errors = " ".join(e.value for e in at.error)
    check("wrong code is rejected", "do not match" in errors.lower(), errors[:160])
    check("app still not loaded after a wrong code",
          not any(t.label == "Handled by" for t in at.text_input), "signed in unexpectedly")

    # ---- 3. correct code ---------------------------------------------------- #
    print("\n=== 3. correct code ===")
    at = run_app(ALLOWLIST)
    at.selectbox[0].set_value("Kunle")
    at.text_input[0].set_value(CODE)
    at.button[0].click().run()
    staff = [t.value for t in at.text_input if t.label == "Handled by"]
    check("correct code signs in", bool(staff), "no 'Handled by' field appeared")
    check("the signed-in name is carried into Handled by",
          staff and staff[0] == "Kunle", f"got {staff[:1]}")

    # ---- 4. session persists across a rerun --------------------------------- #
    print("\n=== 4. signed in, then rerun ===")
    at.run()
    still = [t.value for t in at.text_input if t.label == "Handled by"]
    check("stays signed in on rerun", bool(still), "gate reappeared")
finally:
    restored()
    print("\n(real secrets restored)")

failed = [n for n, ok, _ in results if not ok]
print("\n" + "=" * 66)
print(f"{len(results) - len(failed)}/{len(results)} gate checks passed")
if failed:
    print("FAILED:")
    for n in failed:
        print(f"  - {n}")
    raise SystemExit(1)
print("ACCESS GATE VERIFIED")
