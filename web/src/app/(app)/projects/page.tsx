"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { ApiError, createProject, listProjects } from "@/lib/api";
import type { Project } from "@/lib/types";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function refresh() {
    const items = await listProjects();
    setProjects(items);
  }

  useEffect(() => {
    refresh().catch((err) => {
      setError(err instanceof ApiError ? err.message : "Could not load projects");
    });
  }, []);

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError("");
    try {
      await createProject(name.trim());
      setName("");
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create project");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>Projects</h1>
          <p>Each project keeps its own discovery runs, leads, and rejected candidates.</p>
        </div>
      </div>
      {error ? <div className="error">{error}</div> : null}
      <section className="panel">
        <form className="toolbar" onSubmit={onCreate}>
          <input
            placeholder="New project name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          <button className="btn" type="submit" disabled={busy}>
            {busy ? "Creating…" : "Create project"}
          </button>
        </form>
        {projects.length === 0 ? (
          <div className="empty">No projects yet.</div>
        ) : (
          projects.map((project) => (
            <div className="list-row" key={project.id}>
              <div>
                <strong>{project.name}</strong>
                {project.created_at ? (
                  <div style={{ color: "var(--muted)", fontSize: "0.9rem" }}>
                    Created {new Date(project.created_at).toLocaleString()}
                  </div>
                ) : null}
              </div>
              <Link className="btn secondary" href={`/projects/${project.id}`}>
                Manage
              </Link>
            </div>
          ))
        )}
      </section>
    </div>
  );
}
