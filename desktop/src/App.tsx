import { useCallback, useEffect, useState } from "react";

import { api, errorText } from "./api";
import { isTab, ProjectView, type TabId } from "./components/ProjectView";
import { Sidebar } from "./components/Sidebar";
import type { ProjectSummary, SystemInfo } from "./types";

export function App() {
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<TabId>("requirements");
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  const refreshProjects = useCallback(async () => {
    const list = await api.projects();
    setProjects(list);
    return list;
  }, []);

  // Restore the last project and tab (Phase 1 gate: state survives an app restart).
  // System info can be slow (KiCad detection), so it never blocks the project list.
  useEffect(() => {
    api.system().then(setSystem).catch((e) => setError(errorText(e)));
    (async () => {
      try {
        const [list, settings] = await Promise.all([api.projects(), api.settings()]);
        setProjects(list);
        if (settings.last_project_id && list.some((p) => p.id === settings.last_project_id)) {
          setSelected(settings.last_project_id);
          if (isTab(settings.last_tab)) setTab(settings.last_tab);
        }
      } catch (e) {
        setError(errorText(e));
      } finally {
        setReady(true);
      }
    })();
  }, []);

  const select = async (id: string) => {
    setSelected(id);
    setError(null);
    try {
      await api.openProject(id);
      await api.saveSettings({ last_project_id: id, last_tab: tab });
      await refreshProjects();
    } catch (e) {
      setError(errorText(e));
    }
  };

  const changeTab = (next: TabId) => {
    setTab(next);
    api.saveSettings({ last_project_id: selected, last_tab: next }).catch(() => undefined);
  };

  const create = async (name: string) => {
    try {
      const project = await api.createProject(name);
      await refreshProjects();
      setTab("requirements");
      await select(project.id);
    } catch (e) {
      setError(errorText(e));
    }
  };

  const remove = async (project: ProjectSummary) => {
    if (!window.confirm(`'${project.name}' 프로젝트를 삭제할까요?\n폴더는 워크스페이스의 .trash로 옮겨지며 복구할 수 있습니다.`)) return;
    try {
      await api.deleteProject(project.id);
      if (selected === project.id) setSelected(null);
      await refreshProjects();
    } catch (e) {
      setError(errorText(e));
    }
  };

  const current = projects.find((p) => p.id === selected) ?? null;

  return (
    <div className="app">
      <Sidebar
        projects={projects}
        selected={selected}
        onSelect={select}
        onCreate={create}
        onDelete={remove}
        system={system}
        ready={ready}
      />
      <main className="main">
        {error && (
          <div className="banner banner-bad" role="alert">
            {error}
            <button className="link" onClick={() => setError(null)}>
              닫기
            </button>
          </div>
        )}
        {!ready ? (
          <div className="empty">불러오는 중…</div>
        ) : current ? (
          <ProjectView
            key={current.id}
            project={current}
            system={system}
            tab={tab}
            onTab={changeTab}
            onProjectChanged={refreshProjects}
            onError={setError}
          />
        ) : (
          <div className="empty">
            <h1>AI Circuit Studio</h1>
            <p>왼쪽에서 프로젝트를 고르거나 새 프로젝트를 만드세요.</p>
          </div>
        )}
      </main>
    </div>
  );
}
