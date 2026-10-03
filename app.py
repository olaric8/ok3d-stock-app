"""
app.py
======

**OK3D Stock App** -- visual sales checkout and stock management workspace.

Built by LemonLogic to move OK3D's stock operations off WhatsApp/Telegram text
syntax (``Sale VIVA 800G 2 John``) and into a guarded visual interface.

Run with:
    .\\run.ps1
    # or
    .\\.venv\\Scripts\\python.exe -m streamlit run app.py

The UI talks only to :class:`sheets_engine.StockBackend`. It never imports
gspread and never decides where data is stored -- that is entirely the engine's
job, which is what keeps the write-safety checks in one place.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from typing import Any, Optional

import pandas as pd
import streamlit as st

from demo_backend import DemoBackend
from sheets_engine import (
    LEDGER_HEADERS,
    REORDER_FLAG,
    SPREADSHEET_ID_ENV,
    STOCK_HEADERS,
    ConfigurationError,
    EngineError,
    SafetyBoundaryError,
    SchemaMismatchError,
    SheetsEngine,
    StockBackend,
    StockError,
    StockRow,
    TransactionResult,
    build_engine,
    CONFIG_FILE,
    credentials_path_from_env,
    credentials_summary,
    spreadsheet_id_from_config_file,
    spreadsheet_id_from_env,
)

# --------------------------------------------------------------------------- #
# Page + theme
# --------------------------------------------------------------------------- #

st.set_page_config(
    page_title="OK3D Stock App | LemonLogic",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

CUSTOM_CSS = """
<style>
  .block-container { padding-top: 1.4rem; padding-bottom: 3rem; max-width: 1550px; }
  #MainMenu, footer, header [data-testid="stStatusWidget"] { visibility: hidden; }

  /* Hide Streamlit's own toolbar -- the Share / star / pencil / GitHub icons in
     the top-right corner. They are platform chrome, not app UI: on a shop
     tablet they invite staff into the Streamlit editor, and the GitHub icon
     leads out of the app entirely. */
  [data-testid="stToolbar"] { display: none !important; }
  [data-testid="stDecoration"] { display: none !important; }
  [data-testid="stAppDeployButton"] { display: none !important; }
  /* Streamlit Cloud's "Manage app" button sits in the bottom-right of a hosted
     app and opens the settings/secrets panel. Hiding it keeps staff inside the
     app; it is still reachable from the Streamlit console for the owner. */
  [data-testid="stToolbarActionButton"] { display: none !important; }
  .stToolbarActionButton { display: none !important; }

  /* hero */
  .ok-hero {
    background: linear-gradient(120deg, #00204F 0%, #002870 48%, #1868C8 78%, #4C7E06 118%);
    border-radius: 18px; padding: 24px 30px; margin-bottom: 8px;
    box-shadow: 0 12px 30px rgba(0,0,0,.22);
    display: flex; align-items: center; justify-content: space-between; gap: 18px; flex-wrap: wrap;
  }
  /* Brand plate: the wordmark is navy on white, so it needs a white field on the
     navy hero. Rounded to match the theme's baseRadius. */
  .ok-brand img {
    display: block;
    height: 62px; width: auto;
    background: #FFFFFF;
    padding: 10px 16px;
    border-radius: 12px;
    box-shadow: 0 6px 18px rgba(0,0,0,.22);
  }
  .ok-brand p {
    color: rgba(255,255,255,.78);
    margin: 10px 0 0;
    font-size: 13.5px;
    letter-spacing: .2px;
  }
  @media (max-width: 640px) {
    .ok-brand img { height: 46px; padding: 8px 12px; }
    .ok-brand p { font-size: 12px; }
  }
  /* Brand plate on the left, status pill on the right. The hero already wraps,
     so this only takes effect once there is width for two columns -- on a phone
     the pill drops below the logo instead of being squeezed. */
  .ok-hero > div:first-child { flex: 1 1 auto; }
  .ok-hero > .ok-pill { margin-left: auto; align-self: center; }
  .ok-hero h1 { color: #FFFFFF; font-size: 29px; font-weight: 800; margin: 0; letter-spacing: -.4px; }
  .ok-hero p { color: rgba(255,255,255,.82); margin: 6px 0 0; font-size: 14px; }
  .ok-pill {
    background: rgba(255,255,255,.16); color: #EAFBF3; border: 1px solid rgba(255,255,255,.35);
    border-radius: 999px; padding: 8px 18px; font-size: 12px; font-weight: 700;
    letter-spacing: .7px; text-transform: uppercase; white-space: nowrap;
  }

  /* metric cards */
  div[data-testid="stMetric"] {
    background: rgba(0, 40, 112, .045); border: 1px solid rgba(0, 40, 112, .14);
    border-radius: 14px; padding: 15px 18px 13px;
  }
  div[data-testid="stMetricLabel"] p {
    font-size: 11.5px !important; font-weight: 700 !important;
    letter-spacing: .8px; text-transform: uppercase; opacity: .68;
  }
  div[data-testid="stMetricValue"] { font-size: 26px; font-weight: 750; }

  /* section heading */
  .ok-section {
    display: flex; align-items: baseline; gap: 10px; margin: 20px 0 4px;
    padding-bottom: 8px; border-bottom: 2px solid rgba(0, 40, 112, .18);
  }
  .ok-section span.t { font-size: 17px; font-weight: 750; color: #00205A; }
  .ok-section span.s { font-size: 12.5px; opacity: .62; }

  /* blocks */
  .ok-alert {
    background: rgba(214,48,49,.10); border: 1px solid rgba(214,48,49,.45);
    border-left: 5px solid #D63031; border-radius: 10px; padding: 12px 16px;
    margin: 10px 0; font-size: 14px;
  }
  .ok-alert b { color: #D63031; }
  .ok-ok {
    background: rgba(76,126,6,.10); border: 1px solid rgba(76,126,6,.38);
    border-left: 5px solid #4C7E06; border-radius: 10px; padding: 12px 16px;
    margin: 10px 0; font-size: 14px;
  }
  .ok-block {
    background: rgba(214,48,49,.10); border: 1px solid rgba(214,48,49,.50);
    border-left: 5px solid #D63031; border-radius: 10px; padding: 14px 18px;
    margin: 8px 0 14px; font-size: 14.5px; line-height: 1.55;
  }
  .ok-block b { color: #D63031; }
  .ok-demo {
    background: rgba(242,169,59,.14); border: 1px solid rgba(242,169,59,.55);
    border-left: 5px solid #F2A93B; border-radius: 10px; padding: 13px 17px;
    margin: 8px 0 16px; font-size: 14px; line-height: 1.5;
  }
  .ok-demo b { color: #A9701A; }

  /* product card */
  .ok-card {
    background: rgba(0, 40, 112, .045); border: 1px solid rgba(0, 40, 112, .14);
    border-radius: 14px; padding: 16px 18px; margin: 10px 0 14px;
  }
  .ok-kv { font-size: 13.5px; margin: 3px 0; }
  .ok-kv b { opacity: .60; font-weight: 600; }

  /* receipt */
  .ok-receipt {
    background: rgba(76,126,6,.08); border: 1px solid rgba(76,126,6,.40);
    border-radius: 14px; padding: 18px 22px; margin: 6px 0 16px;
  }
  .ok-receipt .hdr { font-size: 15px; font-weight: 750; color: #3F6A05; margin-bottom: 10px;
    letter-spacing: .3px; text-transform: uppercase; }
  .ok-receipt .row { font-size: 14px; margin: 4px 0; }
  .ok-receipt .row b { opacity: .62; font-weight: 600; display: inline-block; min-width: 130px; }
  .ok-receipt .big { font-size: 17px; font-weight: 750; }

  div[data-testid="stTabs"] button { font-weight: 650; font-size: 14.5px; }
  .stButton > button, .stFormSubmitButton > button { border-radius: 9px; font-weight: 650; }
  div[data-testid="stDataFrame"] { border-radius: 10px; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Brand header
# --------------------------------------------------------------------------- #
# Defined above the access gate because the sign-in screen calls it -- below the
# gate it would be a NameError on the first page a visitor sees.


def brand_header(subtitle: str, pill: str = "", pill_colour: str = "") -> None:
    """
    The app's brand header: OK3D lockup, strapline, optional status pill.

    Shared by the sign-in screen and the workspace so the two cannot drift apart.
    The lockup sits on a white plate because the wordmark is navy on white and
    would vanish against the navy hero.

    The sign-in screen passes no pill: it cannot know whether the workbook is
    reachable, because the gate stops before any data is fetched. Claiming
    "Connected" there would be a claim the app has not earned.
    """
    pill_markup = ""
    if pill:
        pill_markup = (
            f'<div class="ok-pill" style="border-color:{pill_colour}">'
            f'<span style="display:inline-block;width:.5rem;height:.5rem;'
            f'border-radius:50%;background:{pill_colour};margin-right:.45rem;'
            f'vertical-align:middle"></span>{pill}</div>'
        )

    st.markdown(
        f"""
        <div class="ok-hero">
          <div class="ok-brand">
            <img src="app/static/ok3d-lockup.png" alt="OK3D">
            <p>{subtitle}</p>
          </div>
          {pill_markup}
        </div>
        """,
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# Access gate
# --------------------------------------------------------------------------- #
# Everyone needs their own code, including the owner -- handing the owner a
# bypass would create exactly the shared-secret weakness this replaces.
#
# Codes do NOT expire. Streamlit Cloud sleeps idle apps, so expiry would mean
# staff hitting "code expired" every morning while doing nothing about the risk
# that matters. Revocation is explicit: delete the person's line, redeploy.
#
# Hashes only: the secrets entry holds SHA-256 digests, so a usable code is not
# recoverable from the repo, the Streamlit console, or a screenshot.


def _allowed_users() -> dict[str, str]:
    """``{display name: sha256 hex}`` from the ``ok3d_users`` secret."""
    try:
        raw = str(st.secrets.get("ok3d_users", "") or "")
    except Exception:  # noqa: BLE001
        return {}
    users: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, digest = line.partition("=")
        name = name.strip()
        digest = digest.strip().lower()
        if name and len(digest) == 64:
            users[name] = digest
    return users


def _code_matches(code: str, digest: str) -> bool:
    """Compare a typed code against a stored digest in constant time."""
    import hmac

    try:
        candidate = hashlib.sha256(code.strip().encode("utf-8")).hexdigest()
    except Exception:  # noqa: BLE001
        return False
    return hmac.compare_digest(candidate, digest)


_ALLOWED = _allowed_users()

if not st.session_state.get("ok3d_signed_in"):
    brand_header("Sign in to continue \u00b7 stock &amp; sales workspace")

    if not _ALLOWED:
        # Fail CLOSED. An app with no allowlist configured must not be an open
        # app -- that would be the very hole this gate exists to close.
        st.error("This app has no access list configured, so nobody can sign in.")
        st.caption(
            "Add `ok3d_users` in **Settings \u2192 Secrets** (one `Name = sha256hash` "
            "per line) and reboot the app."
        )
        st.stop()

    st.caption("Enter your name and the access code you were given.")
    with st.form("ok3d-signin", clear_on_submit=False):
        _who = st.selectbox("Your name", options=sorted(_ALLOWED), index=None,
                            placeholder="Select your name")
        _code = st.text_input("Access code", type="password",
                              placeholder="OK3D-XXXX-XXXX")
        _go = st.form_submit_button("Sign in", type="primary", width="stretch")

    if _go:
        if _who and _code and _code_matches(_code, _ALLOWED[_who]):
            st.session_state["ok3d_signed_in"] = True
            st.session_state["staff"] = _who
            st.session_state.pop("ok3d_attempts", None)
            st.rerun()
        else:
            st.session_state["ok3d_attempts"] = st.session_state.get("ok3d_attempts", 0) + 1
            st.error("That name and code do not match. Check for typos and try again.")

    st.caption("Codes are personal. Do not share yours \u2014 tell the owner if someone asks for it.")
    st.stop()


# --------------------------------------------------------------------------- #
# Session state
# --------------------------------------------------------------------------- #

_NOW = datetime.now()
for _key, _value in {
    "mode": "demo",                      # "demo" | "live"
    "spreadsheet_id": os.environ.get(SPREADSHEET_ID_ENV, "").strip(),
    "staff": "Chidi",
    "receipt": None,
    "batch_receipt": None,
    "basket": [],
}.items():
    st.session_state.setdefault(_key, _value)


# --------------------------------------------------------------------------- #
# Checkout field reset
# --------------------------------------------------------------------------- #
# The three checkout widgets all use STABLE keys: "co-product", "co-qty" and
# "co-customer". Two reasons:
#
#   1. Streamlit stores a widget's value under its key and DISCARDS that value
#      when the key changes. The original generated keys therefore erased the
#      operator's selection on every rerun, which is why the stock banner never
#      populated.
#   2. The product selector sits outside the form so it reruns the script on
#      click. Stable keys are what carry quantity and customer through that
#      rerun, so nothing already typed is lost mid-sale.
#
# Because the keys are fixed, the post-sale reset cannot be done by swapping in
# a new key. It is queued as a flag and applied here, at the top of the next
# run -- the only point at which Streamlit permits a widget's session_state
# entry to be written. Writing it after the widget has been created raises
# StreamlitWidgetAlreadyInstantiatedError.
if st.session_state.pop("pending_clear_inputs", False):
    st.session_state["co-product"] = None
    st.session_state["co-qty"] = 1
    st.session_state["co-customer"] = ""


def money_free(value: float) -> str:
    return f"{value:,.0f}"


# --------------------------------------------------------------------------- #
# Favicon
# --------------------------------------------------------------------------- #
# The tab icon is set in the document <head> that Streamlit Cloud's hosting layer
# generates, which app CSS cannot reach. st.components.v1.html renders into a
# same-origin iframe, so script inside it can reach the parent document and
# rewrite the <link rel="icon"> entries.
#
# This is best-effort: if a browser blocks parent access, the tab keeps
# Streamlit's icon and nothing else is affected. The title is unaffected either
# way -- set_page_config already sets it.
def _install_favicon() -> None:
    """Point the browser tab at the OK3D icon."""
    try:
        import streamlit.components.v1 as components

        components.html(
            """
            <script>
            (function () {
              try {
                var doc = window.parent.document;
                var href = "app/static/ok3d-icon-192.png";
                // Absolute, resolved against the parent page so the iframe's own
                // base URL cannot misdirect it.
                var abs = new URL(href, doc.location.href).href;
                var links = doc.querySelectorAll(
                  'link[rel="icon"], link[rel="shortcut icon"], link[rel="apple-touch-icon"]'
                );
                if (links.length === 0) {
                  var made = doc.createElement('link');
                  made.rel = 'icon';
                  made.href = abs;
                  doc.head.appendChild(made);
                } else {
                  links.forEach(function (l) { l.href = abs; });
                }
              } catch (e) {
                /* cross-origin or sandboxed: keep the default icon */
              }
            })();
            </script>
            """,
            height=0,
            width=0,
        )
    except Exception:  # noqa: BLE001
        pass


def section(title: str, subtitle: str = "") -> None:
    st.markdown(
        f'<div class="ok-section"><span class="t">{title}</span>'
        f'<span class="s">{subtitle}</span></div>',
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# Backend resolution
# --------------------------------------------------------------------------- #


@st.cache_resource(show_spinner=False)
def load_backend(mode: str, spreadsheet_id: str) -> tuple[Optional[StockBackend], str, str]:
    """
    Build the backend once per (mode, id).

    Returns ``(backend, error_kind, error_message)``. On any failure the caller
    drops to the demo backend -- the app is never left without a working screen,
    and a failed connection is always surfaced rather than hidden.
    """
    if mode != "live":
        return DemoBackend(), "", ""

    try:
        engine = build_engine(spreadsheet_id=spreadsheet_id)
        engine.connect()
        return engine, "", ""
    except SafetyBoundaryError as exc:
        return None, "safety", str(exc)
    except SchemaMismatchError as exc:
        return None, "schema", str(exc)
    except ConfigurationError as exc:
        return None, "config", str(exc)
    except EngineError as exc:
        return None, "engine", str(exc)
    except Exception as exc:  # noqa: BLE001
        return None, "unexpected", f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- #
# Sidebar: connection control
# --------------------------------------------------------------------------- #

with st.sidebar:
    # There is deliberately NO demo/live switch. A staff member could park the
    # app in demo mode, and every sale they recorded would vanish into memory at
    # the end of the session. The app always targets the workbook; if it cannot
    # be reached it says so loudly rather than quietly going offline.
    st.session_state["mode"] = "live"

    creds = credentials_summary()
    creds_path = credentials_path_from_env()
    on_cloud = not creds.get("present") and not creds_path.exists()

    try:
        _keys_for_detail = set(dict(st.secrets).keys())
    except Exception:  # noqa: BLE001
        _keys_for_detail = set()

    # ---------------------------------------------------------------- setup -- #
    # Shown only when something is genuinely wrong, so it stays actionable.
    if not creds.get("present"):
        st.error("Setup: no service-account key")
        if on_cloud:
            st.caption(
                "There is no `credentials.json` here by design on a cloud deployment. "
                "Add the key in **Settings \u2192 Secrets**, or rebuild the block locally "
                "with `make-secrets-block-b64.ps1`."
            )
        else:
            st.caption(
                "Save the service-account key as `credentials.json` beside `app.py`, or "
                "point `OK3D_GOOGLE_CREDENTIALS` at it."
            )

    # Resolve the id HERE, with the engine's own precedence, before anything
    # decides whether setup is incomplete. Reading session_state alone was wrong:
    # it holds only an explicit entry, so the app could be connected via
    # config.toml while this block still showed an empty box and a warning.
    _resolved_id = spreadsheet_id_from_env()
    if _resolved_id and not st.session_state.get("spreadsheet_id"):
        st.session_state["spreadsheet_id"] = _resolved_id

    if not _resolved_id:
        # Nothing supplied an id. The box appears ONLY in this case -- an
        # always-visible field lets anyone retarget the app mid-shift.
        st.session_state["spreadsheet_id"] = st.text_input(
            "Spreadsheet ID",
            value="",
            placeholder="paste the id from the /d/<id>/edit URL",
            help="The id of the OK3D Shadow Copy workbook - never the live trading sheet.",
        ).strip()
        if not st.session_state["spreadsheet_id"]:
            # One specific message, no empty input left sitting above it.
            _config_id = spreadsheet_id_from_config_file()
            if "spreadsheet_id" in _keys_for_detail:
                st.warning("Setup: the spreadsheet id in secrets is empty")
                st.caption(
                    "The key exists but carries no value. Give it one line, "
                    "`spreadsheet_id = \"<id>\"`, with the quotes -- or set it under "
                    "`[ok3d]` in `.streamlit/config.toml`."
                )
            elif not _config_id:
                st.warning("Setup: no spreadsheet id")
                st.caption(
                    "Neither secrets nor `config.toml` supplied a workbook id. Add "
                    "`spreadsheet_id` under `[ok3d]` in `.streamlit/config.toml`, or the "
                    "first line of **Settings \u2192 Secrets**."
                )
            else:
                st.warning("Setup: no spreadsheet id")
                st.caption("An id resolved but the workbook could not be opened.")

    # ------------------------------------------------------------ connection -- #
    backend, error_kind, error_message = load_backend(
        st.session_state["mode"], st.session_state["spreadsheet_id"]
    )

    if backend is None:
        # Surface the failure, then keep the workspace usable rather than
        # showing staff a dead page.
        titles = {
            "safety": "Safety boundary blocked this connection",
            "schema": "Sheet layout does not match the agreed schema",
            "config": "Setup incomplete",
            "engine": "Engine error",
            "unexpected": "Unexpected error",
        }
        st.error(titles.get(error_kind, "Connection failed"))
        with st.expander("Full detail", expanded=error_kind in {"safety", "schema"}):
            st.code(error_message, language=None)

            # Show which source supplied a workbook id. The id is a pointer, not a
            # credential, and it is already public in the repository -- printing it
            # here saves a round of screenshot ping-pong when setup misbehaves.
            st.markdown("**Workbook id resolution**")
            _cfg = spreadsheet_id_from_config_file()
            st.write(f"- environment `{SPREADSHEET_ID_ENV}`: "
                     f"{'set' if __import__('os').environ.get(SPREADSHEET_ID_ENV) else 'not set'}")
            st.write(f"- secrets `spreadsheet_id`: "
                     f"{'present' if 'spreadsheet_id' in _keys_for_detail else 'absent'}")
            st.write(f"- `config.toml` at `{CONFIG_FILE}`: "
                     f"{('found ' + _cfg) if _cfg else 'no id found'}")
            st.write(f"- **resolved: {spreadsheet_id_from_env() or 'NOTHING'}**")

            # The secrets read-out lives inside the error path only: it names the
            # keys the app can see, which is what fixes a broken deployment and is
            # of no use to anyone else.
            st.markdown("**Secret keys visible to the app**")
            try:
                visible = dict(st.secrets)
            except Exception as exc:  # noqa: BLE001
                visible = {}
                st.write(f"st.secrets unreadable: {type(exc).__name__}: {exc}")
            if not visible:
                st.write("Nothing loaded - the secrets file may be missing or invalid TOML.")
            else:
                for key in sorted(visible):
                    value = visible[key]
                    if isinstance(value, dict):
                        detail = f"table, {len(value)} field(s)"
                    else:
                        text = str(value)
                        # Shape only, never content.
                        detail = f"{len(text)} chars" if text else "EMPTY"
                    st.write(f"- `{key}` - {detail}")

        st.info("Showing in-memory sample data until the connection is fixed.")
        backend = DemoBackend()

    connected = not backend.is_demo
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:.5rem;margin:.35rem 0'>"
        f"<span style='width:.55rem;height:.55rem;border-radius:50%;"
        f"background:{'#4C7E06' if connected else '#D63031'};display:inline-block'></span>"
        f"<b>{'Connected' if connected else 'Not connected'}</b></div>",
        unsafe_allow_html=True,
    )
    if creds.get("present"):
        st.caption(f"`{creds.get('client_email', 'n/a')}`")
    if connected:
        st.caption(
            f"`{backend.spreadsheet_title}` \u00b7 last read {backend.last_read_utc or '\u2014'}"
        )
    else:
        st.caption("Reading sample data - nothing is being saved.")

    st.divider()

    # ----------------------------------------------------------------- staff -- #
    st.session_state["staff"] = st.text_input(
        "Handled by",
        value=st.session_state["staff"],
        help="Recorded against every sale in the Sales Ledger.",
    )

    st.divider()

    # ------------------------------------------------------------------ data -- #
    if st.button("Refresh from source", width="stretch"):
        load_backend.clear()
        st.rerun()

_install_favicon()

# --------------------------------------------------------------------------- #
# Hero
# --------------------------------------------------------------------------- #

# Words staff recognise, matching the sidebar status line exactly. "Shadow Copy"
# meant nothing outside this project.
mode_pill = "Sample data" if backend.is_demo else "Connected"
pill_colour = "#C62828" if backend.is_demo else "#4C7E06"
brand_header(
    "Stock &amp; sales workspace · built by LemonLogic",
    pill=mode_pill,
    pill_colour=pill_colour,
)

if backend.is_demo:
    # This is a FALLBACK, not a mode anyone chooses: the app always targets the
    # workbook, so reaching here means the connection failed. Say that plainly
    # instead of pointing at a sidebar switch that no longer exists.
    st.markdown(
        '<div class="ok-demo"><b>Not connected — showing sample data.</b> '
        "The workbook could not be opened, so nothing below is real and "
        "<b>no sale recorded here will be saved</b>. Check the setup message in the "
        "sidebar; if it is not obvious, tell whoever maintains the app rather than "
        "recording sales.</div>",
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# Data load
# --------------------------------------------------------------------------- #

try:
    stock_rows: list[StockRow] = backend.fetch_stock(force=True)
    ledger_rows: list[dict[str, Any]] = backend.fetch_ledger(limit=300)
    kpis = backend.summary()
    flagged = backend.low_stock()
except EngineError as exc:
    st.error(f"Could not read the stock sheet: {exc}")
    st.stop()


# --------------------------------------------------------------------------- #
# KPIs
# --------------------------------------------------------------------------- #

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Products", f"{kpis['products']}")
k2.metric("Units on hand", f"{kpis['units']:,}")
k3.metric("Needs reorder", f"{kpis['low']}", delta="action needed" if kpis["low"] else "all clear",
          delta_color="inverse" if kpis["low"] else "normal")
k4.metric("Out of stock", f"{kpis['out_of_stock']}")
k5.metric("Healthy lines", f"{kpis['healthy']}")

if flagged:
    names = " · ".join(f"**{r.product}** ({r.current}/{r.minimum})" for r in flagged[:5])
    extra = f" · +{len(flagged) - 5} more" if len(flagged) > 5 else ""
    st.markdown(
        f'<div class="ok-alert">🔔 <b>{len(flagged)} line(s) at or below minimum stock:</b> {names}{extra}</div>',
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        '<div class="ok-ok">✅ <b>All lines are above their minimum stock.</b></div>',
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# View helpers
# --------------------------------------------------------------------------- #


def stock_dataframe(rows: list[StockRow]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "SKU": r.sku,
                "Product name": r.product,
                "Opening balance": r.opening,
                "Minimum stock": r.minimum,
                "Total receipts": r.receipts,
                "Total sales/issue": r.sales,
                "Current stock": r.current,
                "Reorder status": r.reorder,
            }
            for r in rows
        ],
        columns=STOCK_HEADERS,
    )


def ledger_dataframe(entries: list[dict[str, Any]]) -> pd.DataFrame:
    if not entries:
        return pd.DataFrame(columns=LEDGER_HEADERS)
    frame = pd.DataFrame(entries)
    for column in LEDGER_HEADERS:
        if column not in frame.columns:
            frame[column] = ""
    frame["Timestamp"] = pd.to_datetime(frame["Timestamp"], errors="coerce")
    return frame[LEDGER_HEADERS]


def render_receipt(result: TransactionResult, kind: str = "sale") -> None:
    label = "Sale recorded" if kind == "sale" else "Stock received"
    rows = [
        ("Transaction ID", f"<code>{result.transaction_id}</code>"),
        ("Product", f"<span class='big'>{result.product}</span>"),
        ("Quantity", f"<span class='big'>{result.quantity}</span>"),
        ("Stock", f"{result.stock_before} → <b>{result.stock_after}</b>"),
    ]
    if kind == "sale":
        rows.append(("Customer", result.customer or "—"))
    rows.append(("Handled by", result.handled_by or "—"))
    status = (
        f"<span style='color:#D63031;font-weight:700'>REORDER</span>"
        if result.reorder == REORDER_FLAG
        else "<span style='color:#4C7E06;font-weight:700'>OK</span>"
    )
    rows.append(("Reorder status", status))

    body = "".join(f"<div class='row'><b>{k}</b>{v}</div>" for k, v in rows)
    st.markdown(f'<div class="ok-receipt"><div class="hdr">✅ {label}</div>{body}</div>', unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Tabs
# --------------------------------------------------------------------------- #

tab_checkout, tab_dashboard, tab_ledger, tab_stockin, tab_products = st.tabs(
    [
        "🛒 Checkout",
        "📊 Stock dashboard",
        "🧾 Sales ledger",
        "📥 Stock in",
        "🗂️ Products",
    ]
)

# ------------------------------- checkout ---------------------------------- #
with tab_checkout:
    sub_single, sub_batch = st.tabs(["🛒 Single sale", "🧺 Batch sale (multiple products)"])

# --------------------------- single-product sale --------------------------- #
with sub_single:
    section("Visual checkout", "pick the product by name — SKUs are handled for you")

    if st.session_state.get("receipt"):
        render_receipt(st.session_state["receipt"])
        if st.button("Start next sale", key="clear-receipt"):
            st.session_state["receipt"] = None
            st.rerun()

    products = backend.list_products()
    if not products:
        st.warning("No products yet. Add one from the **Products** tab first.")
    else:
        # ------------------------------------------------------------------ #
        # The product selector lives OUTSIDE the form on purpose.
        #
        # Widgets inside a form only send their value when the submit button is
        # pressed, so a selector in there would not refresh the stock banner
        # until the sale was already committed. Outside the form it triggers an
        # immediate rerun on click, which is what pulls the live stock count,
        # the banner text and the button state straight onto the screen.
        #
        # All three checkout widgets use STABLE keys. Streamlit stores a
        # widget's value under its key, so stable keys are what keep quantity
        # and customer intact across the reruns the selector now causes.
        # ------------------------------------------------------------------ #
        product = st.selectbox(
            "Product name",
            options=products,
            index=None,
            placeholder="Type to search, e.g. VIVA 800G",
            key="co-product",
        )

        with st.form("checkout-form", clear_on_submit=False):
            col_qty, col_customer = st.columns([1, 2])
            with col_qty:
                quantity = st.number_input(
                    "Quantity sold", min_value=1, max_value=1_000_000, value=1, step=1,
                    key="co-qty",
                )
            with col_customer:
                customer = st.text_input(
                    "Customer name", placeholder="e.g. Mama Ngozi Stores",
                    key="co-customer",
                )

            # ---- live stock read-out + guardrail ---------------------------- #
            # Recomputed on every rerun, so choosing a product updates this
            # block before anything is submitted.
            selected = backend.get_stock(product) if product else None
            blocked = False

            if selected is None:
                st.info("Select a product above to see live stock before committing.")
            else:
                after = selected.current - int(quantity)
                cols = st.columns(4)
                cols[0].metric("In stock now", f"{selected.current}")
                cols[1].metric("After this sale", f"{max(after, 0)}")
                cols[2].metric("Minimum stock", f"{selected.minimum}")
                cols[3].metric("SKU", selected.sku or "—")

                if int(quantity) > selected.current:
                    blocked = True
                    st.markdown(
                        f'<div class="ok-block">⛔ <b>Checkout blocked — not enough stock.</b><br>'
                        f"Only <b>{selected.current}</b> unit(s) of <b>{selected.product}</b> are in stock, "
                        f"but <b>{int(quantity)}</b> were requested.<br>"
                        "Nothing has been written to the sheet. Reduce the quantity, or record a "
                        "delivery on the <b>Stock in</b> tab first.</div>",
                        unsafe_allow_html=True,
                    )
                elif after <= selected.minimum:
                    st.warning(
                        f"Stock will drop to {max(after, 0)}, at or below the minimum of "
                        f"{selected.minimum} — this line will flip to REORDER."
                    )

            submitted = st.form_submit_button(
                "✅ Confirm sale",
                type="primary",
                width='stretch',
                disabled=blocked or selected is None,
            )

        if submitted and selected is not None:
            try:
                result = backend.record_sale(
                    selected.product,
                    int(quantity),
                    customer=customer.strip(),
                    handled_by=st.session_state["staff"],
                )
                st.session_state["receipt"] = result
                # Queue the three checkout fields for reset at the top of the
                # next run -- see the note above "pending_clear_inputs". Writing
                # their session_state entries here would raise
                # StreamlitWidgetAlreadyInstantiatedError.
                st.session_state["pending_clear_inputs"] = True
                st.toast(f"{result.quantity} x {result.product} sold · stock now {result.stock_after}", icon="✅")
                st.rerun()
            except StockError as exc:
                st.error(f"Sale blocked: {exc}")
            except EngineError as exc:
                st.error(f"Could not complete the sale: {exc}")

        st.caption(
            "This replaces the old chat syntax — `Sale VIVA 800G 2 John` is now three clicks, "
            "and the stock check happens before anything is written."
        )

# ------------------------------ batch sale --------------------------------- #
with sub_batch:
    section(
        "Batch sale",
        "one customer, several products — recorded as a single transaction",
    )

    basket = st.session_state.setdefault("basket", [])

    add_col, view_col = st.columns([3, 4], gap="large")

    with add_col:
        st.markdown("**Add items**")
        products = backend.list_products()
        if not products:
            st.warning("No products yet. Add one from the **Products** tab first.")
        else:
            # NOTE: the selector and quantity MUST stay outside st.form. Form
            # widgets do not rerun until the form is submitted, so inside a form
            # `basket_product` would still be None on the first render -- which
            # kept the submit button disabled, and a disabled button can never
            # submit the form. The single-sale checkout uses the same pattern.
            basket_product = st.selectbox(
                "Product name",
                options=products,
                index=None,
                placeholder="Type to search, e.g. VIVA 800G",
                key="basket-product",
            )
            basket_qty = st.number_input(
                "Quantity", min_value=1, max_value=1_000_000, value=1, step=1,
                key="basket-qty",
            )
            with st.form("basket-add-form", clear_on_submit=False):
                basket_customer = st.text_input(
                    "Customer name", placeholder="e.g. Mama Ngozi Stores",
                    key="basket-customer",
                )
                add_submitted = st.form_submit_button(
                    "➕ Add to basket", type="primary", width="stretch",
                )

            if add_submitted and basket_product:
                existing = next(
                    (item for item in basket if item["product"].casefold() == basket_product.casefold()),
                    None,
                )
                if existing:
                    existing["quantity"] += int(basket_qty)
                else:
                    basket.append({"product": basket_product, "quantity": int(basket_qty)})
                st.session_state["basket"] = basket
                st.rerun()

            picked = backend.get_stock(basket_product) if basket_product else None
            if picked is not None:
                st.caption(
                    f"In stock now: **{picked.current}** · minimum {picked.minimum} · "
                    f"SKU `{picked.sku or '—'}`"
                )

    with view_col:
        st.markdown("**Basket**")

        if not basket:
            st.info("No items yet. Add products on the left, then confirm the sale.")
        else:
            # --- evaluate every line against LIVE stock -------------------- #
            issues: list[str] = []
            rows_for_view: list[dict[str, object]] = []
            total_units = 0
            will_reorder = 0

            for index, item in enumerate(basket):
                row = backend.get_stock(item["product"])
                if row is None:
                    issues.append(f"**{item['product']}** is no longer in Current Stock.")
                    continue
                wanted = int(item["quantity"])
                total_units += wanted
                short = wanted > row.current
                after = max(row.current - wanted, 0)
                if short:
                    issues.append(
                        f"**{row.product}** — only {row.current} in stock, {wanted} requested."
                    )
                elif after <= row.minimum:
                    will_reorder += 1
                rows_for_view.append(
                    {
                        "#": index + 1,
                        "Product": row.product,
                        "Qty": wanted,
                        "In stock": row.current,
                        "After": after,
                        "Status": "⛔ short" if short else ("🟡 hits minimum" if after <= row.minimum else "🟢 ok"),
                    }
                )

            # One row per basket line, each with its own Remove control. A single
            # "Clear basket" button forced staff to rebuild the whole order to
            # drop one item, which is not how a customer changes their mind.
            head = st.columns([6, 2, 1])
            head[0].caption("**Product**")
            head[1].caption("**Qty**")
            head[2].caption("**Remove**")

            for index, item in enumerate(list(basket)):
                line = next(
                    (r for r in rows_for_view if r["#"] == index + 1),
                    None,
                )
                row_cols = st.columns([6, 2, 1])
                with row_cols[0]:
                    if line is None:
                        st.markdown(f"⚠️ ~~{item['product']}~~")
                    else:
                        flag = "" if line["Status"] == "🟢 ok" else f" · {line['Status']}"
                        st.markdown(
                            f"**{line['Product']}**  \n"
                            f"<span style='opacity:.7;font-size:.85em'>"
                            f"{line['In stock']} in stock → {line['After']} after{flag}</span>",
                            unsafe_allow_html=True,
                        )
                with row_cols[1]:
                    dec_col, qty_col, inc_col = st.columns(3)
                    if dec_col.button("−", key=f"basket-dec-{index}", help="One fewer"):
                        item["quantity"] = max(1, int(item["quantity"]) - 1)
                        st.session_state["basket"] = basket
                        st.rerun()
                    qty_col.markdown(
                        f"<div style='text-align:center;font-weight:700;padding-top:.35rem'>"
                        f"{int(item['quantity'])}</div>",
                        unsafe_allow_html=True,
                    )
                    if inc_col.button("+", key=f"basket-inc-{index}", help="One more"):
                        item["quantity"] = int(item["quantity"]) + 1
                        st.session_state["basket"] = basket
                        st.rerun()
                with row_cols[2]:
                    if st.button("🗑️", key=f"basket-remove-{index}", help="Remove this line"):
                        basket.pop(index)
                        st.session_state["basket"] = basket
                        st.rerun()

            summary_cols = st.columns(3)
            summary_cols[0].metric("Product lines", f"{len(rows_for_view)}")
            summary_cols[1].metric("Total units", f"{total_units}")
            summary_cols[2].metric(
                "Will need reorder",
                f"{will_reorder}",
                delta="action after sale" if will_reorder else "all stay healthy",
                delta_color="inverse" if will_reorder else "normal",
            )

            if issues:
                st.markdown(
                    '<div class="ok-block">⛔ <b>This basket cannot be sold as it stands.</b><br>'
                    + "<br>".join(issues)
                    + "<br>Adjust the quantities, or record a delivery on the <b>Stock in</b> tab."
                    + "</div>",
                    unsafe_allow_html=True,
                )

            act_left, act_right = st.columns(2)
            with act_left:
                if st.button("🗑️ Clear basket", width="stretch", key="basket-clear"):
                    st.session_state["basket"] = []
                    st.rerun()
            with act_right:
                confirm = st.button(
                    "✅ Confirm batch sale",
                    type="primary",
                    width="stretch",
                    disabled=bool(issues) or not rows_for_view,
                    key="basket-confirm",
                )

            if confirm:
                try:
                    batch = backend.record_sale_batch(
                        [(item["product"], int(item["quantity"])) for item in basket],
                        customer=(basket_customer or "").strip(),
                        handled_by=st.session_state["staff"],
                    )
                    st.session_state["batch_receipt"] = batch
                    st.session_state["basket"] = []
                    st.session_state.pop("basket-customer", None)
                    st.toast(
                        f"{batch.product_count} product(s), {batch.total_units} unit(s) sold",
                        icon="🧺",
                    )
                    st.rerun()
                except StockError as exc:
                    st.error(f"Batch blocked: {exc}")
                except EngineError as exc:
                    st.error(f"Could not complete the batch: {exc}")

    receipt = st.session_state.get("batch_receipt")
    if receipt is not None:
        st.divider()
        rows_html = "".join(
            "<div class='row'><b>{qty} × {product}</b>"
            "<span style='opacity:.75'>{before} → {after} on hand</span></div>".format(
                qty=line.quantity,
                product=line.product,
                before=line.stock_before,
                after=line.stock_after,
            )
            for line in receipt.lines
        )
        # Named `reorder_names`, NOT `flagged`: `flagged` above holds the
        # dashboard's list of StockRow objects, and rebinding it here to strings
        # made the dashboard read `.product` off a str and crash the page.
        reorder_names = [
            line.product for line in receipt.lines if line.reorder == REORDER_FLAG
        ]
        note = (
            f"<div class='row' style='margin-top:8px'><b>Now REORDER</b>"
            f"<span style='color:#D63031;font-weight:700'>{', '.join(reorder_names)}</span></div>"
            if reorder_names
            else ""
        )
        st.markdown(
            f'<div class="ok-receipt"><div class="hdr">✅ Batch sale recorded</div>'
            f"<div class='row'><b>Transaction ID</b><code>{receipt.transaction_id}</code></div>"
            f"<div class='row'><b>Customer</b>{receipt.customer or '—'}</div>"
            f"<div class='row'><b>Handled by</b>{receipt.handled_by or '—'}</div>"
            f"<div class='row'><b>Total units</b><span class='big'>{receipt.total_units}</span></div>"
            f"{rows_html}{note}</div>",
            unsafe_allow_html=True,
        )
        st.caption(
            "All lines above share one Transaction ID, so the Sales Ledger shows them "
            "as a single transaction."
        )
        if st.button("Start next batch", key="basket-clear-receipt"):
            st.session_state["batch_receipt"] = None
            st.rerun()

# ------------------------------ dashboard ---------------------------------- #
with tab_dashboard:
    section("Executive stock dashboard", "lines in light red have hit their minimum")

    search = st.text_input(
        "Search", placeholder="Filter by product name or SKU…", key="dash-search"
    )
    only_flagged = st.checkbox("Show only REORDER lines", value=False, key="dash-flagged")

    view_rows = stock_rows
    if search.strip():
        needle = search.strip().casefold()
        view_rows = [
            r for r in view_rows
            if needle in r.product.casefold() or needle in r.sku.casefold()
        ]
    if only_flagged:
        view_rows = [r for r in view_rows if r.is_low]

    if not view_rows:
        st.info("No lines match the current filter.")
    else:
        frame = stock_dataframe(view_rows)
        st.dataframe(
            frame,
            width='stretch',
            hide_index=True,
            height=min(680, 44 + 35 * len(frame)),
            column_config={
                "Opening balance": st.column_config.NumberColumn(format="%d"),
                "Minimum stock": st.column_config.NumberColumn(format="%d"),
                "Total receipts": st.column_config.NumberColumn(format="%d"),
                "Total sales/issue": st.column_config.NumberColumn(format="%d"),
                "Current stock": st.column_config.NumberColumn(format="%d"),
            },
        )
        st.caption(
            f"{len(view_rows)} of {len(stock_rows)} line(s) · "
            f"check the Reorder status column for {REORDER_FLAG} lines"
        )
        st.download_button(
            "⬇️ Export this view (CSV)",
            data=frame.to_csv(index=False).encode("utf-8"),
            file_name=f"ok3d-stock-{_NOW:%Y%m%d-%H%M}.csv",
            mime="text/csv",
        )

    if flagged:
        section("Restock list", "what to reorder, and how many to get back above minimum")
        restock = pd.DataFrame(
            [
                {
                    "Product name": r.product,
                    "SKU": r.sku,
                    "Current stock": r.current,
                    "Minimum stock": r.minimum,
                    "Units to order": r.shortfall,
                }
                for r in flagged
            ]
        )
        st.dataframe(restock, width='stretch', hide_index=True)
    else:
        st.success("Nothing needs reordering.")

# -------------------------------- ledger ----------------------------------- #
with tab_ledger:
    section("Sales ledger", "every checkout, newest first")

    if not ledger_rows:
        st.info("No transactions recorded yet. Ring up a sale on the Checkout tab and it will appear here.")
    else:
        frame = ledger_dataframe(ledger_rows)
        st.dataframe(
            frame,
            width='stretch',
            hide_index=True,
            height=min(700, 44 + 35 * len(frame)),
            column_config={
                "Timestamp": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm:ss"),
                "Quantity Sold": st.column_config.NumberColumn(format="%d"),
            },
        )
        total_units = int(pd.to_numeric(frame["Quantity Sold"], errors="coerce").fillna(0).sum())
        st.caption(f"{len(frame)} transaction(s) · {total_units} unit(s) sold")
        st.download_button(
            "⬇️ Export ledger (CSV)",
            data=frame.to_csv(index=False).encode("utf-8"),
            file_name=f"ok3d-sales-ledger-{_NOW:%Y%m%d-%H%M}.csv",
            mime="text/csv",
        )

# ------------------------------- stock in ---------------------------------- #
with tab_stockin:
    section("Record a delivery", "increments Total receipts and recomputes current stock")

    products = backend.list_products()
    if not products:
        st.warning("No products yet — add one from the Products tab first.")
    else:
        # clear_on_submit=True resets these three fields after each delivery, so
        # fixed keys are safe here and keep the widget identity stable.
        with st.form("receipt-form", clear_on_submit=True):
            r_product = st.selectbox(
                "Product name", options=products, index=None,
                placeholder="Type to search…", key="rec-product",
            )
            col_a, col_b = st.columns([1, 2])
            with col_a:
                r_qty = st.number_input(
                    "Quantity received", min_value=1, max_value=1_000_000, value=1, step=1,
                    key="rec-qty",
                )
            with col_b:
                r_note = st.text_input(
                    "Note (optional)", placeholder="e.g. Supplier invoice 4471",
                    key="rec-note",
                )
            r_submitted = st.form_submit_button("📥 Add to stock", type="primary", width='stretch')

        if r_submitted and r_product:
            try:
                result = backend.record_receipt(
                    r_product, int(r_qty), handled_by=st.session_state["staff"], note=r_note.strip()
                )
                st.session_state["receipt"] = result
                st.toast(f"{result.quantity} x {result.product} received · stock now {result.stock_after}", icon="📥")
                st.rerun()
            except EngineError as exc:
                st.error(str(exc))

# ------------------------------- products ---------------------------------- #
with tab_products:
    left, right = st.columns(2, gap="large")

    with left:
        section("Add a product", "SKU is generated automatically")
        with st.form("add-product-form", clear_on_submit=True):
            p_name = st.text_input("Product name *", placeholder="e.g. VIVA 800G GOLD")
            col_c, col_d = st.columns(2)
            p_opening = col_c.number_input("Opening balance", min_value=0, value=0, step=1)
            p_minimum = col_d.number_input("Minimum stock", min_value=0, value=10, step=1)
            p_receipts = st.number_input("Total receipts (optional)", min_value=0, value=0, step=1)
            p_submitted = st.form_submit_button("➕ Add product", type="primary", width='stretch')

        if p_submitted:
            try:
                new_row = backend.add_product(
                    p_name, opening=int(p_opening), minimum=int(p_minimum), receipts=int(p_receipts)
                )
                st.success(
                    f"Added **{new_row.product}** as `{new_row.sku}` · "
                    f"current stock {new_row.current} · status {new_row.reorder}"
                )
                st.rerun()
            except EngineError as exc:
                st.error(str(exc))

    with right:
        section("SKU housekeeping", "auto-SKUs for any row still missing one")
        missing = [r for r in stock_rows if not r.sku]
        st.write(
            f"**{len(stock_rows) - len(missing)}** of **{len(stock_rows)}** line(s) have an auto-SKU."
        )
        if missing:
            st.write("Missing: " + ", ".join(r.product for r in missing[:8]))
            if st.button("Backfill SKUs now", width='stretch'):
                try:
                    filled = backend.ensure_skus()
                    st.success(f"Filled {filled} SKU(s).")
                    st.rerun()
                except EngineError as exc:
                    st.error(str(exc))
        else:
            st.caption("Nothing to do — every line already has an SKU.")

        st.divider()
        st.caption(
            "SKUs are for the sheet's benefit only. Staff never see or type one — "
            "the checkout dropdown is populated from the Product name column."
        )


# --------------------------------------------------------------------------- #
# Footer
# --------------------------------------------------------------------------- #

st.divider()
st.caption(
    f"OK3D Stock App prototype · **{backend.label}** · "
    + ("demo data only — nothing is written anywhere." if backend.is_demo
       else f"writing to `{backend.spreadsheet_title}`")
    + f" · session started {_NOW:%Y-%m-%d %H:%M}"
)
