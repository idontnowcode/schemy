import { useEffect, useState } from "react";

import { api } from "../api";
import { formatTime } from "../format";
import type { ProjectSummary, SystemInfo } from "../types";
import { Icon, type IconName } from "./Icons";

export type View = "projects" | "inventory" | "datasheets" | "guides" | "notes" | "evals" | "settings";

interface Props {
  projects: ProjectSummary[];
  selected: string | null;
  system: SystemInfo | null;
  /** False until the initial project list has loaded, so a new project cannot be overwritten by it. */
  ready: boolean;
  view: View;
  onView(view: View): void;
  onSelect(id: string): void;
  onCreate(name: string): void;
  onDelete(project: ProjectSummary): void;
}

const LIBRARY: { view: View; label: string; icon: IconName }[] = [
  { view: "inventory", label: "보유 자재", icon: "cube" },
  { view: "datasheets", label: "데이터시트", icon: "file" },
  { view: "guides", label: "설계 가이드", icon: "book" },
  { view: "notes", label: "연구노트", icon: "notes" },
  { view: "evals", label: "AI 성능 평가", icon: "bars" },
];

export function Sidebar({ projects, selected, system, ready, view, onView, onSelect, onCreate, onDelete }: Props) {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [stock, setStock] = useState<number | null>(null);

  // The inventory count shown beside 「보유 자재」; refreshed when the user comes back from that page.
  useEffect(() => {
    api
      .inventory()
      .then((r) => setStock(r.inventory ? r.inventory.parts.length : null))
      .catch(() => setStock(null));
  }, [view]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    onCreate(trimmed);
    setName("");
    setCreating(false);
  };

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark">
          <Icon name="chip" size={22} />
        </span>
        AI Circuit Studio
      </div>

      {creating ? (
        <form className="new-project" onSubmit={submit}>
          <input
            aria-label="새 프로젝트 이름"
            placeholder="새 프로젝트 이름"
            autoFocus
            value={name}
            maxLength={120}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && setCreating(false)}
          />
          <div className="new-project-actions">
            <button type="submit" disabled={!ready || !name.trim()}>
              만들기
            </button>
            <button type="button" className="ghost" onClick={() => setCreating(false)}>
              취소
            </button>
          </div>
        </form>
      ) : (
        <div className="new-project">
          <button className="new-project-btn" onClick={() => setCreating(true)} disabled={!ready}>
            <Icon name="plus" /> 새 프로젝트
          </button>
        </div>
      )}

      <div className="side-label">프로젝트</div>
      <nav className="project-list" aria-label="프로젝트 목록">
        {projects.length === 0 && <p className="muted small side-empty">프로젝트가 없습니다.</p>}
        {projects.map((p) => (
          <div key={p.id} className={`project-item ${p.id === selected ? "active" : ""}`}>
            <button className="project-open" onClick={() => onSelect(p.id)}
              title={`보드 ${p.board_list.length}개 · ${formatTime(p.updated_at)}`}>
              <Icon name="board" />
              <span className="project-name">{p.name}</span>
            </button>
            <button className="icon project-delete" aria-label={`${p.name} 삭제`} title="삭제 (휴지통으로 이동)" onClick={() => onDelete(p)}>
              ✕
            </button>
          </div>
        ))}
      </nav>

      <nav className="side-nav" aria-label="자료">
        {LIBRARY.map((x) => (
          <button key={x.view} className={`side-link ${view === x.view ? "active" : ""}`} onClick={() => onView(x.view)}>
            <Icon name={x.icon} />
            <span>{x.label}</span>
            {x.view === "inventory" && stock !== null && <span className="side-count">{stock}개</span>}
          </button>
        ))}
      </nav>
      <nav className="side-nav" aria-label="설정">
        <button className={`side-link ${view === "settings" ? "active" : ""}`} onClick={() => onView("settings")}>
          <Icon name="gear" />
          <span>설정</span>
        </button>
      </nav>

      <footer className="sidebar-footer small">
        {system ? (
          <>
            v{system.app_version} · KiCad{" "}
            {system.kicad.available ? <span className="ok-text">{system.kicad.version}</span> : <span className="warn-text">없음</span>}
          </>
        ) : (
          "백엔드 연결 중…"
        )}
      </footer>
    </aside>
  );
}
