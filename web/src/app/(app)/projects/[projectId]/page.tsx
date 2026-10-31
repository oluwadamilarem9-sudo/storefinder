"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import {
  ApiError,
  createRun,
  downloadCsv,
  getRun,
  listLeads,
  listProjects,
  listRejected,
  listRuns,
} from "@/lib/api";
import type { DiscoveryRun, Lead, Project, Rejected } from "@/lib/types";

type Tab = "runs" | "leads" | "rejected";

export default function ProjectDetailPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = params.projectId;

  const [project, setProject] = useState<Project | null>(null);
  const [tab, setTab] = useState<Tab>("runs");
  const [runs, setRuns] = useState<DiscoveryRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<DiscoveryRun | null>(null);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [rejected, setRejected] = useState<Rejected[]>([]);
  const [query, setQuery] = useState("");
  const [maxDomains, setMaxDomains] = useState(20);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [projects, runRows, leadRows, rejectedRows] = await Promise.all([
      listProjects(),
      listRuns(projectId),
      listLeads(projectId),
      listRejected(projectId),
    ]);
    setProject(projects.find((item) => item.id === projectId) || null);
    setRuns(runRows);
    setLeads(leadRows);
    setRejected(rejectedRows);
    if (runRows[0]) {
      const detail = await getRun(projectId, runRows[0].id);
      setSelectedRun(detail);
    } else {
      setSelectedRun(null);
    }
  }, [projectId]);

  useEffect(() => {
    load().catch((err) => {
      setError(err instanceof ApiError ? err.message : "Could not load project");
    });
  }, [load]);

  useEffect(() => {
    if (!selectedRun || (selectedRun.status !== "queued" && selectedRun.status !== "running")) {
      return;
    }
    const timer = setInterval(() => {
      getRun(projectId, selectedRun.id)
        .then((detail) => {
          setSelectedRun(detail);
          setRuns((current) =>
            current.map((run) => (run.id === detail.id ? { ...run, status: detail.status } : run)),
          );
        })
        .catch(() => undefined);
    }, 4000);
    return () => clearInterval(timer);
  }, [projectId, selectedRun]);

  const filteredLeads = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return leads;
    return leads.filter((row) =>
      [row.domain, row.store_name, row.public_email, row.country, row.source]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(q)),
    );
  }, [leads, query]);

  const filteredRejected = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return rejected;
    return rejected.filter((row) =>
      [row.domain, row.store_name, row.reason, row.public_email]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(q)),
    );
  }, [rejected, query]);

  async function onStartRun(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const run = await createRun(projectId, {
        name: `Discovery ${new Date().toLocaleString()}`,
        max_domains: maxDomains,
        on_page: false,
      });
      await load();
      const detail = await getRun(projectId, run.id);
      setSelectedRun(detail);
      setTab("runs");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start run");
    } finally {
      setBusy(false);
    }
  }

  async function openRun(runId: string) {
    try {
      const detail = await getRun(projectId, runId);
      setSelectedRun(detail);
      setTab("runs");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load run");
    }
  }

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{project?.name || "Project"}</h1>
          <p>Queue discovery runs and review leads for this project.</p>
        </div>
        <Link className="btn secondary" href="/projects">
          All projects
        </Link>
      </div>

      {error ? <div className="error">{error}</div> : null}

      <section className="panel">
        <form className="toolbar" onSubmit={onStartRun}>
          <label>
            Max domains{" "}
            <input
              type="number"
              min={1}
              max={100}
              value={maxDomains}
              onChange={(e) => setMaxDomains(Number(e.target.value) || 20)}
              style={{ width: 100, marginLeft: 8 }}
            />
          </label>
          <button className="btn" type="submit" disabled={busy}>
            {busy ? "Queuing…" : "Queue discovery run"}
          </button>
        </form>
        <p style={{ color: "var(--muted)", margin: 0 }}>
          Runs are queued for a worker (`python worker.py` with the same local DATABASE_URL).
        </p>
      </section>

      <div className="toolbar">
        <button
          type="button"
          className={tab === "runs" ? "btn" : "btn secondary"}
          onClick={() => setTab("runs")}
        >
          Runs
        </button>
        <button
          type="button"
          className={tab === "leads" ? "btn" : "btn secondary"}
          onClick={() => setTab("leads")}
        >
          Leads ({leads.length})
        </button>
        <button
          type="button"
          className={tab === "rejected" ? "btn" : "btn secondary"}
          onClick={() => setTab("rejected")}
        >
          Rejected ({rejected.length})
        </button>
      </div>

      {tab === "runs" ? (
        <section className="panel stack">
          <div>
            <h2 style={{ marginTop: 0 }}>Discovery runs</h2>
            {runs.length === 0 ? (
              <div className="empty">No runs yet.</div>
            ) : (
              runs.map((run) => (
                <div className="list-row" key={run.id}>
                  <div>
                    <strong>{run.name || run.id}</strong>{" "}
                    <span className="badge">{run.status}</span>
                  </div>
                  <button type="button" className="btn secondary" onClick={() => openRun(run.id)}>
                    View
                  </button>
                </div>
              ))
            )}
          </div>
          {selectedRun ? (
            <div>
              <h3 style={{ marginBottom: 8 }}>
                {selectedRun.name || selectedRun.id}{" "}
                <span className="badge">{selectedRun.status}</span>
              </h3>
              {selectedRun.summary ? <p>{selectedRun.summary}</p> : null}
              {selectedRun.error ? <div className="error">{selectedRun.error}</div> : null}
              <div className="log-box">{selectedRun.log || "No activity log yet."}</div>
            </div>
          ) : null}
        </section>
      ) : null}

      {tab === "leads" ? (
        <section className="panel">
          <div className="toolbar">
            <input
              placeholder="Search leads"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <button
              type="button"
              className="btn secondary"
              onClick={() =>
                downloadCsv(
                  `${project?.name || "project"}-leads.csv`,
                  filteredLeads as unknown as Record<string, unknown>[],
                  [
                    "domain",
                    "store_name",
                    "public_email",
                    "country",
                    "freshness_level",
                    "freshness_score",
                    "source",
                  ],
                )
              }
              disabled={filteredLeads.length === 0}
            >
              Export CSV
            </button>
          </div>
          <div className="table-wrap">
            {filteredLeads.length === 0 ? (
              <div className="empty">No leads match this view.</div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Domain</th>
                    <th>Store</th>
                    <th>Email</th>
                    <th>Country</th>
                    <th>Freshness</th>
                    <th>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredLeads.map((lead) => (
                    <tr key={lead.id}>
                      <td>{lead.domain}</td>
                      <td>{lead.store_name || "—"}</td>
                      <td>{lead.public_email || "—"}</td>
                      <td>{lead.country || "—"}</td>
                      <td>{lead.freshness_level || "—"}</td>
                      <td>{lead.source || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
      ) : null}

      {tab === "rejected" ? (
        <section className="panel">
          <div className="toolbar">
            <input
              placeholder="Search rejected"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <button
              type="button"
              className="btn secondary"
              onClick={() =>
                downloadCsv(
                  `${project?.name || "project"}-rejected.csv`,
                  filteredRejected as unknown as Record<string, unknown>[],
                  ["domain", "store_name", "public_email", "reason"],
                )
              }
              disabled={filteredRejected.length === 0}
            >
              Export CSV
            </button>
          </div>
          <div className="table-wrap">
            {filteredRejected.length === 0 ? (
              <div className="empty">No rejected candidates match this view.</div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Domain</th>
                    <th>Store</th>
                    <th>Email</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRejected.map((row) => (
                    <tr key={row.id}>
                      <td>{row.domain}</td>
                      <td>{row.store_name || "—"}</td>
                      <td>{row.public_email || "—"}</td>
                      <td>{row.reason || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
      ) : null}
    </div>
  );
}
