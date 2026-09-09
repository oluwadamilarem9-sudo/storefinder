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
import requests
import streamlit as st

from config import (
    ENABLE_SECONDARY_SOURCES,
    KEEP_UNKNOWN_COUNTRY,
    MAX_DOMAINS_PER_CYCLE,
    MIN_FRESHNESS_LEVEL,
    OUTPUT_FILE,
    REJECTED_FILE,
    TARGET_COUNTRIES,
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
from utils.countries import COUNTRY_CHOICES, country_label


def _load_run_cycle():
    """Reload discovery modules so Streamlit does not keep a stale empty engine."""
    import importlib

    import contact.public_contact_finder
    import database.database
    import discovery.candidate_manager
    import discovery.discovery_sources
    import discovery.domain_discovery
    import pipeline
    import utils.countries

    importlib.reload(database.database)
    importlib.reload(utils.countries)
    importlib.reload(contact.public_contact_finder)
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


BACKEND_URL = "http://127.0.0.1:8001"


def _backend_request(path: str, *, method: str = "GET", payload: dict | None = None, token: str | None = None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
    try:
        response = requests.request(
            method.upper(),
            f"{BACKEND_URL}{path}",
            json=payload,
            headers=headers,
            timeout=20,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Backend unavailable: {exc}") from exc

    if response.status_code >= 400:
        detail = response.text
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            pass
        raise RuntimeError(f"Backend error {response.status_code}: {detail}")

    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}


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
if "auth_token" not in st.session_state:
    st.session_state.auth_token = None
if "backend_user" not in st.session_state:
    st.session_state.backend_user = None
if "selected_project_id" not in st.session_state:
    st.session_state.selected_project_id = None
if "backend_projects" not in st.session_state:
    st.session_state.backend_projects = []
if "backend_run_id" not in st.session_state:
    st.session_state.backend_run_id = None


def _sync_run_results_to_backend(project_id: str, run_id: str, result: dict, *, token: str):
    current_leads = _load_tables()[0]
    lead_domains = set((result.get("this_run_lead_domains") or []))
    if lead_domains:
        lead_rows = current_leads[current_leads["domain"].astype(str).isin(lead_domains)]
        for _, row in lead_rows.iterrows():
            payload = {
                "domain": str(row.get("domain") or ""),
                "store_name": row.get("store_name"),
                "public_email": row.get("public_email"),
                "country": row.get("country"),
                "freshness_level": row.get("freshness_level"),
                "freshness_score": int(row.get("freshness_score") or 0),
                "source": row.get("discovery_source"),
                "run_id": run_id,
            }
            _backend_request(
                f"/projects/{project_id}/leads",
                method="POST",
                payload=payload,
                token=token,
            )

    rejected_domains = set((result.get("this_run_rejected_domains") or []))
    if rejected_domains:
        _, rejected_df = _load_tables()
        rejected_rows = rejected_df[rejected_df["domain"].astype(str).isin(rejected_domains)]
        for _, row in rejected_rows.iterrows():
            payload = {
                "domain": str(row.get("domain") or ""),
                "store_name": row.get("store_name"),
                "public_email": row.get("public_email"),
                "reason": row.get("reject_reason") or "rejected_by_filters",
                "run_id": run_id,
            }
            _backend_request(
                f"/projects/{project_id}/rejected",
                method="POST",
                payload=payload,
                token=token,
            )


st.title("Shopify Public Lead Finder")
st.caption(
    "Automatically discovers candidate Shopify websites from public sources, "
    "scores freshness, and saves publicly displayed business contacts. "
    "You do not paste store URLs."
)

with st.sidebar:
    st.header("Account")
    if not st.session_state.auth_token:
        with st.form("auth_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            login_col, signup_col = st.columns(2)
            login_clicked = login_col.form_submit_button("Login")
            signup_clicked = signup_col.form_submit_button("Sign up")

            if login_clicked and email and password:
                try:
                    user = _backend_request(
                        "/auth/login",
                        method="POST",
                        payload={"email": email, "password": password},
                    )
                    st.session_state.auth_token = user["token"]
                    st.session_state.backend_user = user["user"]
                    st.session_state.backend_projects = []
                    st.session_state.selected_project_id = None
                    st.rerun()
                except RuntimeError as exc:
                    st.error(str(exc))

            if signup_clicked and email and password:
                try:
                    user = _backend_request(
                        "/auth/signup",
                        method="POST",
                        payload={"name": email.split("@")[0], "email": email, "password": password},
                    )
                    st.session_state.auth_token = user["token"]
                    st.session_state.backend_user = user["user"]
                    st.session_state.backend_projects = []
                    st.session_state.selected_project_id = None
                    st.rerun()
                except RuntimeError as exc:
                    st.error(str(exc))
    else:
        user = st.session_state.backend_user or {}
        st.write(f"Signed in as: {user.get('name', 'User')}")
        st.write(user.get('email', ''))
        if st.button("Logout"):
            st.session_state.auth_token = None
            st.session_state.backend_user = None
            st.session_state.selected_project_id = None
            st.session_state.backend_projects = []
            st.rerun()

        with st.form("project_form"):
            project_name = st.text_input("New project name")
            if st.form_submit_button("Create project") and project_name:
                try:
                    project = _backend_request(
                        "/projects",
                        method="POST",
                        payload={"name": project_name},
                        token=st.session_state.auth_token,
                    )
                    st.session_state.selected_project_id = project["id"]
                    st.session_state.backend_projects = []
                    st.rerun()
                except RuntimeError as exc:
                    st.error(str(exc))

        try:
            if not st.session_state.backend_projects:
                st.session_state.backend_projects = _backend_request(
                    "/projects",
                    token=st.session_state.auth_token,
                )
        except RuntimeError as exc:
            st.warning(str(exc))

        if st.session_state.backend_projects:
            project_ids = [project["id"] for project in st.session_state.backend_projects]
            project_names = [project["name"] for project in st.session_state.backend_projects]
            selected_name = st.selectbox(
                "Project",
                options=project_names,
                index=min(
                    project_ids.index(st.session_state.selected_project_id) if st.session_state.selected_project_id in project_ids else 0,
                    len(project_names) - 1,
                ),
            )
            st.session_state.selected_project_id = st.session_state.backend_projects[project_names.index(selected_name)]["id"]
            st.caption(f"Project ID: {st.session_state.selected_project_id}")
        else:
            st.info("Create a project to begin a user-scoped run.")

    st.divider()
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
    all_countries = st.checkbox(
        "All countries",
        value=not bool(TARGET_COUNTRIES),
        help="Discovery is still global. Country is read from public store pages after a site is checked.",
    )
    selected_country_codes: list[str] = []
    if not all_countries:
        selected_country_codes = st.multiselect(
            "Countries to keep",
            options=[code for code, _name in COUNTRY_CHOICES],
            default=list(TARGET_COUNTRIES) or ["NG", "US", "GB", "CA", "AU"],
            format_func=country_label,
            help="A store is kept only when its public page publishes one of these countries.",
        )
        keep_unknown_country = st.checkbox(
            "Keep stores with no published country",
            value=bool(KEEP_UNKNOWN_COUNTRY),
            help="Many Shopify stores never publish a country. Turn this off to reject those.",
        )
    else:
        keep_unknown_country = True
    st.caption(
        "Country is never guessed from Shopify hosting IPs or a myshopify.com name."
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
    if not st.session_state.auth_token or not st.session_state.selected_project_id:
        st.warning("Please sign in and create/select a project before running discovery.")
        st.stop()

    log_box = st.empty()
    logs: list[str] = []

    def on_log(message: str) -> None:
        logs.append(message)
        log_box.code("\n".join(logs[-40:]), language="text")

    project_id = st.session_state.get("selected_project_id")
    backend_run = None
    if project_id and st.session_state.auth_token:
        try:
            backend_run = _backend_request(
                f"/projects/{project_id}/runs",
                method="POST",
                payload={"name": f"Discovery run {len(st.session_state.logs) + 1}", "max_domains": int(max_domains)},
                token=st.session_state.auth_token,
            )
            st.session_state.backend_run_id = backend_run.get("id")
        except RuntimeError as exc:
            st.warning(f"Project run could not be created in the backend: {exc}")
            st.session_state.backend_run_id = None
            st.stop()
    else:
        st.session_state.backend_run_id = None
        st.stop()

    with st.spinner("Searching public sources and checking websites..."):
        run_cycle = _load_run_cycle()
        result = run_cycle(
            on_log=on_log,
            max_domains=max_domains,
            min_freshness=min_freshness,
            enable_secondary=enable_secondary,
            target_countries=[] if all_countries else selected_country_codes,
            keep_unknown_country=keep_unknown_country,
        )
    st.session_state.logs = logs
    st.session_state.last_stats = result
    st.session_state.this_run_lead_domains = list(result.get("this_run_lead_domains") or [])
    st.session_state.this_run_rejected_domains = list(result.get("this_run_rejected_domains") or [])

    if st.session_state.auth_token and project_id and st.session_state.backend_run_id:
        try:
            _sync_run_results_to_backend(
                project_id,
                st.session_state.backend_run_id,
                result,
                token=st.session_state.auth_token,
            )
        except RuntimeError as exc:
            st.warning(f"Project sync warning: {exc}")

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
            "and country filter this run. Open Rejected candidates and the Activity log."
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
Country is copied from public store pages only when the user filters by country.
"""
)
