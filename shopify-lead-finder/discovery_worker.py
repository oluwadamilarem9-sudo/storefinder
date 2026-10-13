"""
Run queued discovery cycles.

The website only records a cycle. These workers perform it, so several
cycles can run at the same time. Each extra machine that starts
worker.py adds another set of slots.
"""

from __future__ import annotations

import threading
import time

import config
import account_db
from database.database import connect, load_leads_frame, load_rejected_frame, remember_domains, upsert_rejected

_STARTED = False
_START_LOCK = threading.Lock()


def start_worker_pool() -> None:
    """Start this machine's cycle slots once per process."""
    global _STARTED
    with _START_LOCK:
        if _STARTED:
            return
        slots = max(1, int(config.WORKER_SLOTS))
        for index in range(slots):
            threading.Thread(target=worker_loop, name=f"discovery-{index}", daemon=True).start()
        _STARTED = True
        print(f"Discovery workers started: {slots}")


def worker_loop() -> None:
    while True:
        job = account_db.claim_next_run()
        if job is None:
            time.sleep(1)
            continue
        try:
            execute_run(job)
        except Exception as exc:
            account_db.finish_run(
                job["id"],
                status="failed",
                summary="Discovery did not complete. The cycle stopped with an error. Open the Activity log.",
                result={"saved": 0, "complete": False, "lead_domains": [], "rejected_domains": [], "stats": {}},
                error=str(exc),
            )


def execute_run(job: dict) -> None:
    from pipeline import run_cycle

    settings = job.get("settings") or {}
    paths = config.project_storage_paths(str(job["user_id"]), str(job["project_id"]))
    _seed_history(paths["db"], str(job["project_id"]))
    lines: list[str] = []
    last_flush = time.monotonic()

    def on_log(message: str) -> None:
        nonlocal last_flush
        lines.append(message)
        now = time.monotonic()
        if now - last_flush >= 2:
            account_db.save_run_log(job["id"], "\n".join(lines[-80:]))
            last_flush = now

    countries = settings.get("target_countries")
    if not isinstance(countries, list):
        countries = None
    result = run_cycle(
        on_log=on_log,
        max_domains=int(job.get("max_domains") or settings.get("max_domains") or 20),
        enable_secondary=bool(settings.get("enable_secondary")),
        target_countries=countries,
        keep_unknown_country=bool(settings.get("keep_unknown_country")),
        db_path=paths["db"],
        leads_csv=paths["leads_csv"],
        rejected_csv=paths["rejected_csv"],
    )
    account_db.save_run_log(job["id"], "\n".join(lines[-80:]))
    lead_domains = list(result.get("this_run_lead_domains") or [])
    rejected_domains = list(result.get("this_run_rejected_domains") or [])
    _store_rows(paths["db"], job, lead_domains, rejected_domains)
    saved = len(lead_domains)
    checked = int(result.get("checked") or 0)
    requested = int(result.get("requested") or checked)
    count_line = (
        f"Checked all {requested} websites."
        if result.get("count_complete")
        else f"Checked {checked} of {requested} websites. Public sources ran out."
    )
    complete = bool(result.get("complete")) and saved > 0
    if complete:
        summary = (
            f"Scraped {saved} live shop(s). Download them from Newly scraped stores. "
            f"{count_line} Open the Qualifying leads tab."
        )
        status = "complete"
    elif checked:
        summary = (
            "Discovery did not complete. No live shop with a public product catalog was saved. "
            f"{count_line} Open Rejected candidates and the Activity log."
        )
        status = "complete"
    else:
        summary = (
            "Discovery did not complete. Public sources did not give any unused "
            "website to check this run. Open the Activity log."
        )
        status = "complete"
    stats = {key: value for key, value in result.items() if key not in {"this_run_lead_domains", "this_run_rejected_domains"}}
    account_db.finish_run(
        job["id"],
        status=status,
        summary=summary,
        result={
            "saved": saved,
            "complete": complete,
            "lead_domains": lead_domains,
            "rejected_domains": rejected_domains,
            "stats": stats,
        },
    )


def _seed_history(db_path, project_id: str) -> None:
    leads, rejected = account_db.project_history(project_id)
    connection = connect(db_path)
    try:
        remember_domains(connection, leads)
        watched = []
        finished = []
        for row in rejected:
            if row.get("reason") == "password_page":
                watched.append(row.get("domain") or "")
            else:
                finished.append(row.get("domain") or "")
        remember_domains(connection, finished)
        for domain in watched:
            if not domain:
                continue
            upsert_rejected(
                connection,
                {
                    "domain": domain,
                    "shopify_status": "SHOPIFY",
                    "freshness_evidence": "Watched password page.",
                    "reject_reason": "password_page",
                },
            )
    finally:
        connection.close()


def _clean_cell(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return text


def _store_rows(db_path, job: dict, lead_domains: list[str], rejected_domains: list[str]) -> None:
    connection = connect(db_path)
    try:
        leads_df = load_leads_frame(connection)
        rejected_df = load_rejected_frame(connection)
    finally:
        connection.close()
    lead_set = set(lead_domains)
    rejected_set = set(rejected_domains)
    leads = []
    if lead_set and not leads_df.empty and "domain" in leads_df.columns:
        picked = leads_df[leads_df["domain"].astype(str).isin(lead_set)]
        for _, row in picked.iterrows():
            leads.append(
                {
                    "domain": str(row.get("domain") or ""),
                    "store_name": _clean_cell(row.get("store_name")),
                    "public_email": _clean_cell(row.get("public_email")),
                    "country": _clean_cell(row.get("country")),
                    "freshness_level": _clean_cell(row.get("freshness_level")),
                    "freshness_score": int(row.get("freshness_score") or 0),
                    "source": _clean_cell(row.get("discovery_source")),
                }
            )
    rejected = []
    if rejected_set and not rejected_df.empty and "domain" in rejected_df.columns:
        picked = rejected_df[rejected_df["domain"].astype(str).isin(rejected_set)]
        for _, row in picked.iterrows():
            rejected.append(
                {
                    "domain": str(row.get("domain") or ""),
                    "store_name": _clean_cell(row.get("store_name")),
                    "public_email": _clean_cell(row.get("public_email")),
                    "reason": _clean_cell(row.get("reject_reason")) or "rejected_by_filters",
                }
            )
    account_db.store_run_rows(str(job["project_id"]), str(job["user_id"]), str(job["id"]), leads, rejected)
