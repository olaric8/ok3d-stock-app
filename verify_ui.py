"""
verify_ui.py
============

Executes ``app.py`` end to end against the **real** Streamlit library, with only
the render calls intercepted. That combination catches the two classes of bug a
plain "HTTP 200" check cannot:

* Streamlit API misuse (a missing or renamed ``st.*`` call raises immediately),
* runtime errors on the demo path -- sale commit, receipt commit, product
  creation, styler, dataframes -- because every code path still executes.

Widget-call interception means no state machine is involved, so click handlers
are invoked explicitly at the end.

    .\\.venv\\Scripts\\python.exe verify_ui.py
"""

from __future__ import annotations

import os
import runpy
import sys
import traceback
from pathlib import Path
from typing import Any

import streamlit as real_st

RESULTS: list[tuple[str, bool, str]] = []
CALLS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(condition), detail))
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not condition else ""))


class _Ctx:
    """
    Context manager standing in for a Streamlit container (column, tab, form).

    The same rendering calls are reachable through a container as through the
    module (``col.metric`` vs ``st.metric``), so every one of them is supported
    here. ``__getattr__`` deliberately still raises, so a typo'd widget name in
    app.py surfaces as a failure rather than silently doing nothing.
    """

    _DELEGATED = (
        "metric", "markdown", "caption", "info", "warning", "error", "success",
        "toast", "divider", "write", "code", "dataframe", "data_editor",
        "download_button", "button", "text_input", "number_input", "checkbox",
        "selectbox", "multiselect", "radio", "date_input", "form_submit_button",
        "subheader", "text_area", "slider", "toggle", "container", "expander",
        "columns", "tabs", "form", "empty", "latex", "json", "table", "line_chart",
        "bar_chart", "area_chart", "altair_chart", "plotly_chart", "image", "progress",
        "spinner", "status", "rerun", "stop", "link_button", "page_link",
    )

    def __init__(self, name: str) -> None:
        self._name = name

    def __enter__(self) -> "_Ctx":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def __getattr__(self, item: str) -> Any:
        if item in _Ctx._DELEGATED:
            def _delegate(*args: Any, **kwargs: Any) -> Any:
                CALLS.append(f"{self._name}.{item}")
                if item == "columns":
                    count = args[0] if args and isinstance(args[0], int) else len(args[0])
                    return [_Ctx(f"{self._name}.col{i}") for i in range(count)]
                if item == "tabs":
                    return [_Ctx(f"{self._name}.tab{i}") for i in range(len(args[0]))]
                if item in {"container", "expander", "form", "spinner", "status", "empty"}:
                    return _Ctx(f"{self._name}.{item}")
                if item == "selectbox":
                    opts = list(kwargs.get("options") or (args[1] if len(args) > 1 else []) or [])
                    return opts[0] if opts else None
                if item == "radio":
                    opts = list(kwargs.get("options") or (args[1] if len(args) > 1 else []) or [])
                    index = int(kwargs.get("index", 0) or 0)
                    return opts[index] if opts else None
                if item == "multiselect":
                    return list(kwargs.get("default", []) or [])
                if item in {"number_input"}:
                    return kwargs.get("value", 0)
                if item in {"text_input", "text_area"}:
                    return kwargs.get("value", "")
                if item in {"checkbox", "toggle"}:
                    return bool(kwargs.get("value", False))
                if item in {"button", "form_submit_button", "download_button"}:
                    return False
                if item == "dataframe" and args and hasattr(args[0], "to_html"):
                    args[0].to_html()  # force pandas to materialise stylers/dtypes
                return None

            return _delegate
        raise AttributeError(f"{self._name}.{item}")


class _Stub(real_st.__class__ if isinstance(real_st, type) else type(real_st)):
    pass


def build_stub() -> Any:
    """
    Wrap the real streamlit module: delegate every attribute to it, but replace
    the rendering functions with recorders.
    """
    stub = _Stub("streamlit")
    for attr in dir(real_st):
        if not attr.startswith("__"):
            try:
                setattr(stub, attr, getattr(real_st, attr))
            except Exception:  # noqa: BLE001 - some attrs are read-only
                pass

    def record(name: str):
        def _fn(*args: Any, **kwargs: Any) -> Any:
            CALLS.append(name)
            return None

        return _fn

    def columns(spec: Any, **kwargs: Any) -> list[_Ctx]:
        CALLS.append("columns")
        count = spec if isinstance(spec, int) else len(spec)
        return [_Ctx(f"col{i}") for i in range(count)]

    def tabs(labels: Any) -> list[_Ctx]:
        CALLS.append("tabs")
        return [_Ctx(f"tab{i}") for i in range(len(labels))]

    def container(*args: Any, **kwargs: Any) -> _Ctx:
        CALLS.append("container")
        return _Ctx("container")

    def selectbox(label: str = "", options: Any = None, **kwargs: Any) -> Any:
        CALLS.append("selectbox")
        opts = list(options or [])
        return opts[0] if opts else None

    def multiselect(label: str = "", options: Any = None, **kwargs: Any) -> list[Any]:
        CALLS.append("multiselect")
        return list(kwargs.get("default", []) or [])

    def number_input(label: str = "", **kwargs: Any) -> Any:
        CALLS.append("number_input")
        return kwargs.get("value", 0)

    def text_input(label: str = "", **kwargs: Any) -> str:
        CALLS.append("text_input")
        return kwargs.get("value", "")

    def checkbox(label: str = "", **kwargs: Any) -> bool:
        CALLS.append("checkbox")
        return bool(kwargs.get("value", False))

    def radio(label: str = "", options: Any = None, **kwargs: Any) -> Any:
        CALLS.append("radio")
        opts = list(options or [])
        index = int(kwargs.get("index", 0) or 0)
        return opts[index] if opts else None

    def button(*args: Any, **kwargs: Any) -> bool:
        CALLS.append("button")
        return False

    def form_submit_button(*args: Any, **kwargs: Any) -> bool:
        CALLS.append("form_submit_button")
        return False

    def dataframe(data: Any = None, **kwargs: Any) -> None:
        CALLS.append("dataframe")
        # Force pandas to materialise the frame, so styler/dtype bugs surface.
        if data is not None and hasattr(data, "to_html"):
            data.to_html()

    def subheader(*args: Any, **kwargs: Any) -> None:
        CALLS.append("subheader")

    stub.columns = columns  # type: ignore[attr-defined]
    stub.tabs = tabs  # type: ignore[attr-defined]
    stub.expander = container  # type: ignore[attr-defined]
    stub.form = container  # type: ignore[attr-defined]
    stub.container = container  # type: ignore[attr-defined]
    stub.selectbox = selectbox  # type: ignore[attr-defined]
    stub.multiselect = multiselect  # type: ignore[attr-defined]
    stub.number_input = number_input  # type: ignore[attr-defined]
    stub.text_input = text_input  # type: ignore[attr-defined]
    stub.checkbox = checkbox  # type: ignore[attr-defined]
    stub.radio = radio  # type: ignore[attr-defined]
    stub.button = button  # type: ignore[attr-defined]
    stub.form_submit_button = form_submit_button  # type: ignore[attr-defined]
    stub.dataframe = dataframe  # type: ignore[attr-defined]
    stub.data_editor = dataframe  # type: ignore[attr-defined]
    for name in ("markdown", "metric", "caption", "info", "warning", "error", "success",
                 "toast", "divider", "write", "code", "download_button", "rerun", "stop",
                 "set_page_config", "subheader"):
        setattr(stub, name, record(name))
    return stub


def main() -> int:
    here = Path(__file__).resolve().parent
    app_source = (here / "app.py").read_text(encoding="utf-8")
    here = Path(__file__).resolve().parent
    app_path = here / "app.py"

    print("=" * 72)
    print("OK3D Stock App -- UI execution check (real Streamlit, intercepted render)")
    print("=" * 72)
    print(f"\nExecuting {app_path.name} ...")

    stub = build_stub()
    saved = sys.modules.get("streamlit")
    sys.modules["streamlit"] = stub
    try:
        runpy.run_path(str(app_path), run_name="__main__")
    except Exception:
        traceback.print_exc()
        print("\n  [FAIL] app.py raised while rendering")
        return 1
    finally:
        if saved is not None:
            sys.modules["streamlit"] = saved
        else:
            sys.modules.pop("streamlit", None)

    print(f"  [PASS] app.py rendered without raising ({len(CALLS)} intercepted calls)")

    print("\n=== widgets actually exercised ===")
    unique = sorted(set(CALLS))
    for name in unique[:26]:
        print(f"  - {name}")
    if len(unique) > 26:
        print(f"  ... and {len(unique) - 26} more")

    # Containers qualify: `col1.metric(...)` counts as exercising `metric`.
    families = {name.split(".")[-1] for name in unique}
    required = {"tabs", "columns", "selectbox", "number_input", "text_input", "dataframe",
                "metric", "markdown", "download_button", "radio", "form_submit_button"}
    missing = required - families
    check("all key widget families used", not missing, f"missing {sorted(missing)}")

    # ---- batch UI wiring --------------------------------------------------- #
    print("\n=== batch sale UI ===")
    check("a Batch sale tab is defined",
          "Batch sale (multiple products)" in app_source)
    check("the basket confirm control exists",
          "Confirm batch sale" in app_source)
    check("the basket clear control exists",
          "Clear basket" in app_source)
    check("the confirm path calls record_sale_batch",
          "backend.record_sale_batch(" in app_source)
    # A selector inside st.form never triggers a rerun, so a submit button
    # disabled on its value can never be enabled. Assert the ordering.
    _batch = app_source[app_source.index("with sub_batch:"):]
    _batch = _batch[: _batch.index("# ------------------------------ dashboard")]
    _sel = _batch.find("basket_product = st.selectbox")
    _form = _batch.find('with st.form("basket-add-form"')
    check("basket selector sits outside the form",
          -1 < _sel < _form,
          f"selector at {_sel}, form at {_form}")
    check("basket quantity sits outside the form",
          -1 < _batch.find("basket_qty = st.number_input") < _form)
    check("basket submit button is not conditionally disabled",
          "disabled=basket_product is None" not in _batch)
    check("no dead total_value metric in the basket",
          "total_value" not in _batch)

    check("no deprecated use_container_width remains",
          "use_container_width" not in app_source,
          f"{app_source.count('use_container_width')} occurrence(s)")
    check("Streamlit's toolbar is hidden",
          'data-testid="stToolbar"' in app_source)

    # ---- CLOUD PATH REGRESSION --------------------------------------------- #
    # The original NameError shipped because this branch was never executed here:
    # it only runs when LIVE mode is selected AND no credentials file exists --
    # exactly the Streamlit Cloud environment. Never let that go untested again.
    print("\n=== cloud-path regression: live mode, no credentials file ===")
    import shutil as _shutil
    from streamlit.testing.v1 import AppTest as _AppTest

    cloud_root = here / "_cloudcheck"
    _shutil.rmtree(cloud_root, ignore_errors=True)
    (cloud_root / ".streamlit").mkdir(parents=True, exist_ok=True)
    for _name in ("app.py", "sheets_engine.py", "demo_backend.py"):
        _shutil.copy(here / _name, cloud_root / _name)
    _shutil.copy(here / ".streamlit" / "config.toml", cloud_root / ".streamlit" / "config.toml")

    import sheets_engine as _se

    _saved_creds_file = _se.CREDENTIALS_FILE
    _saved_env_id = os.environ.pop("OK3D_SPREADSHEET_ID", None)
    _saved_env_creds = os.environ.pop("OK3D_GOOGLE_CREDENTIALS", None)
    _saved_secrets_reader = _se._st_secrets
    _se.CREDENTIALS_FILE = cloud_root / "credentials.json"   # deliberately absent
    # Also neutralise st.secrets. Without this the test still reads the project's
    # real .streamlit/secrets.toml, finds a key, and stops simulating the cloud --
    # which is exactly how it silently passed for the wrong reason.
    _se._st_secrets = lambda: {}

    try:
        at_cloud = _AppTest.from_file(str(cloud_root / "app.py"), default_timeout=180).run()
        radios = [r for r in at_cloud.sidebar.radio]
        check("sidebar 'Connect to' radio rendered", len(radios) == 1, f"found {len(radios)}")
        if radios:
            radios[0].set_value("live").run()

        exceptions = [str(e.value)[:200] for e in at_cloud.exception]
        check("live mode with no credentials raises nothing", not exceptions, "; ".join(exceptions))

        error_text = " ".join(e.value for e in at_cloud.error)
        check("reports the missing key instead of crashing",
              "No service-account key configured" in error_text or "Setup incomplete" in error_text,
              error_text[:160])

        caption_text = " ".join(c.value for c in at_cloud.sidebar.caption)
        check("points at Streamlit secrets for cloud deployments",
              "Settings" in caption_text and "Secrets" in caption_text, caption_text[:200])

        check("still falls back to demo data so the UI stays usable",
              any("Demo data" in c.value for c in at_cloud.sidebar.caption))
    finally:
        _se.CREDENTIALS_FILE = _saved_creds_file
        _se._st_secrets = _saved_secrets_reader
        if _saved_env_id is not None:
            os.environ["OK3D_SPREADSHEET_ID"] = _saved_env_id
        if _saved_env_creds is not None:
            os.environ["OK3D_GOOGLE_CREDENTIALS"] = _saved_env_creds
        _shutil.rmtree(cloud_root, ignore_errors=True)

    # ---- now drive the write paths directly through the demo backend ------- #
    print("\n=== write paths (the code the buttons invoke) ===")
    import sheets_engine as se
    from demo_backend import DemoBackend

    backend = DemoBackend()
    before = len(backend.fetch_ledger())

    sale = backend.record_sale("VIVA 800G", 2, customer="UI Check", handled_by="QA")
    check("checkout commits a sale", sale.ok and len(backend.fetch_ledger()) == before + 1,
          f"ledger {before} -> {len(backend.fetch_ledger())}")
    check("grid data reflects the sale", backend.get_stock("VIVA 800G").current == sale.stock_after)

    # Receiving exactly the minimum leaves the line *at* minimum, which the engine
    # treats as REORDER (verified in verify_engine.py), so receive comfortably over it.
    receipt = backend.record_receipt("RIM BLOCK 140G", 20, handled_by="QA")
    check("stock-in commits a receipt", receipt.ok and receipt.reorder == se.OK_FLAG,
          f"{receipt.stock_before} -> {receipt.stock_after}, {receipt.reorder}")
    check("receipt raises stock by the full quantity",
          receipt.stock_after == receipt.stock_before + 20,
          f"{receipt.stock_before} -> {receipt.stock_after}")

    new = backend.add_product("VIVA 5L", opening=6, minimum=2)
    check("add product assigns an SKU", bool(new.sku), new.sku)

    try:
        backend.record_sale("VIVA 800G", 10_000_000)
        check("over-checkout still blocked", False, "it was allowed!")
    except se.StockError:
        check("over-checkout still blocked", True)

    # The dashboard passes a plain DataFrame to st.dataframe -- no pandas Styler.
    # Assert the source has no styling left, so it cannot creep back in.
        check("app.py has no .style accessor", ".style" not in app_source)
    check("app.py has no style_stock function", "style_stock" not in app_source)
    check("app.py has no LOW_ROW_CSS constant", "LOW_ROW_CSS" not in app_source)
    check("app.py still imports pandas for real dataframe work", "import pandas as pd" in app_source)

    failed = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 72)
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("UI EXECUTION CHECK PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
