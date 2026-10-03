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

- **41 of 65 lines read zero stock; 55 flagged REORDER.** Cause: the import wrote
  opening balance `0` for products with no movement in `OK3D RECENT.xlsx`. Real
  counts live in production's `Stock Summary` tab. `fix_opening_balances.py`
  syncs them — it matched 462 units on both sides when last run.
- **Test rows in the Sales Ledger** to clear before staff use it:
  `OK3D-20261003-012030-OK4W` (EMEKA, user's test) and
  `OK3D-20261003-012709-7NE6` ("Ledger Write Test", mine, from verifying writes).
  Note `OK3D-20261003-013427-G6V7` (ERIC, 1 × SUPA 50G) may be a **real** sale —
  check before deleting anything.
- **SKUs don't match production numbering.** The shadow copy auto-numbered
  `OK3D-0001…`; production uses its own scheme (`SUPA 50G` is `OK3D-0013` here,
  `OK3D-015` there). Harmless while the two stay separate.
- **Mobile layout.** Sidebar starts expanded; consider collapsing it for phone
  use and check tap targets on the checkout forms.

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
