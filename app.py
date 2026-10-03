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
    credentials_path_from_env,
    credentials_summary,
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

  /* hero */
  .ok-hero {
    background: linear-gradient(120deg, #0B3D2E 0%, #12704F 55%, #F2A93B 145%);
    border-radius: 18px; padding: 24px 30px; margin-bottom: 8px;
    box-shadow: 0 12px 30px rgba(0,0,0,.22);
    display: flex; align-items: center; justify-content: space-between; gap: 18px; flex-wrap: wrap;
  }
  .ok-hero h1 { color: #FFFFFF; font-size: 29px; font-weight: 800; margin: 0; letter-spacing: -.4px; }
  .ok-hero p { color: rgba(255,255,255,.82); margin: 6px 0 0; font-size: 14px; }
  .ok-pill {
    background: rgba(255,255,255,.16); color: #EAFBF3; border: 1px solid rgba(255,255,255,.35);
    border-radius: 999px; padding: 8px 18px; font-size: 12px; font-weight: 700;
    letter-spacing: .7px; text-transform: uppercase; white-space: nowrap;
  }

  /* metric cards */
  div[data-testid="stMetric"] {
    background: rgba(128,128,128,.07); border: 1px solid rgba(128,128,128,.20);
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
    padding-bottom: 8px; border-bottom: 2px solid rgba(128,128,128,.20);
  }
  .ok-section span.t { font-size: 17px; font-weight: 750; }
  .ok-section span.s { font-size: 12.5px; opacity: .62; }

  /* blocks */
  .ok-alert {
    background: rgba(214,48,49,.10); border: 1px solid rgba(214,48,49,.45);
    border-left: 5px solid #D63031; border-radius: 10px; padding: 12px 16px;
    margin: 10px 0; font-size: 14px;
  }
  .ok-alert b { color: #D63031; }
  .ok-ok {
    background: rgba(18,112,79,.10); border: 1px solid rgba(18,112,79,.40);
    border-left: 5px solid #12704F; border-radius: 10px; padding: 12px 16px;
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
    background: rgba(128,128,128,.07); border: 1px solid rgba(128,128,128,.20);
    border-radius: 14px; padding: 16px 18px; margin: 10px 0 14px;
  }
  .ok-kv { font-size: 13.5px; margin: 3px 0; }
  .ok-kv b { opacity: .60; font-weight: 600; }

  /* receipt */
  .ok-receipt {
    background: rgba(18,112,79,.08); border: 1px solid rgba(18,112,79,.42);
    border-radius: 14px; padding: 18px 22px; margin: 6px 0 16px;
  }
  .ok-receipt .hdr { font-size: 15px; font-weight: 750; color: #12704F; margin-bottom: 10px;
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
# Session state
# --------------------------------------------------------------------------- #

_NOW = datetime.now()
for _key, _value in {
    "mode": "demo",                      # "demo" | "live"
    "spreadsheet_id": os.environ.get(SPREADSHEET_ID_ENV, "").strip(),
    "staff": "Chidi",
    "receipt": None,
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
    st.markdown("### 🔌 Data source")

    mode_choice = st.radio(
        "Connect to",
        options=["demo", "live"],
        format_func=lambda m: "Demo data (offline)" if m == "demo" else "Shadow Copy Google Sheet",
        index=0 if st.session_state["mode"] == "demo" else 1,
        key="mode_radio",
        help=(
            "Demo data lives in memory and is perfect for training staff. "
            "Shadow Copy reads and writes the isolated test workbook."
        ),
    )
    st.session_state["mode"] = mode_choice

    if mode_choice == "live":
        st.session_state["spreadsheet_id"] = st.text_input(
            "Spreadsheet ID",
            value=st.session_state["spreadsheet_id"],
            placeholder="paste the id from the /d/<id>/edit URL",
            help="The id of the OK3D Shadow Copy workbook -- never the live trading sheet.",
        ).strip()
        st.caption(f"env override: `{SPREADSHEET_ID_ENV}`")

    creds = credentials_summary()
    if mode_choice == "live":
        # A local run reads credentials.json from the project root; a cloud run
        # has no such file and reads the key from Streamlit secrets instead.
        # Saying "credentials.json not found" on the cloud sends people looking
        # for a file that is not supposed to exist there, so name the right
        # remedy for each environment.
        # There is no credentials.json on a cloud deployment by design -- the key
        # arrives through Streamlit secrets. credentials_path_from_env() is
        # already imported, so this needs no extra helper.
        on_cloud = not creds.get("present") and not credentials_path_from_env().exists()
        if creds.get("present"):
            where = "credentials.json" if creds.get("path", "").endswith("credentials.json") else "Streamlit secrets"
            st.success(f"Service account: {creds.get('client_email', 'n/a')}  (via {where})")
        elif on_cloud:
            st.error("No service-account key configured")
            st.caption(
                "This looks like a cloud deployment: there is no credentials.json here "
                "by design. Add the key to **Settings → Secrets** (see "
                "`.streamlit/secrets.toml.example`), or run "
                "`make-secrets-block.ps1` locally to build the block."
            )
        else:
            st.error("credentials.json not found in the project root")
            st.caption(
                "Local run: save the service-account key as `credentials.json` beside app.py, "
                "or set `OK3D_GOOGLE_CREDENTIALS` to its path."
            )

        if not st.session_state.get("spreadsheet_id"):
            st.warning("No spreadsheet id set")
            st.caption(
                "Add `spreadsheet_id = \"<the Shadow Copy id>\"` to **Settings → Secrets**, "
                "or paste it in the box above (that box is not persisted)."
            )

    backend, error_kind, error_message = load_backend(
        st.session_state["mode"], st.session_state["spreadsheet_id"]
    )

    if backend is None:
        # A live attempt failed -- surface it, then keep the app usable.
        titles = {
            "safety": "🛑 Safety boundary blocked this connection",
            "schema": "🧱 Sheet layout does not match the agreed schema",
            "config": "⚙️ Setup incomplete",
            "engine": "⚠️ Engine error",
            "unexpected": "⚠️ Unexpected error",
        }
        st.error(titles.get(error_kind, "Connection failed"))
        with st.expander("Full detail", expanded=error_kind in {"safety", "schema"}):
            st.code(error_message, language=None)
        st.info("Falling back to demo data so the workspace stays usable.")
        backend = DemoBackend()

    st.divider()
    st.markdown("### 👤 Staff")
    st.session_state["staff"] = st.text_input("Handled by", value=st.session_state["staff"])

    st.divider()
    st.markdown("### 🔄 Data")
    if st.button("Refresh from source", use_container_width=True):
        load_backend.clear()
        st.rerun()
    if isinstance(backend, DemoBackend):
        if st.button("Reset demo data", use_container_width=True):
            backend.reset()
            st.session_state["receipt"] = None
            st.rerun()

    st.divider()
    if backend.is_demo:
        st.caption(f"**{backend.label}**")
    else:
        st.caption(f"**{backend.label}**")
        st.caption(f"Workbook: `{backend.spreadsheet_title}`")
    st.caption(f"Last read: {backend.last_read_utc or '—'}")


# --------------------------------------------------------------------------- #
# Hero
# --------------------------------------------------------------------------- #

mode_pill = "Demo data" if backend.is_demo else "Shadow Copy · live"
st.markdown(
    f"""
    <div class="ok-hero">
      <div>
        <h1>📦 OK3D Stock App</h1>
        <p>Visual sales checkout &amp; stock control · no SKUs, no chat syntax · built by LemonLogic</p>
      </div>
      <div class="ok-pill">{mode_pill}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

if backend.is_demo:
    st.markdown(
        '<div class="ok-demo"><b>Demo mode</b> — everything below is in-memory sample data seeded with '
        "OK3D product names. Nothing is written to any spreadsheet. Switch the sidebar to "
        "<i>Shadow Copy Google Sheet</i> once <code>credentials.json</code> is in place.</div>",
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
        else "<span style='color:#12704F;font-weight:700'>OK</span>"
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
                use_container_width=True,
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
            use_container_width=True,
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
        st.dataframe(restock, use_container_width=True, hide_index=True)
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
            use_container_width=True,
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
            r_submitted = st.form_submit_button("📥 Add to stock", type="primary", use_container_width=True)

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
            p_submitted = st.form_submit_button("➕ Add product", type="primary", use_container_width=True)

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
            if st.button("Backfill SKUs now", use_container_width=True):
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
