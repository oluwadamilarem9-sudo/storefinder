"""
Streamlit web interface for Shopify Public Lead Finder.

This UI runs the existing discovery pipeline. It does not ask you
to paste store URLs.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from config import (
    ENABLE_SECONDARY_SOURCES,
    MAX_DOMAINS_PER_CYCLE,
    MIN_FRESHNESS_LEVEL,
    OUTPUT_FILE,
    REJECTED_FILE,
)
import importlib

import database.database as dbmod

importlib.reload(dbmod)
from database.database import (
    connect,
    delete_leads,
    delete_rejected,
    load_leads_frame,
    load_rejected_frame,
)


def _load_run_cycle():
    """Reload discovery modules so Streamlit does not keep a stale empty engine."""
    import importlib

    import database.database
    import discovery.candidate_manager
    import discovery.discovery_sources
    import discovery.domain_discovery
    import pipeline

    importlib.reload(database.database)
    importlib.reload(discovery.discovery_sources)
    importlib.reload(discovery.domain_discovery)
    importlib.reload(discovery.candidate_manager)
    importlib.reload(pipeline)
    return pipeline.run_cycle

st.set_page_config(
    page_title="Shopify Public Lead Finder",
    page_icon="🛍️",
    layout="wide",
)


def _load_tables() -> tuple[pd.DataFrame, pd.DataFrame]:
    connection = connect()
    try:
        return load_leads_frame(connection), load_rejected_frame(connection)
    finally:
        connection.close()


def _csv_bytes(path: Path, fallback: pd.DataFrame) -> bytes:
    if path.exists() and path.stat().st_size > 0:
        return path.read_bytes()
    return fallback.to_csv(index=False).encode("utf-8")


if "logs" not in st.session_state:
    st.session_state.logs = [
        "Ready. Click Run discovery cycle to search public sources automatically."
    ]
if "last_stats" not in st.session_state:
    st.session_state.last_stats = None
if "flash" not in st.session_state:
    st.session_state.flash = ""
if "flash_kind" not in st.session_state:
    st.session_state.flash_kind = "info"
if "this_run_lead_domains" not in st.session_state:
    st.session_state.this_run_lead_domains = []
if "this_run_rejected_domains" not in st.session_state:
    st.session_state.this_run_rejected_domains = []


st.title("Shopify Public Lead Finder")
st.caption(
    "Automatically discovers candidate Shopify websites from public sources, "
    "scores freshness, and saves publicly displayed business contacts. "
    "You do not paste store URLs."
)

with st.sidebar:
    st.header("Run settings")
    st.write("These change only this run. Defaults still live in `config.py`.")
    max_domains = st.number_input(
        "Max websites to check",
        min_value=5,
        max_value=10000,
        value=min(int(MAX_DOMAINS_PER_CYCLE), 10000),
        step=5,
        help="How many discovered websites to visit this cycle. Public sources may return fewer than this in one run.",
    )
    min_freshness = st.selectbox(
        "Minimum freshness for leads.csv",
        options=["HIGH", "MEDIUM"],
        index=0 if MIN_FRESHNESS_LEVEL == "HIGH" else 1,
    )
    enable_secondary = st.checkbox(
        "Include secondary sources (Hacker News, Wayback, Common Crawl)",
        value=bool(ENABLE_SECONDARY_SOURCES),
        help="Off by default. Secondary sources usually find existing stores, not new launches.",
    )
    st.divider()
    run_clicked = st.button("Run discovery cycle", type="primary", width="stretch")
    st.caption(
        "Fast mode is on: shorter waits, fewer extra pages, and no slow "
        "certificate/RDAP lookups during the cycle."
    )

leads_df, rejected_df = _load_tables()
stats = st.session_state.last_stats or {}
qualifying_count = 0 if leads_df.empty else len(leads_df)
rejected_count = 0 if rejected_df.empty else len(rejected_df)
email_count = (
    int((leads_df["public_email"].fillna("") != "").sum()) if not leads_df.empty else 0
)

metric_one, metric_two, metric_three, metric_four, metric_five, metric_six = st.columns(6)
metric_one.metric("Last cycle candidates", int(stats.get("candidates") or 0))
metric_two.metric("Last cycle Shopify", int(stats.get("shopify") or 0))
metric_three.metric("High freshness", int(stats.get("high") or 0))
metric_four.metric("Medium freshness", int(stats.get("medium") or 0))
this_run_saved = len(st.session_state.this_run_lead_domains)
metric_five.metric("This run qualifying", this_run_saved)
metric_six.metric("This run rejected", len(st.session_state.this_run_rejected_domains))

if run_clicked:
    log_box = st.empty()
    logs: list[str] = []

    def on_log(message: str) -> None:
        logs.append(message)
        log_box.code("\n".join(logs[-40:]), language="text")

    with st.spinner("Searching public sources and checking websites..."):
        run_cycle = _load_run_cycle()
        result = run_cycle(
            on_log=on_log,
            max_domains=max_domains,
            min_freshness=min_freshness,
            enable_secondary=enable_secondary,
        )
    st.session_state.logs = logs
    st.session_state.last_stats = result
    st.session_state.this_run_lead_domains = list(result.get("this_run_lead_domains") or [])
    st.session_state.this_run_rejected_domains = list(result.get("this_run_rejected_domains") or [])
    saved = int(result.get("high") or 0) + int(result.get("medium") or 0)
    complete = bool(result.get("complete")) and saved > 0
    if complete:
        st.session_state.flash_kind = "success"
        st.session_state.flash = (
            f"Discovery found {saved} qualifying HIGH/MEDIUM store(s) this run. "
            "Open the Qualifying leads tab."
        )
    elif result.get("checked"):
        st.session_state.flash_kind = "warning"
        st.session_state.flash = (
            f"Discovery did not complete. No store met the {min_freshness} freshness bar "
            "this run. Open Rejected candidates and the Activity log."
        )
    else:
        st.session_state.flash_kind = "warning"
        st.session_state.flash = (
            "Discovery did not complete. Public sources did not give any unused "
            "website to check this run. Open the Activity log."
        )
    st.rerun()

if st.session_state.flash:
    if st.session_state.flash_kind == "success":
        st.success(st.session_state.flash)
    elif st.session_state.flash_kind == "warning":
        st.warning(st.session_state.flash)
    else:
        st.info(st.session_state.flash)

with st.expander("Last cycle activity log", expanded=True):
    st.code("\n".join(st.session_state.logs), language="text")

leads_tab, rejected_tab = st.tabs(
    [
        f"This run leads ({this_run_saved})",
        f"This run rejected ({len(st.session_state.this_run_rejected_domains)})",
    ]
)

with leads_tab:
    st.subheader("Qualifying leads from this run")
    st.write(
        "Only stores found in the latest run are shown here. "
        "Earlier runs stay in the database but are never checked again. "
        "Emails are copied from public pages only."
    )
    show_earlier_leads = st.checkbox(
        "Also show saved leads from earlier runs",
        value=False,
        key="show_earlier_leads",
    )
    view = leads_df
    if not show_earlier_leads:
        this_run = set(st.session_state.this_run_lead_domains)
        if leads_df.empty or "domain" not in leads_df.columns:
            view = leads_df.iloc[0:0]
        else:
            view = leads_df[leads_df["domain"].astype(str).isin(this_run)]
    if view.empty:
        if show_earlier_leads:
            st.info(
                "No HIGH/MEDIUM leads are saved yet. "
                "A run is complete only after a qualifying store is found."
            )
        else:
            st.info(
                "This run has no new qualifying leads. "
                "Earlier stores are hidden so they are not mixed into this cycle."
            )
    else:
        email_only = st.checkbox("Show only rows with a public email", value=False)
        if email_only:
            view = view[view["public_email"].fillna("") != ""]
        st.dataframe(view, width="stretch", hide_index=True)
        lead_domains = view["domain"].dropna().astype(str).tolist()
        selected_leads = st.multiselect(
            "Select qualifying leads to delete",
            options=lead_domains,
            default=[],
            key="selected_qualifying_leads",
        )
        lead_delete_col, lead_clear_col, lead_download_col = st.columns(3)
        with lead_delete_col:
            delete_selected_leads = st.button(
                "Delete selected",
                width="stretch",
                key="delete_selected_leads",
            )
        with lead_clear_col:
            delete_all_leads = st.button(
                "Delete all qualifying leads",
                width="stretch",
                key="delete_all_leads",
            )
        with lead_download_col:
            st.download_button(
                "Download leads.csv",
                data=_csv_bytes(OUTPUT_FILE, leads_df),
                file_name="leads.csv",
                mime="text/csv",
                width="stretch",
            )
        if delete_selected_leads:
            if not selected_leads:
                st.warning("Select at least one qualifying lead first.")
            else:
                connection = connect()
                try:
                    removed = delete_leads(connection, selected_leads)
                finally:
                    connection.close()
                st.session_state.flash_kind = "success"
                st.session_state.flash = f"Deleted {removed} qualifying lead(s)."
                st.rerun()
        if delete_all_leads:
            connection = connect()
            try:
                removed = delete_leads(connection, None)
            finally:
                connection.close()
            st.session_state.flash_kind = "success"
            st.session_state.flash = f"Deleted all {removed} qualifying lead(s)."
            st.rerun()
        st.caption(f"Also saved on disk at `{OUTPUT_FILE}`")

with rejected_tab:
    st.subheader("Rejected candidates from this run")
    st.write(
        "Only websites from the latest run are shown here. "
        "Earlier rejected stores stay excluded from future cycles. "
        "Discovery date is not a launch date."
    )
    show_earlier_rejected = st.checkbox(
        "Also show rejected rows from earlier runs",
        value=False,
        key="show_earlier_rejected",
    )
    rejected_view = rejected_df
    if not show_earlier_rejected:
        this_run_rejected = set(st.session_state.this_run_rejected_domains)
        if rejected_df.empty or "domain" not in rejected_df.columns:
            rejected_view = rejected_df.iloc[0:0]
        else:
            rejected_view = rejected_df[rejected_df["domain"].astype(str).isin(this_run_rejected)]
    if rejected_view.empty:
        if show_earlier_rejected:
            st.info("No rejected candidates yet.")
        else:
            st.info("This run has no new rejected candidates. Earlier rows are hidden.")
    else:
        st.dataframe(rejected_view, width="stretch", hide_index=True)
        domains = rejected_view["domain"].dropna().astype(str).tolist()
        selected = st.multiselect(
            "Select rejected domains to delete",
            options=domains,
            default=[],
            key="selected_rejected_domains",
        )
        delete_col, clear_col, download_col = st.columns(3)
        with delete_col:
            delete_selected = st.button(
                "Delete selected",
                width="stretch",
                key="delete_selected_rejected",
            )
        with clear_col:
            delete_all = st.button(
                "Delete all rejected",
                width="stretch",
                key="delete_all_rejected",
            )
        with download_col:
            st.download_button(
                "Download rejected_candidates.csv",
                data=_csv_bytes(REJECTED_FILE, rejected_df),
                file_name="rejected_candidates.csv",
                mime="text/csv",
                width="stretch",
            )
        if delete_selected:
            if not selected:
                st.warning("Select at least one rejected domain first.")
            else:
                connection = connect()
                try:
                    removed = delete_rejected(connection, selected)
                finally:
                    connection.close()
                st.session_state.flash_kind = "success"
                st.session_state.flash = f"Deleted {removed} rejected candidate(s)."
                st.rerun()
        if delete_all:
            connection = connect()
            try:
                removed = delete_rejected(connection, None)
            finally:
                connection.close()
            st.session_state.flash_kind = "success"
            st.session_state.flash = f"Deleted all {removed} rejected candidate(s)."
            st.rerun()
        st.caption(f"Also saved on disk at `{REJECTED_FILE}`")

st.divider()
st.markdown(
    """
**How this works**

Public sources → automatic store discovery → Shopify detection → freshness scoring
→ public contact discovery → SQLite + CSV.

Primary sources: recent crt.sh certificates, urlscan.io public scans, and Cert Spotter.
None of them can guarantee a store launched on a specific date.
"""
)
