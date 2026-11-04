import { useState } from "react";

import { formatTime } from "../format";
import type { ProjectSummary, SystemInfo } from "../types";

interface Props {
  projects: ProjectSummary[];
  selected: string | null;
  system: SystemInfo | null;
  /** False until the initial project list has loaded, so a new project cannot be overwritten by it. */
  ready: boolean;
  onSelect(id: string): void;
  onCreate(name: string): void;
  onDelete(project: ProjectSummary): void;
}

export function Sidebar({ projects, selected, system, ready, onSelect, onCreate, onDelete }: Props) {
  const [name, setName] = useState("");

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    onCreate(trimmed);
    setName("");
  };

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark" aria-hidden>
          ⏚
        </span>
        AI Circuit Studio
      </div>

      <form className="new-project" onSubmit={submit}>
        <input
          aria-label="새 프로젝트 이름"
          placeholder="새 프로젝트 이름"
          value={name}
          maxLength={120}
          onChange={(e) => setName(e.target.value)}
        />
        <button type="submit" disabled={!ready || !name.trim()}>
          새 프로젝트
        </button>
      </form>

      <nav className="project-list" aria-label="프로젝트 목록">
        {projects.length === 0 && <p className="muted small">프로젝트가 없습니다.</p>}
        {projects.map((p) => (
          <div key={p.id} className={`project-item ${p.id === selected ? "active" : ""}`}>
            <button className="project-open" onClick={() => onSelect(p.id)} title={p.path}>
              <span className="project-name">{p.name}</span>
              <span className="project-meta">
                {p.current_revision ? `rev ${p.current_revision}` : "회로 없음"} · {formatTime(p.updated_at)}
              </span>
            </button>
            <button className="icon" aria-label={`${p.name} 삭제`} title="삭제 (휴지통으로 이동)" onClick={() => onDelete(p)}>
              ✕
            </button>
          </div>
        ))}
      </nav>

      <footer className="sidebar-footer small">
        {system ? (
          <>
            <div>v{system.app_version}</div>
            <div title={system.workspace} className="truncate">
              워크스페이스: {system.workspace}
            </div>
            <div>
              KiCad:{" "}
              {system.kicad.available ? (
                <span className="ok-text">{system.kicad.version}</span>
              ) : (
                <span className="warn-text">없음</span>
              )}
            </div>
          </>
        ) : (
          "백엔드 연결 중…"
        )}
      </footer>
    </aside>
  );
}
