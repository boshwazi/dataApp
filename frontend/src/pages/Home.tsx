import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type Project, type ProviderSettings } from "../api";

export function Home() {
  const [settings, setSettings] = useState<ProviderSettings | null>(null);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    api.getSettings().then(setSettings).catch(() => setSettings(null));
    api.listProjects().then(setProjects).catch((e) => setError(e.message));
  }, []);

  async function create(event: FormEvent) {
    event.preventDefault();
    try {
      const project = await api.createProject(name);
      navigate(`/projects/${project.id}`);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function remove(project: Project) {
    if (!confirm(`Delete "${project.name}" and all of its data? This cannot be undone.`)) return;
    try {
      await api.deleteProject(project.id);
      setProjects((list) => list?.filter((p) => p.id !== project.id) ?? null);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const connected = settings?.capabilities.tools !== undefined;

  return (
    <div className="page">
      <h1>Projects</h1>
      {settings && !connected && (
        <div className="notice">
          Connect your AI provider first: <Link to="/settings">open Settings</Link> and run the connection test.
        </div>
      )}

      <form className="card inline-form" onSubmit={create}>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="New project name" aria-label="Project name" />
        <button type="submit">Create project</button>
      </form>

      {error && <p className="bad">{error}</p>}
      {projects === null && !error && <p className="muted">Loading…</p>}
      {projects?.length === 0 && <p className="muted">No projects yet. Create one to load your data.</p>}

      <ul className="project-list">
        {projects?.map((project) => (
          <li key={project.id}>
            <Link to={`/projects/${project.id}`} className="project-link">
              <strong>{project.name}</strong>
              <span className="muted">
                {project.sources.length} {project.sources.length === 1 ? "table" : "tables"} · updated{" "}
                {new Date(project.updated_at).toLocaleString()}
              </span>
            </Link>
            <button className="link danger" onClick={() => remove(project)}>
              Delete
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
