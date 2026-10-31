"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, listProjects, listRuns } from "@/lib/api";
import type { Project } from "@/lib/types";

export default function DashboardPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [openRuns, setOpenRuns] = useState(0);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const items = await listProjects();
        if (cancelled) return;
        setProjects(items);
        const runLists = await Promise.all(
          items.slice(0, 8).map(async (project) => {
            try {
              return await listRuns(project.id);
            } catch {
              return [];
            }
          }),
        );
        if (cancelled) return;
        setOpenRuns(
          runLists.flat().filter((run) => run.status === "queued" || run.status === "running")
            .length,
        );
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load dashboard");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>Dashboard</h1>
          <p>Overview of your projects and discovery activity.</p>
        </div>
        <Link className="btn" href="/projects">
          Open projects
        </Link>
      </div>
      {error ? <div className="error">{error}</div> : null}
      <div className="grid-cards">
        <div className="metric">
          Projects
          <strong>{projects.length}</strong>
        </div>
        <div className="metric">
          Open runs
          <strong>{openRuns}</strong>
        </div>
      </div>
      <section className="panel">
        <h2 style={{ marginTop: 0 }}>Recent projects</h2>
        {projects.length === 0 ? (
          <div className="empty">No projects yet. Create one to start discovery.</div>
        ) : (
          projects.slice(0, 6).map((project) => (
            <div className="list-row" key={project.id}>
              <div>
                <strong>{project.name}</strong>
                <div style={{ color: "var(--muted)", fontSize: "0.9rem" }}>{project.id}</div>
              </div>
              <Link className="btn secondary" href={`/projects/${project.id}`}>
                Open
              </Link>
            </div>
          ))
        )}
      </section>
    </div>
  );
}
