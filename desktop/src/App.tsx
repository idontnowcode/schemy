import { useCallback, useEffect, useState } from "react";

import { api, errorText } from "./api";
import { DatasheetLibraryPage } from "./components/DatasheetLibraryPage";
import { GuidesPage } from "./components/Guides";
import { InventoryPage } from "./components/InventoryPage";
import { isTab, ProjectView, type TabId } from "./components/ProjectView";
import { SettingsPage } from "./components/SettingsPage";
import { Sidebar, type View } from "./components/Sidebar";
import type { ProjectSummary, SystemInfo } from "./types";

export function App() {
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [boardId, setBoardId] = useState<string | null>(null);
  const [tab, setTab] = useState<TabId>("requirements");
  const [view, setView] = useState<View>("projects");
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  const refreshProjects = useCallback(async () => {
    const list = await api.projects();
    setProjects(list);
    return list;
  }, []);

  // Restore the last project, board and tab (state survives an app restart).
  // System info can be slow (KiCad detection), so it never blocks the project list.
  useEffect(() => {
    api.system().then(setSystem).catch((e) => setError(errorText(e)));
    (async () => {
      try {
        const [list, settings] = await Promise.all([api.projects(), api.settings()]);
        setProjects(list);
        const project = list.find((p) => p.id === settings.last_project_id);
        if (project) {
          setSelected(project.id);
          setBoardId(settings.last_board_id && project.boards.includes(settings.last_board_id)
            ? settings.last_board_id
            : project.boards[0] ?? null);
          if (isTab(settings.last_tab)) setTab(settings.last_tab);
        }
      } catch (e) {
        setError(errorText(e));
      } finally {
        setReady(true);
      }
    })();
  }, []);

  const persist = (projectId: string | null, board: string | null, nextTab: TabId) =>
    api.saveSettings({ last_project_id: projectId, last_board_id: board, last_tab: nextTab }).catch(() => undefined);

  const select = async (id: string) => {
    setView("projects");
    setSelected(id);
    setError(null);
    try {
      const project = await api.openProject(id);
      const settings = await api.settings();
      const board = settings.last_board_id ?? project.boards[0] ?? null;
      setBoardId(board);
      await persist(id, board, tab);
      await refreshProjects();
    } catch (e) {
      setError(errorText(e));
    }
  };

  const changeTab = (next: TabId) => {
    setTab(next);
    void persist(selected, boardId, next);
  };

  const changeBoard = (next: string) => {
    setBoardId(next);
    void persist(selected, next, tab);
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
      if (selected === project.id) {
        setSelected(null);
        setBoardId(null);
      }
      await refreshProjects();
    } catch (e) {
      setError(errorText(e));
    }
  };

  const current = projects.find((p) => p.id === selected) ?? null;
  const currentBoard = current && boardId && current.boards.includes(boardId) ? boardId : current?.boards[0] ?? null;

  return (
    <div className="app">
      <Sidebar
        projects={projects}
        selected={view === "projects" ? selected : null}
        onSelect={select}
        onCreate={create}
        onDelete={remove}
        system={system}
        ready={ready}
        view={view}
        onView={setView}
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
        ) : view === "inventory" ? (
          <InventoryPage onError={setError} />
        ) : view === "datasheets" ? (
          <DatasheetLibraryPage onError={setError} />
        ) : view === "guides" ? (
          <GuidesPage onError={setError} />
        ) : view === "settings" ? (
          <SettingsPage system={system} onError={setError} />
        ) : current && currentBoard ? (
          <ProjectView
            key={current.id}
            project={current}
            boardId={currentBoard}
            onBoard={changeBoard}
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
