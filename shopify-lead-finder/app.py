"""
Streamlit web interface for Shopify Public Lead Finder.

This UI runs the existing discovery pipeline. It does not ask you
to paste store URLs.
"""

from __future__ import annotations

from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import importlib.util

import pandas as pd
import requests
import streamlit as st


def _load_app_config():
    """Load this app's config.py, not another module named config."""
    path = ROOT / "config.py"
    spec = importlib.util.spec_from_file_location("leadfinder_config", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load settings from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["leadfinder_config"] = module
    spec.loader.exec_module(module)
    sys.modules["config"] = module
    return module


_config = _load_app_config()
ENABLE_SECONDARY_SOURCES = _config.ENABLE_SECONDARY_SOURCES
MAX_DOMAINS_PER_CYCLE = _config.MAX_DOMAINS_PER_CYCLE
OUTPUT_FILE = _config.OUTPUT_FILE
REJECTED_FILE = _config.REJECTED_FILE
TARGET_COUNTRIES = _config.TARGET_COUNTRIES
project_storage_paths = _config.project_storage_paths
import importlib

import database.database as dbmod

importlib.reload(dbmod)
from database.database import (
    connect,
    delete_leads,
    delete_rejected,
    load_leads_frame,
    load_rejected_frame,
    remember_domains,
)
from utils.countries import COUNTRY_CHOICES, country_label


def _load_run_cycle():
    """Reload discovery modules so Streamlit does not keep a stale empty engine."""
    import importlib

    import contact.public_contact_finder
    import database.database
    import detection.shopify_detector
    import detection.storefront_profile
    import discovery.candidate_manager
    import discovery.discovery_sources
    import discovery.domain_discovery
    import pipeline
    import utils.countries
    import utils.normalization

    importlib.reload(utils.normalization)
    importlib.reload(database.database)
    importlib.reload(utils.countries)
    importlib.reload(contact.public_contact_finder)
    importlib.reload(detection.shopify_detector)
    importlib.reload(detection.storefront_profile)
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


def _project_storage():
    user = st.session_state.get("backend_user") or {}
    project_id = st.session_state.get("selected_project_id")
    user_id = user.get("id")
    if not user_id or not project_id:
        return None
    return project_storage_paths(str(user_id), str(project_id))


def _load_tables(db_path: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    if db_path is None:
        return pd.DataFrame(), pd.DataFrame()
    connection = connect(db_path)
    try:
        return load_leads_frame(connection), load_rejected_frame(connection)
    finally:
        connection.close()


def _merge_account_rows(local: pd.DataFrame, remote_rows: list, rename: dict | None = None) -> pd.DataFrame:
    remote = pd.DataFrame(remote_rows or [])
    if rename and not remote.empty:
        remote = remote.rename(columns=rename)
    if remote.empty:
        return local
    if local.empty or "domain" not in local.columns:
        return remote
    known = set(local["domain"].astype(str))
    extra = remote[~remote["domain"].astype(str).isin(known)]
    if extra.empty:
        return local
    return pd.concat([local, extra], ignore_index=True)


def _load_project_tables(storage: dict, token: str, project_id: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    leads_df, rejected_df = _load_tables(storage["db"])
    try:
        remote_leads = _backend_request(f"/projects/{project_id}/leads", token=token)
        remote_rejected = _backend_request(f"/projects/{project_id}/rejected", token=token)
    except RuntimeError:
        return leads_df, rejected_df
    leads_df = _merge_account_rows(leads_df, remote_leads, {"source": "discovery_source"})
    rejected_df = _merge_account_rows(rejected_df, remote_rejected, {"reason": "reject_reason"})
    return leads_df, rejected_df


def _seed_project_history(storage: dict, token: str, project_id: str) -> None:
    remote_leads = _backend_request(f"/projects/{project_id}/leads", token=token)
    remote_rejected = _backend_request(f"/projects/{project_id}/rejected", token=token)
    domains = [
        item.get("domain")
        for item in list(remote_leads or []) + list(remote_rejected or [])
        if item.get("domain")
    ]
    connection = connect(storage["db"])
    try:
        remember_domains(connection, domains)
    finally:
        connection.close()


def _csv_bytes(path: Path, fallback: pd.DataFrame) -> bytes:
    if path.exists() and path.stat().st_size > 0:
        return path.read_bytes()
    return fallback.to_csv(index=False).encode("utf-8")


def _resolve_backend_url() -> str:
    """Use the live API on Streamlit Cloud. Local runs keep localhost."""
    url = os.environ.get("BACKEND_URL", "").strip()
    if not url:
        try:
            url = str(st.secrets["BACKEND_URL"]).strip()
        except Exception:
            url = ""
    return (url or "http://127.0.0.1:8000").rstrip("/")


BACKEND_URL = _resolve_backend_url()


def _backend_request(path: str, *, method: str = "GET", payload: dict | None = None, token: str | None = None):
    import account_db

    if account_db.using_database():
        return account_db.handle(path, method, payload, token)
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


def _sync_run_results_to_backend(project_id: str, run_id: str, result: dict, *, token: str, db_path: Path):
    current_leads, rejected_df = _load_tables(db_path)
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
    "Scrapes live Shopify shops from public sources. "
    "A shop behind a password page is watched. "
    "Each check uses the shop's own domain, such as the brand's website. "
    "A myshopify.com name is not checked and is not saved. "
    "You do not paste store URLs."
)

with st.sidebar:
    st.header("Account")
    import account_db

    if account_db.using_database():
        url = account_db.database_url()
        if url.startswith("sqlite"):
            st.caption("Account database: local")
        else:
            st.caption("Account database: Supabase")
    else:
        st.caption(f"Account API: {BACKEND_URL}")
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
        help="The run visits this many websites. It does not stop when the first lead is saved.",
    )
    enable_secondary = st.checkbox(
        "Include extra sources (Wayback, Hacker News)",
        value=bool(ENABLE_SECONDARY_SOURCES),
        help="Off by default. These indexes list older mentions. A shop from them still needs a public email, a country, and products.",
    )
    all_countries = st.checkbox(
        "All countries",
        value=not bool(TARGET_COUNTRIES),
        help="A lead still needs a country published on the store. This chooses which published countries to keep.",
    )
    selected_country_codes: list[str] = []
    keep_unknown_country = False
    if not all_countries:
        selected_country_codes = st.multiselect(
            "Countries to keep",
            options=[code for code, _name in COUNTRY_CHOICES],
            default=list(TARGET_COUNTRIES) or ["NG", "US", "GB", "CA", "AU"],
            format_func=country_label,
            help="A store is kept only when its public page publishes one of these countries.",
        )
    st.caption(
        "A saved shop is a live store with public products. "
        "Its email and country are filled in when the store publishes them. "
        "Country is never guessed from Shopify hosting IPs or a myshopify.com name."
    )
    st.divider()
    run_clicked = st.button("Run discovery cycle", type="primary", width="stretch")
    st.caption(
        "Each checked store is opened in public. Fast mode only shortens the contact-page visits."
    )

storage = _project_storage()
if storage is None:
    leads_df, rejected_df = pd.DataFrame(), pd.DataFrame()
else:
    leads_df, rejected_df = _load_project_tables(
        storage,
        st.session_state.auth_token,
        st.session_state.selected_project_id,
    )
leads_csv = storage["leads_csv"] if storage else OUTPUT_FILE
rejected_csv = storage["rejected_csv"] if storage else REJECTED_FILE
stats = st.session_state.last_stats or {}
qualifying_count = 0 if leads_df.empty else len(leads_df)
rejected_count = 0 if rejected_df.empty else len(rejected_df)
email_count = (
    int((leads_df["public_email"].fillna("") != "").sum()) if not leads_df.empty else 0
)

metric_one, metric_two, metric_three, metric_four, metric_five, metric_six = st.columns(6)
metric_one.metric("Last cycle candidates", int(stats.get("candidates") or 0))
metric_two.metric("Last cycle Shopify", int(stats.get("shopify") or 0))
metric_three.metric("Operating shops", int(stats.get("high") or 0))
metric_four.metric("No public products", int(stats.get("low") or 0))
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
    storage = _project_storage()
    if storage is None:
        st.warning("Please sign in and create/select a project before running discovery.")
        st.stop()
    try:
        _seed_project_history(storage, st.session_state.auth_token, project_id)
    except RuntimeError as exc:
        st.warning(f"This project's history could not be loaded: {exc}")
        st.stop()

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
            enable_secondary=enable_secondary,
            target_countries=[] if all_countries else selected_country_codes,
            keep_unknown_country=keep_unknown_country,
            db_path=storage["db"],
            leads_csv=storage["leads_csv"],
            rejected_csv=storage["rejected_csv"],
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
                db_path=storage["db"],
            )
        except RuntimeError as exc:
            st.warning(f"Project sync warning: {exc}")

    saved = len(result.get("this_run_lead_domains") or [])
    checked = int(result.get("checked") or 0)
    requested = int(result.get("requested") or checked)
    count_done = bool(result.get("count_complete"))
    count_line = (
        f"Checked all {requested} websites."
        if count_done
        else f"Checked {checked} of {requested} websites. Public sources ran out."
    )
    complete = bool(result.get("complete")) and saved > 0
    if complete:
        st.session_state.flash_kind = "success"
        st.session_state.flash = (
            f"Scraped {saved} live shop(s). Download them from Newly scraped stores. "
            f"{count_line} Open the Qualifying leads tab."
        )
    elif result.get("checked"):
        st.session_state.flash_kind = "warning"
        st.session_state.flash = (
            "Discovery did not complete. No live shop with a public product catalog was saved. "
            f"{count_line} Open Rejected candidates and the Activity log."
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
        f"Newly scraped stores ({this_run_saved})",
        f"This run rejected ({len(st.session_state.this_run_rejected_domains)})",
    ]
)

with leads_tab:
    st.subheader("Newly scraped stores")
    st.write(
        "Live shops scraped in this run. Download this list. "
        "Email and country appear when the store publishes them. "
        "Another account's scrapes are not included."
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
                "No live shops are saved yet. "
                "A run is complete only after a shop with public products is scraped."
            )
        else:
            st.info(
                "This scrape has no new live shops. "
                "Earlier stores are hidden so they are not mixed into this scrape."
            )
    else:
        email_only = st.checkbox("Show only rows with a public email", value=False)
        if email_only:
            view = view[view["public_email"].fillna("") != ""]
        st.download_button(
            "Download newly scraped stores",
            data=view.to_csv(index=False).encode("utf-8"),
            file_name="newly_scraped_stores.csv",
            mime="text/csv",
            width="stretch",
            type="primary",
        )
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
                "Download all saved shops",
                data=_csv_bytes(leads_csv, leads_df),
                file_name="leads.csv",
                mime="text/csv",
                width="stretch",
            )
        if delete_selected_leads:
            if not selected_leads:
                st.warning("Select at least one qualifying lead first.")
            else:
                connection = connect(storage["db"])
                try:
                    removed = delete_leads(connection, selected_leads)
                finally:
                    connection.close()
                st.session_state.flash_kind = "success"
                st.session_state.flash = f"Deleted {removed} qualifying lead(s)."
                st.rerun()
        if delete_all_leads:
            connection = connect(storage["db"])
            try:
                removed = delete_leads(connection, None)
            finally:
                connection.close()
            st.session_state.flash_kind = "success"
            st.session_state.flash = f"Deleted all {removed} qualifying lead(s)."
            st.rerun()
        st.caption("Saved for this project only.")

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
                data=_csv_bytes(rejected_csv, rejected_df),
                file_name="rejected_candidates.csv",
                mime="text/csv",
                width="stretch",
            )
        if delete_selected:
            if not selected:
                st.warning("Select at least one rejected domain first.")
            else:
                connection = connect(storage["db"])
                try:
                    removed = delete_rejected(connection, selected)
                finally:
                    connection.close()
                st.session_state.flash_kind = "success"
                st.session_state.flash = f"Deleted {removed} rejected candidate(s)."
                st.rerun()
        if delete_all:
            connection = connect(storage["db"])
            try:
                removed = delete_rejected(connection, None)
            finally:
                connection.close()
            st.session_state.flash_kind = "success"
            st.session_state.flash = f"Deleted all {removed} rejected candidate(s)."
            st.rerun()
        st.caption("Saved for this project only.")

st.divider()
st.markdown(
    """
**How this works**

Public sources → open the live shop → keep it when the public catalog has products
→ show it under Newly scraped stores for download.

Email and country are copied from the shop's own pages when they are published.
A password wall or an empty catalog is rejected. Sales and revenue are not estimated.
"""
)
