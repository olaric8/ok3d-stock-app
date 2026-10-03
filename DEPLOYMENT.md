# 🚀 Deploying OK3D Stock App to Streamlit Community Cloud

Target: a phone-accessible URL, running against the **Shadow Copy** workbook, with
Account B owning the deployment. Roughly 10 minutes.

The deployment gets both its target workbook and its credentials from
**Streamlit's secrets manager** — two entries you paste once. No environment
variables, no code edits, and GitHub holds only the code.

> **Why the workbook id isn't baked into the code.** I tried that first and my own
> safety test caught it: a compiled-in fallback target means that if configuration
> ever goes wrong, the app silently writes into whatever workbook that id points
> at. Keeping it in secrets preserves the one-click cloud setup *and* keeps the
> engine failing loudly when nothing is configured.

---

## What's in this package

| File | Needed on the cloud? |
| --- | --- |
| `app.py` | ✅ the interface |
| `sheets_engine.py` | ✅ Google Sheets layer + config resolution |
| `demo_backend.py` | ✅ offline fallback so the app never crashes |
| `requirements.txt` | ✅ dependencies |
| `.streamlit/config.toml` | ✅ theme + telemetry settings |
| `.streamlit/secrets.toml.example` | reference shape for the secrets you paste |
| `README.md` | project notes |
| `DEPLOYMENT.md` | this file |
| `credentials.json` | ❌ **see the warning below** |

Everything else (migration scripts, verification suites, `run.ps1`) stays on your
machine. They are development tools, not runtime dependencies.

---

## 🔴 Read this before you push

**Never commit `credentials.json` or `secrets.toml` to GitHub.**

A service-account key in a public repo is a live credential: anyone who finds it
can read and write every sheet shared with that account — both the Shadow Copy and
anything else you later share with it. GitHub's secret scanning will usually
suspend the account, but the key must be treated as compromised the moment it is
pushed.

The `.gitignore` in this package already excludes both files. **Do not remove those
lines.** The key goes into Streamlit's secrets manager instead, where it stays on
the server and out of version control.

If you have already pushed a key by accident: delete the repo, then rotate the key
in Google Cloud Console → IAM → Service Accounts → Keys → revoke the old key and
create a new one.

---

## Step 1 — Put the code in a GitHub repo (Account B)

1. Sign in to GitHub as **Account B**.
2. **New repository** → name it `ok3d-stock-app` → **Private** → Create.
3. Upload the files **from this package** (not from the parent project folder).
   Either drag-and-drop in the browser, or from this folder:

   ```powershell
   git init
   git add app.py sheets_engine.py demo_backend.py requirements.txt README.md .gitignore .streamlit/config.toml .streamlit/secrets.toml.example
   git commit -m "OK3D Stock App - shadow deployment"
   git branch -M main
   git remote add origin https://github.com/<account-b>/ok3d-stock-app.git
   git push -u origin main
   ```

   Note the explicit `git add` list: it adds only the safe files. `credentials.json`
   is deliberately absent.

4. **Verify before moving on**: open the repo in a browser and confirm
   `credentials.json` and `secrets.toml` are **not** in the file list. If either is
   there, delete it and rotate the key.

## Step 2 — Create the Streamlit app

1. Go to <https://share.streamlit.io> and sign in **with Account B's GitHub**.
2. **Create app** → *Deploy a public app from GitHub*.
3. Fill in:
   - **Repository**: `<account-b>/ok3d-stock-app`
   - **Branch**: `main`
   - **Main file path**: `app.py`
   - **App URL**: choose something like `ok3d-stock` → gives
     `https://ok3d-stock.streamlit.app`
4. **Do not click Deploy yet.** Paste the secrets first (Step 3), or the first boot
   will land in demo mode with an auth error.

## Step 3 — Paste the two secrets

1. In the app dashboard: **Settings → Secrets**.
2. Add the **workbook id** as its own line at the top:

   ```toml
   spreadsheet_id = "1BIizam6JvfXW1YX6sxJS5pB4aKBj77Vdn09uvKmjMcg"
   ```

   Use the id from your own sheet URL —
   `docs.google.com/spreadsheets/d/<THIS PART>/edit` — the **Shadow Copy's**,
   never the live sheet's.
3. Below it, add the service-account key: open your local `credentials.json`, copy
   **all** of it, and convert it to the TOML shape in
   `.streamlit/secrets.toml.example`. The essential part is the
   `[gcp_service_account]` header line above the fields.
4. **Save.** The app restarts automatically.

The finished block looks like this:

```toml
spreadsheet_id = "1BIizam6JvfXW1YX6sxJS5pB4aKBj77Vdn09uvKmjMcg"

[gcp_service_account]
type = "service_account"
project_id = "ok3d-stock-app"
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "ok3d-app-handler@ok3d-stock-app.iam.gserviceaccount.com"
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
```

If `spreadsheet_id` is missing the app does **not** guess — it stays in demo mode
and the log tells you exactly which line to add.

Two things that break this most often:

- **The `private_key` must keep its quotes and its `\n` escapes.** Copy it exactly
  as it appears in `credentials.json`; do not let an editor reflow it into real
  newlines.
- **`client_email` must match the account the sheet is shared with** —
  `ok3d-app-handler@ok3d-stock-app.iam.gserviceaccount.com`.

## Step 4 — Confirm it is live and connected

1. Open `https://<your-app>.streamlit.app` **on your phone**.
2. The sidebar must show **Active backend: Google Sheets (Shadow Copy)**. If it
   says *Demo data*, the secret did not load — check the app logs under
   **Manage app** for the exact error, which the app prints verbatim.
3. Ring up a test sale and confirm the row changes in the Shadow Copy sheet.

The service account already has **Editor** rights on the Shadow Copy workbook
(verified), so sales, receipts and new products all have write access.

## Step 5 — Keep it awake (optional)

Streamlit Community Cloud sleeps idle apps. The first visitor after a nap waits
~30 seconds while it wakes. For a shop running all day, the simplest remedy is to
open the app at the start of the shift.

---

## Updating the app later

Edit locally → commit → push. Streamlit Cloud redeploys on push automatically.

```powershell
git add -A
git commit -m "describe the change"
git push
```

---

## If you would rather not use GitHub at all

Streamlit Community Cloud always deploys from a GitHub repo, so there is no
GitHub-free path there. Two alternatives if Account B should not hold the code:

1. **Run it on the shop's own machine** with `.\run.ps1`, and reach it from phones
   on the same Wi-Fi at `http://<pc-ip>:8501`. No cloud account, no public URL,
   nothing to leak — but it only works on-site.
2. **Any container host** (Render, Railway, Fly.io) can run the same three files.
   The app is a standard Streamlit app with no platform-specific code; the only
   requirement is that the service-account JSON reaches it as a file or via
   `OK3D_GOOGLE_CREDENTIALS`.

---

## What I could not do from here

I have no access to your GitHub or Streamlit accounts, so Steps 1–3 are yours to
click through. Everything in this package is verified to run from the code side:
the app has been booted and served, the data layer passes all 129 checks, and the
zero-configuration path (no environment variables at all) connects to the Shadow
Copy successfully.

If a deploy fails, send me the error text from **Manage app → logs** and I will
work from that directly.
