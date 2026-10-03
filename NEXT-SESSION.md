# OK3D Stock App — resume notes

- App: `https://ok3d-stock.streamlit.app`
- Repo: `github.com/olaric8/ok3d-stock-app` (public — Community Cloud requires it)
- Workbook: `OK3D_Shadow_Database` — 64 products, 493 units
- Local: `C:\Users\USER\OneDrive\Documents\deepseek-harness\default-workspace\ok3d-stock-app`

Suites before any push:

```
verify_engine.py   117/117   data layer, schema, guardrails, batch sales
verify_import.py    24/24    import write path against a fake sheet
verify_ui.py        40/40    executes app.py; cloud-path, gate and sign-in
verify_auth.py       7/7     the access gate (swaps secrets.toml, then restores)
```

---

## PARKED: let it run for two weeks

Decision taken at the end of this session. The app is feature-complete for shop
use; what it lacks is **its own demand history**. Its Sales Ledger holds a handful
of entries because it went live this week.

Once there are two or three weeks of real transactions, the app can rank the
reorder list by actual velocity instead of inferring it. That was the next
planned piece of work and it is deliberately deferred until the data exists.

**What to look at when returning:**

1. Does the reorder list still read as noise? It shows ~44 lines, of which 39 are
   dormant products at zero stock. The planned fix is three filters on the
   dashboard: hide zero-stock lines with no sales, show only fast movers, and
   "show at most N" set per session by what can be funded.
2. Are the new minimums behaving? Fast movers at 5, everything else at 2.
3. Any stockouts on the five fast movers that matter? EXCEL 40G, KLIN 170G,
   MATCHES, XTREME 145G, G/MAMA 800G.

---

## Done this session

**Access control (the urgent one).** Per-person sign-in codes, no email addresses
required. Only SHA-256 hashes live in `ok3d_users` in Streamlit secrets; the codes
are in the git-ignored `access-codes.csv`. Codes do not expire — revocation is
deleting a line and redeploying, so a leaver loses access without disturbing
anyone else. The owner has a code and no bypass, deliberately.

**Batch sales.** One customer, several products, one transaction ID. Per-line
remove and quantity +/- in the basket.

**Crash fix.** A variable named `flagged` was rebound from StockRow objects to
strings inside the batch receipt, so any batch sale crashed the dashboard.

**Sidebar stripped for shop use.** No demo/live switch (staff could park the app
in demo mode and lose every sale to memory), no "Reset demo data", the workbook-id
box hidden once an id resolves, and Streamlit's toolbar hidden by CSS.

**Premium theme** in `.streamlit/config.toml`, with option names verified against
the 52 theme options the installed Streamlit registers.

**Data corrections.**
- `G/PRO 75G` opening balance 0 → 31; matched units now agree exactly with
  production at 493.
- `supa` deleted — a lowercase duplicate of `SUPA 50G`, with no movement and no
  ledger reference.
- `G/PRO 850G` → `G/PRO 800G` (renamed in the business).
- **Minimums rewritten: fast movers 5, everything else 2.** Was a flat 10 across
  all 64 lines, which flagged 53 of them. Now 44.

---

## Corrections to earlier claims (do not repeat these)

- **The "41 zero-stock lines" were never an import bug.** A dry run of
  `fix_opening_balances.py` showed 62 of 63 rows needed no change — production's
  own `Stock Summary` carried the same zeros. The owner has since confirmed those
  products are genuinely out of stock. Do not try to "fix" those numbers again.
- **The 1,044-unit MAGIK 80G "sale" is not an error.** The business records stock
  through an automated WhatsApp/Telegram bot; a movement cannot be edited, so a
  mistake is corrected by posting the opposite movement. That pair is a reversal
  and nets to zero. `analyse_demand.py` neutralises such pairs automatically —
  there are two in the log (1,044 x MAGIK 80G, 3 x G/MAMA 45G).

---

## Brand

Colours sampled from the OK3D logo: navy `#002870`, green `#60A008`, water blue
`#1868C8`.

**The app's green is `#4C7E06`, NOT the logo's `#60A008`.** The bright green scores
a contrast ratio of only 3.22 against white -- below the 4.5 needed for body text
-- so white-on-green buttons would be washed out. `#4C7E06` is the same hue
darkened, scoring 4.89. The bright green survives in the logo and app icon. Do
not "correct" it back.

Assets live in `brand/` (source artwork derivatives) and `static/` (what the app
serves):

| File | Use |
|---|---|
| `brand/ok3d-icon-512.png` | phone home-screen icon |
| `brand/ok3d-icon-192.png` | smaller icon variant |
| `brand/ok3d-lockup-white.png` | wordmark + rule + tagline, on white |
| `static/ok3d-lockup.png` | what the hero actually loads |

The hero serves the lockup as a **static file**, not base64. Streamlit reruns on
every interaction, so inlining 165 KB would re-send the image on every tap;
`server.enableStaticServing` fetches it once and the browser caches it.

Red and amber are deliberately NOT brand colours. A reorder alert must stay red
even though red is absent from the logo.

---

## Data scripts

| Script | Purpose |
|---|---|
| `analyse_demand.py` | neutralises reversal pairs, classifies fast/slow/dormant, writes `demand-clean.csv` |
| `propose_minimums.py` | applies the owner's thresholds, writes `minimum-stock-final.csv` |
| `apply_minimums.py` | writes column D; `--apply` to commit, verifies by re-reading |
| `fix_opening_balances.py` | syncs opening balances from production; `--apply` |
| `tidy_catalogue.py` | deletes/renames sheet rows safely; `--apply` |
| `make_access_codes.py` | regenerates codes and the hashed allowlist |
| `make-secrets-block-b64.ps1` | rebuilds the Streamlit secrets block |

All write scripts default to a dry run and print the diff first.

---

## Setup facts worth remembering

- **Secrets live only in Streamlit → Settings → Secrets.** Never committed.
  `credentials.json` and `.streamlit/secrets.toml` are both git-ignored.
- **The workbook id is in `.streamlit/config.toml` under `[ok3d]`**, not in
  secrets — it is a pointer, not a credential, and git delivers it intact. The
  Secrets editor kept splitting `key = "value"` across lines.
- **The service-account key is base64, folded across many lines** inside a
  triple-quoted TOML string, so the paste path cannot corrupt it.
- **Push with `push-repo-changes.ps1`**, never by hand. It refuses to publish
  credential files and verifies `.gitignore` still protects them.
- **Three things Community Cloud will not let the app change.** All belong to
  the hosting layer, outside the app's DOM or `<head>`:
  1. **"Manage app" button** -- rendered by Streamlit Cloud; app CSS cannot reach
     it. Verified in incognito that it does NOT appear to anyone not signed in to
     the owner's account, so the exposure is limited to accidental tampering.
  2. **"Hosted with Streamlit" badge** -- free-tier branding; only a paid plan
     removes it.
  3. **Browser tab favicon** -- still Streamlit's balloon. Attempted via
     `st.components.v1.html` injecting script that rewrites `<link rel="icon">`
     in the parent document; **it did not work**, so the component iframe does not
     have parent access in their environment. The script remains in `app.py`
     (`_install_favicon`) and fails silently, which is harmless -- but do not
     spend more time on it. The page TITLE is ours and does work.

  Self-hosting would fix all three at once, plus allow a real PWA manifest for
  full-screen launch. That is the main argument for moving off Community Cloud.

- **"Manage app" cannot be hidden.** It is rendered by Streamlit Cloud's hosting
  layer, outside the app's DOM, so app CSS cannot reach it. Verified in incognito:
  it does not appear for anyone who is not signed in to the owner's account.
- **The "Hosted with Streamlit" badge** is free-tier branding; only a paid plan
  removes it. Streamlit Community Cloud has no paid tier — the paid route is
  Streamlit in Snowflake, which is a consumption subscription, not a one-off fee.

---

## Traps that cost time

- `$PSScriptRoot` is **empty** when PowerShell runs a script via `-File`, so it
  cannot appear in a parameter default. Resolve it in the body.
- `$ErrorActionPreference = 'Stop'` turns native command stderr into *terminating*
  errors. Any `git` call must route through the `Invoke-Git` wrapper.
- Passing an array to a PowerShell script via `-File` does not work; `@(...)`
  arrives as separate tokens. Keep file lists inside the script.
- **A widget inside `st.form` does not rerun until submit**, so a submit button
  disabled on that widget's value can never be enabled.
- **`@st.cache_resource` is shared across runs in one process.** A test that
  simulates an unconfigured environment must clear the cache first, or it
  silently measures the previous run.
- **Running the suite is not proof.** Three separate bugs shipped while every
  check passed, because the checks never executed the branch that broke: demo
  mode hid a `NameError`, and the batch receipt's variable shadowing was only
  reachable after a sale.
