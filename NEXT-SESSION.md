# OK3D Stock App — resume notes

Living document. Updated after each working session.

- URL: `https://ok3d-stock.streamlit.app` (add to phone home screen)
- Repo: `github.com/olaric8/ok3d-stock-app` (public — Streamlit Cloud requires it)
- Workbook: `OK3D_Shadow_Database` → 65 products, 462 units
- Local project: `C:\Users\USER\OneDrive\Documents\deepseek-harness\default-workspace\ok3d-stock-app`

---

## Done in session 2 (polish + batch sales)

- **Batch sales.** One customer, several products, one transaction. The engine
  gained `record_sale_batch()` and a `BatchSaleResult`; the Sales Ledger gets one
  row per product sharing a single Transaction ID, so a basket reads as one
  transaction. All validation happens before any write — an unknown product, a
  non-positive quantity, or insufficient stock refuses the whole basket and
  leaves the sheet untouched. Duplicate lines merge rather than double-charging.
- **`use_container_width` removed** — 9 occurrences replaced with
  `width='stretch'`. The app log is now clean of deprecation noise.
- **Streamlit's toolbar hidden** via CSS (`stToolbar`, `stDecoration`,
  `stAppDeployButton`), removing the top-right Share / star / pencil icons and
  the GitHub link that led out of the app.
- **Premium theme** in `.streamlit/config.toml`: rounded corners, softer borders,
  Inter type stack, larger metric values, reconciled status palette. Option names
  were **verified against the 52 theme options this Streamlit build registers** —
  ten of my first attempts were invalid and silently ignored.
- Dead `total_value` calculation removed from the basket view. `StockRow` carries
  no price field, so a `getattr` guard would have shown a confident `0`.

Suites now: **engine 117/117 · import 24/24 · UI 22/22**

```
verify_engine.py    data layer, schema, guardrails, id precedence, batch sales
verify_import.py    the import write path, against a fake sheet
verify_ui.py        executes app.py; cloud-path regression; batch UI wiring
```

Run all three before any push. They have caught real bugs repeatedly.

---

## Still open

- **Every product has `Minimum stock = 10`, identically.** That single fixed
  threshold is why 53 of 64 lines read REORDER. For slow movers it is simply
  wrong: `G/MAMA 1.7KG` holds 1 unit against a minimum of 10. Proposed but not
  done: set per-product minimums from actual turnover, which would cut the
  reorder list to lines that genuinely need action. **A reorder flag staff do not
  trust is worse than no flag.**
- **39 of 64 products are genuinely out of stock** (confirmed by the owner, not
  an import fault). The dashboard is correct; this is a restocking or catalogue
  question, not a software one.
- **Test rows in the Sales Ledger** to clear before staff rely on it:
  `OK3D-20261003-012030-OK4W` (EMEKA) and `OK3D-20261003-012709-7NE6`
  ("Ledger Write Test", mine). Confirm with the owner first — one entry may be a
  real sale.
- **No way to delete a product from the UI.** Discontinued lines need
  `tidy_catalogue.py` or a manual sheet edit.
- **Only tested on desktop.** One real basket sale on a phone is still worth
  doing before staff use it in anger.
- **SKUs still differ from production's scheme** (shadow `OK3D-0013` vs
  production `OK3D-015` for the same product). Harmless while the two stay
  separate; matters if they are ever reconciled.

---

## Data corrections applied (this session)

- **`G/PRO 75G`** — opening balance 0 → 31, from production's `Stock Summary`.
  After the fix, matched units agree exactly: **493 both sides**.
- **`supa`** — deleted. A lowercase duplicate of `SUPA 50G` (row 16, 69 units,
  3 real sales), with zero movement and no ledger reference.
- **`G/PRO 850G` → `G/PRO 800G`** — renamed; the product was renamed in the
  business.

Important correction for future sessions: **the "41 zero-stock lines" were never
an import bug.** A dry run of `fix_opening_balances.py` showed 62 of 63 rows
needed no change — production's own `Stock Summary` carried the same zeros. The
shadow copy was faithful all along. Do not "fix" those numbers again.

---
## Polishing ideas not yet done

1. **Demo-mode notice wording.** Still says *"Switch the sidebar to Shadow Copy
   Google Sheet once \credentials.json\ is in place"* — wrong for the cloud,
   where the key lives in secrets. Reword once, correctly for both environments.
2. **Hero and CSS block.** All bespoke styling sits in the CSS string at the top
   of \pp.py\ (hero gradient, metric cards, alert blocks, receipts). That is the
   file to touch for further looks.
3. **Mobile layout.** Sidebar starts expanded; consider collapsing it on phones.
   Check tap targets in the batch basket, which carries more controls than the
   single-sale form.

---

## Things worth remembering about this setup

- **Secrets live only in Streamlit → Settings → Secrets.** Not in the repo.
  `credentials.json` and `.streamlit/secrets.toml` are both git-ignored —
  verified with `git check-ignore`. Never commit either.
- **The workbook id is in `.streamlit/config.toml` under `[ok3d]`**, not in
  secrets. It's a pointer, not a credential, and git delivers it intact. The
  paste path in the Secrets editor kept splitting `key = "value"` across lines.
- **The service-account key is base64, folded across many lines** inside a
  triple-quoted TOML string. That format absorbs the line breaks this paste path
  inserts. Regenerate with `make-secrets-block-b64.ps1` if it ever needs redoing.
- **The app is gated by per-person access codes** (no emails needed). Only
  SHA-256 hashes live in `ok3d_users` in Streamlit secrets; the codes themselves
  are in the git-ignored `access-codes.csv`. Regenerate or revoke with
  `make_access_codes.py`. Codes do **not** expire — revocation is deleting a line
  and redeploying, so a leaver loses access without disturbing anyone else.
- **Push with `push-repo-changes.ps1`**, never by hand. It refuses to publish
  credential files and verifies `.gitignore` still protects them.

---

## Traps that cost time last session

- `$PSScriptRoot` is **empty** when PowerShell runs a script via `-File`, so it
  cannot appear in a parameter default. Resolve it in the body.
- `$ErrorActionPreference = 'Stop'` turns native command stderr into *terminating*
  errors. Any `git` call must route through the `Invoke-Git` wrapper.
- Streamlit Cloud secrets are UI-only — no API, no CLI. The **Secrets
  diagnostics** expander in the sidebar exists so we can see which keys the
  running app actually has, instead of guessing from screenshots.
- **Running the suite is not proof.** Twice a change shipped untested while all
  checks passed, because the checks only exercised demo mode. New code paths
  need a check that actually reaches them.
