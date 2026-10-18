"""Clear account-scoped Streamlit session values without importing the UI module."""

from __future__ import annotations

from typing import MutableMapping


def clear_account_session_state(state: MutableMapping) -> None:
    """Drop every account-scoped UI value so the next user cannot see the last one."""
    state["auth_token"] = None
    state["backend_user"] = None
    state["selected_project_id"] = None
    state["backend_projects"] = []
    state["backend_run_id"] = None
    state["active_run_id"] = None
    state["active_run_project"] = None
    state["this_run_lead_domains"] = []
    state["this_run_rejected_domains"] = []
    state["logs"] = [
        "Ready. Click Run discovery cycle to search public sources automatically."
    ]
    state["last_stats"] = {}
    state["flash"] = ""
    state["flash_kind"] = "info"
    state.pop("history_seed_project", None)
    state.pop("tables_project", None)
    state.pop("cached_leads", None)
    state.pop("cached_rejected", None)
