# 📦 OK3D Stock App

Visual sales checkout and stock management for **OK3D**, built by **LemonLogic**.

Replaces the chat-based workflow — staff typing `Sale VIVA 800G 2 John` into
WhatsApp/Telegram — with a guarded visual interface: pick the product from a
dropdown, enter a quantity and customer, confirm. No SKUs, no syntax to
remember, and the stock check happens *before* anything is written.

---

## Quick start

From this folder:

```powershell
.\run.ps1
```

The script creates the environment on first run, installs dependencies, and opens
**http://localhost:8501**. Press `Ctrl+C` in the terminal to stop it.

It starts in **Demo mode** — 14 products seeded with OK3D's own product names
(`VIVA 800G`, `VIVA 170GRM`, `SUNLIGHT 1KG`, `RIM BLOCK 140G` …), four of them
already flagged REORDER so the alerting is visible immediately. Nothing is
written anywhere until you connect the Shadow Copy.

If PowerShell blocks the script:

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

### Verify the data layer

```powershell
.\.venv\Scripts\python.exe verify_engine.py    # 94 checks: schema, guardrails, maths
.\.venv\Scripts\python.exe verify_ui.py        # executes app.py against real Streamlit
```

Both exit `0` on success. Run them after any change — `verify_engine.py` is what
proves a rejected sale really does leave the sheet untouched.

---

## Connecting the Shadow Copy

The app **cannot** touch OK3D's live trading sheet. Three independent guards
enforce that, and all three fail closed:

1. **No default spreadsheet id exists in the code.** You must supply one
   explicitly, so the app cannot guess its way into production.
2. **The workbook title must contain `SHADOW`.** Otherwise the connection is
   refused with an explanatory error. (A deliberate override exists via
   `OK3D_ACKNOWLEDGE_SHADOW=1`, intended only for a workbook that cannot be
   renamed.)
3. **Both tabs must match the agreed headers exactly**, in order. A shifted or
   renamed column aborts the connection instead of silently writing sales into
   the wrong field.

### Setup

1. **Create the Shadow Copy workbook.** In Google Sheets, open OK3D's live sheet
   → *File → Make a copy* → name it something containing `SHADOW`, e.g.
   `OK3D Stock — SHADOW COPY`. Working on a copy is what keeps live data safe.
2. **Create the two tabs** with these exact headers (the app can also create
   them for you — see `ensure_tabs()` — but doing it by hand guarantees the
   layout is what you expect):

   **`Current Stock`**

   | A | B | C | D | E | F | G | H |
   |---|---|---|---|---|---|---|---|
   | SKU | Product name | Opening balance | Minimum stock | Total receipts | Total sales/issue | Current stock | Reorder status |

   **`Sales Ledger`**

   | A | B | C | D | E | F |
   |---|---|---|---|---|---|
   | Timestamp | Transaction ID | Product name | Quantity Sold | Customer Name | Handled By |

3. **Create a Google Cloud service account**, enable the **Google Sheets** and
   **Google Drive** APIs, and download its JSON key.
4. **Save the key as `credentials.json`** in this folder (project root).
5. **Share the Shadow Copy workbook** with the key's `client_email` as an
   **Editor**.
6. **Set the spreadsheet id** — the long token in the sheet URL
   (`docs.google.com/spreadsheets/d/<THIS_PART>/edit`):

   ```powershell
   $env:OK3D_SPREADSHEET_ID = "paste-the-id-here"
   .\run.ps1
   ```

   To make it permanent for your user account:
   ```powershell
   [Environment]::SetEnvironmentVariable('OK3D_SPREADSHEET_ID', 'paste-the-id-here', 'User')
   ```

7. In the app sidebar, switch **Connect to → Shadow Copy Google Sheet**. The
   sidebar shows the live connection state, the workbook title and the
   `client_email` it is authenticating as, so you can confirm the target before
   ringing up a sale.

### Rolling the shadow copy forward

The Shadow Copy is a working replica, not a live feed. Re-copy from production
when you want fresh numbers, then use **Products → Backfill SKUs** once to
populate the SKU column on any rows that came across without one.

---

## What the app does

### 🛒 Checkout — no SKUs, no syntax
The product dropdown is populated from the **Product name** column, so staff
never see or type an SKU. Selecting a product shows live stock, the minimum, and
what stock *will* be after the sale.

**Guardrail:** if the requested quantity exceeds **Current stock**, the confirm
button is disabled and a red block explains exactly how many units are actually
available. Nothing is written, and the same rule is enforced again inside the
engine — the UI check is convenience, the engine check is the guarantee.

On confirm, three things happen together:
- `Total sales/issue` increments by the quantity,
- `Current stock` is recomputed (`opening + receipts − sales`),
- `Reorder status` is recalculated,
- and a row is appended to `Sales Ledger` with timestamp, transaction id,
  product, quantity, customer and the staff member who handled it.

The audit row is written **first**. If the stock update then fails, the error
names the exact sheet row and the quantity to reconcile, so a failure can never
leave an untraceable stock movement.

### 📊 Stock dashboard
Executive grid of every line with all eight columns. **Lines whose Reorder status
is `REORDER` are highlighted light red across the whole row.** Search by product
or SKU, filter to flagged lines only, export to CSV. A restock list below shows
each flagged product with the units needed to get back above its minimum.

### 🧾 Sales ledger
Every transaction, newest first, with the full audit columns and a CSV export.

### 📥 Stock in
Records deliveries: increments `Total receipts`, recomputes stock, and clears the
REORDER flag once the line is back above minimum.

### 🗂️ Products
Add new products (the SKU is generated automatically as `OK3D-0001`,
`OK3D-0002`, …), and backfill SKUs on any row that is missing one. Hand-written
legacy codes such as `170GRM` are never overwritten.

---

## Column behaviour

`Current stock` is **written as a number, not a spreadsheet formula.** That keeps
it readable by gspread, by other tabs and by exports with no recalculation
round-trip. It is only ever derived from `Opening balance + Total receipts − Total
sales/issue`, so it cannot drift from those columns.

`Reorder status` is `REORDER` when `Current stock <= Minimum stock`, else `OK`.
Stock is floored at zero: a line cannot go negative through the app.

---

## Files

| File | Purpose |
| --- | --- |
| `run.ps1` | **Start here.** Sets up the venv if needed, then launches the app. |
| `sheets_engine.py` | Google Sheets engine: schema, validation, safety guard, and every business rule. |
| `app.py` | Streamlit UI — checkout, dashboard, ledger, stock-in, products. |
| `demo_backend.py` | In-memory backend seeded with OK3D product names, for offline demos. |
| `verify_engine.py` | 94-check headless verification of the data layer. |
| `verify_ui.py` | Executes `app.py` against real Streamlit to catch API misuse. |
| `requirements.txt` | Dependencies. |
| `credentials.json` | **You create this.** Service-account key (git-ignored). |
| `.piptmp/` | Disposable scratch space (see below). |

### Architecture

```
      app.py  ─────────────►  StockBackend          ← all business rules
                              (checkout guardrail,     live here, once
                               reorder status,
                               auto-SKU, ledger)
                                   ▲
                    ┌──────────────┴──────────────┐
             SheetsEngine                    DemoBackend
                    │                              │
           GoogleSheetsStore                   DemoStore
```

The UI never imports gspread and never decides where data lives. Because the
rules live in one class, the offline demo cannot drift from production behaviour
— `verify_engine.py` asserts that both backends share the same seam.

---

## Notes for whoever maintains this

- **`oauth2client` is deliberately not installed.** The brief listed it, but
  gspread 6.x removed its `oauth2client` authorize path entirely; authentication
  uses `google-auth` instead. Adding it back would introduce a deprecated,
  unmaintained package that no code path touches. See `requirements.txt`.
- **Scratch space is redirected into `.piptmp/`.** On this machine, pip and
  Streamlit writing temp files to the default OneDrive-backed path fail with
  `Errno 13 Permission denied`. `run.ps1` points `TEMP`/`TMP` at the project
  folder. The directory is disposable; delete it any time.
- **`virtualenv`, not `python -m venv`.** `ensurepip` fails in this environment;
  `virtualenv` seeds pip differently and works. `run.ps1` handles it.
- **Google Sheets quota.** Every sale is one ledger append plus one batched
  update — two API calls. Fine for a shop's volume; if you ever script bulk
  imports, batch them rather than looping.
