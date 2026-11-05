import { useCallback, useEffect, useState } from "react";

import { api, errorText } from "../api";
import { outputsLabel, reviewLabel } from "../format";
import type { CircuitIR, OutputsStatus, ProjectSummary, ReviewStatus, SystemInfo } from "../types";
import { Chip } from "./Chip";
import { AgentTab } from "./AgentTab";
import { CircuitTab } from "./CircuitTab";
import { DatasheetsTab } from "./DatasheetsTab";
import { DocumentsTab } from "./DocumentsTab";
import { HistoryTab } from "./HistoryTab";
import { MaterialsTab } from "./MaterialsTab";
import { ProposalsTab } from "./ProposalsTab";
import { RequirementsTab } from "./RequirementsTab";
import { ReviewTab } from "./ReviewTab";
import { SchematicTab } from "./SchematicTab";
import { TemplatesTab } from "./TemplatesTab";

export const TABS = [
  { id: "requirements", label: "요구사항" },
  { id: "templates", label: "템플릿" },
  { id: "proposals", label: "제안" },
  { id: "circuit", label: "회로 (IR)" },
  { id: "schematic", label: "회로도 · 검증" },
  { id: "materials", label: "자재" },
  { id: "datasheets", label: "데이터시트" },
  { id: "review", label: "검토" },
  { id: "agent", label: "Agent" },
  { id: "documents", label: "문서 · BOM" },
  { id: "history", label: "이력" },
] as const;

export type TabId = (typeof TABS)[number]["id"];

export function isTab(value: unknown): value is TabId {
  return TABS.some((t) => t.id === value);
}

interface Props {
  project: ProjectSummary;
  boardId: string;
  onBoard(boardId: string): void;
  system: SystemInfo | null;
  tab: TabId;
  onTab(tab: TabId): void;
  onProjectChanged(): Promise<unknown>;
  onError(message: string | null): void;
}

export interface BoardData {
  circuit: CircuitIR | null;
  revision: number | null;
  outputs: OutputsStatus | null;
  review: ReviewStatus | null;
}

const EMPTY: BoardData = { circuit: null, revision: null, outputs: null, review: null };

export function ProjectView({ project, boardId, onBoard, system, tab, onTab, onProjectChanged, onError }: Props) {
  // Data is stored with the board it belongs to, so a tab never sees the previous board's revision.
  const [loaded, setLoaded] = useState<{ boardId: string; data: BoardData } | null>(null);
  const data = loaded?.boardId === boardId ? loaded.data : EMPTY;
  const [editingName, setEditingName] = useState(false);
  const [focusProposal, setFocusProposal] = useState<string | null>(null);
  const openProposal = (id: string) => {
    setFocusProposal(id);
    onTab("proposals");
  };
  const [name, setName] = useState(project.name);
  const board = project.board_list.find((b) => b.id === boardId) ?? project.board_list[0];

  const reload = useCallback(async () => {
    try {
      const [c, o, r] = await Promise.all([
        api.circuit(project.id, boardId),
        api.outputs(project.id, boardId),
        api.reviews(project.id, boardId).catch(() => null),
      ]);
      setLoaded({ boardId, data: { circuit: c.circuit, revision: c.revision, outputs: o, review: r?.status.board ?? null } });
    } catch (e) {
      onError(errorText(e));
    }
  }, [project.id, boardId, onError]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const afterChange = useCallback(async () => {
    await reload();
    await onProjectChanged();
  }, [reload, onProjectChanged]);

  const rename = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (trimmed && trimmed !== project.name) {
      try {
        await api.renameProject(project.id, trimmed);
        await onProjectChanged();
      } catch (err) {
        onError(errorText(err));
      }
    }
    setEditingName(false);
  };

  const addBoard = async () => {
    const letter = String.fromCharCode(65 + Math.min(project.board_list.length, 25));
    try {
      const created = await api.createBoard(project.id, `보드 ${letter}`);
      await onProjectChanged();
      onBoard(created.id);
    } catch (e) {
      onError(errorText(e));
    }
  };

  const automated = outputsLabel(data.outputs);
  // The engineering review axis comes only from engineers' review records (plan 10.1).
  const review = reviewLabel(data.circuit ? (data.review ?? "unverified") : null);

  return (
    <section className="project">
      <header className="project-header">
        <div className="project-title">
          {editingName ? (
            <form onSubmit={rename} className="rename">
              <input aria-label="프로젝트 이름" autoFocus value={name} onChange={(e) => setName(e.target.value)} maxLength={120} />
              <button type="submit">확인</button>
              <button type="button" className="ghost" onClick={() => setEditingName(false)}>
                취소
              </button>
            </form>
          ) : (
            <h1>
              {project.name}
              <button className="icon" aria-label="이름 바꾸기" title="이름 바꾸기" onClick={() => setEditingName(true)}>
                ✎
              </button>
            </h1>
          )}
          <div className="muted small">
            <code>{project.id}</code> · <span title={project.path} className="truncate">{project.path}</span>
          </div>
        </div>
        <div className="chips" aria-label="상태">
          <Chip label="자동 검사" text={automated.text} tone={automated.tone} />
          <Chip label="엔지니어링 검토" text={review.text} tone={review.tone} />
        </div>
      </header>

      <BoardBar
        project={project}
        boardId={board.id}
        onBoard={onBoard}
        onAdd={addBoard}
        onChanged={onProjectChanged}
        onError={onError}
        revision={data.revision}
      />

      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            className={`tab ${tab === t.id ? "active" : ""}`}
            onClick={() => onTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="tab-body" key={board.id}>
        {tab === "requirements" && (
          <RequirementsTab
            projectId={project.id}
            boardId={board.id}
            boardName={board.name}
            onSaved={onProjectChanged}
            onOpenProposal={openProposal}
          />
        )}
        {tab === "templates" && <TemplatesTab projectId={project.id} boardId={board.id} onOpenProposal={openProposal} />}
        {tab === "proposals" && (
          <ProposalsTab
            projectId={project.id}
            boardId={board.id}
            revision={data.revision}
            circuit={data.circuit}
            focusId={focusProposal}
            onChanged={afterChange}
          />
        )}
        {tab === "circuit" && (
          <CircuitTab
            projectId={project.id}
            boardId={board.id}
            circuit={data.circuit}
            revision={data.revision}
            onSaved={afterChange}
            onOpenProposal={openProposal}
          />
        )}
        {tab === "schematic" && (
          <SchematicTab
            projectId={project.id}
            board={board}
            revision={data.revision}
            outputs={data.outputs}
            system={system}
            fixedParts={Object.keys(data.circuit?.layout ?? {}).length}
            onChanged={afterChange}
            onOpenProposal={openProposal}
          />
        )}
        {tab === "materials" && (
          <MaterialsTab projectId={project.id} boardId={board.id} revision={data.revision} onApplied={afterChange} />
        )}
        {tab === "datasheets" && <DatasheetsTab projectId={project.id} boardId={board.id} revision={data.revision} />}
        {tab === "review" && (
          <ReviewTab projectId={project.id} boardId={board.id} circuit={data.circuit} revision={data.revision} onChanged={afterChange} />
        )}
        {tab === "agent" && (
          <AgentTab projectId={project.id} boardId={board.id} revision={data.revision} onOpenProposal={openProposal} onChanged={afterChange} />
        )}
        {tab === "documents" && <DocumentsTab projectId={project.id} boardId={board.id} revision={data.revision} />}
        {tab === "history" && (
          <HistoryTab projectId={project.id} boardId={board.id} revision={data.revision} onRestored={afterChange} />
        )}
      </div>
    </section>
  );
}

interface BoardBarProps {
  project: ProjectSummary;
  boardId: string;
  revision: number | null;
  onBoard(id: string): void;
  onAdd(): void;
  onChanged(): Promise<unknown>;
  onError(message: string | null): void;
}

function BoardBar({ project, boardId, revision, onBoard, onAdd, onChanged, onError }: BoardBarProps) {
  const [renaming, setRenaming] = useState<string | null>(null);
  const board = project.board_list.find((b) => b.id === boardId)!;

  const rename = async (e: React.FormEvent) => {
    e.preventDefault();
    if (renaming && renaming.trim() && renaming.trim() !== board.name) {
      try {
        await api.renameBoard(project.id, board.id, renaming.trim());
        await onChanged();
      } catch (err) {
        onError(errorText(err));
      }
    }
    setRenaming(null);
  };

  const remove = async () => {
    if (!window.confirm(`'${board.name}' 보드를 삭제할까요?\n보드 폴더는 워크스페이스의 .trash로 옮겨집니다.`)) return;
    try {
      await api.deleteBoard(project.id, board.id);
      const next = project.board_list.find((b) => b.id !== board.id);
      await onChanged();
      if (next) onBoard(next.id);
    } catch (err) {
      onError(errorText(err));
    }
  };

  return (
    <div className="board-bar" aria-label="보드">
      <span className="muted small">보드</span>
      <div className="board-pills" role="tablist">
        {project.board_list.map((b) => (
          <button
            key={b.id}
            role="tab"
            aria-selected={b.id === boardId}
            className={`pill ${b.id === boardId ? "active" : ""}`}
            onClick={() => onBoard(b.id)}
            title={`KiCad 프로젝트: ${b.kicad_project}`}
          >
            {b.name}
            <span className="pill-meta">{b.current_revision ? `rev ${b.current_revision}` : "비어 있음"}</span>
          </button>
        ))}
        <button className="pill ghost" onClick={onAdd} aria-label="보드 추가">
          + 보드
        </button>
      </div>
      {renaming !== null ? (
        <form className="rename" onSubmit={rename}>
          <input aria-label="보드 이름" autoFocus value={renaming} maxLength={80} onChange={(e) => setRenaming(e.target.value)} />
          <button type="submit">확인</button>
          <button type="button" className="ghost" onClick={() => setRenaming(null)}>
            취소
          </button>
        </form>
      ) : (
        <div className="board-actions">
          <span className="muted small">
            {revision ? `revision ${revision}` : "저장된 회로 없음"} · {board.kicad_project}
          </span>
          <button className="ghost small-btn" onClick={() => setRenaming(board.name)}>
            보드 이름 바꾸기
          </button>
          <button className="ghost small-btn" onClick={remove} disabled={project.board_list.length < 2}>
            보드 삭제
          </button>
        </div>
      )}
    </div>
  );
}
