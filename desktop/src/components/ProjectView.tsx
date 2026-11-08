import { useCallback, useEffect, useState } from "react";

import { api, errorText } from "../api";
import { outputsLabel, reviewLabel } from "../format";
import type { CircuitIR, OutputsStatus, ProjectSummary, ReviewStatus, SystemInfo } from "../types";
import { Chip } from "./Chip";
import { AgentTab } from "./AgentTab";
import { Icon, type IconName } from "./Icons";
import { StageFlow } from "./StageFlow";
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
  { id: "requirements", label: "요구사항", icon: "list" },
  { id: "templates", label: "템플릿", icon: "grid" },
  { id: "proposals", label: "제안", icon: "sparkle" },
  { id: "circuit", label: "회로 데이터", icon: "database" },
  { id: "schematic", label: "회로도 · 검증", icon: "board" },
  { id: "review", label: "검토", icon: "shield" },
  { id: "agent", label: "Agent", icon: "sparkle" },
  { id: "materials", label: "자재", icon: "cube" },
  { id: "datasheets", label: "데이터시트", icon: "file" },
  { id: "documents", label: "문서 · BOM", icon: "notes" },
  { id: "history", label: "이력", icon: "clock" },
] as const satisfies readonly { id: string; label: string; icon: IconName }[];

export type TabId = (typeof TABS)[number]["id"];

export function isTab(value: unknown): value is TabId {
  return TABS.some((t) => t.id === value);
}

/** The advanced screen groups the eleven tabs in four: design, verification, references, records. */
export const GROUPS: { id: string; label: string; icon: IconName; tabs: TabId[] }[] = [
  { id: "design", label: "설계", icon: "chip", tabs: ["requirements", "templates", "proposals", "circuit"] },
  { id: "verify", label: "검증", icon: "shield", tabs: ["schematic", "review", "agent"] },
  { id: "library", label: "자료", icon: "file", tabs: ["materials", "datasheets", "documents"] },
  { id: "records", label: "기록", icon: "clock", tabs: ["history"] },
];

const tabLabel = (id: TabId) => TABS.find((t) => t.id === id)!.label;

/** Which screen a project opens in: the seven-step flow (default) or the advanced tabs. Kept in app settings. */
export type ProjectMode = "flow" | "advanced";

interface Props {
  project: ProjectSummary;
  boardId: string;
  onBoard(boardId: string): void;
  system: SystemInfo | null;
  tab: TabId;
  onTab(tab: TabId): void;
  mode: ProjectMode;
  onMode(mode: ProjectMode): void;
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

export function ProjectView({ project, boardId, onBoard, system, tab, onTab, mode, onMode, onProjectChanged, onError }: Props) {
  // Data is stored with the board it belongs to, so a tab never sees the previous board's revision.
  const [loaded, setLoaded] = useState<{ boardId: string; data: BoardData } | null>(null);
  const data = loaded?.boardId === boardId ? loaded.data : EMPTY;
  const [editingName, setEditingName] = useState(false);
  const [focusProposal, setFocusProposal] = useState<string | null>(null);
  const setMode = onMode;
  const openProposal = (id: string) => {
    setFocusProposal(id);
    onTab("proposals");
  };
  /** From the step screens: open one advanced tab (e.g. 문서 · BOM), optionally at a proposal. */
  const openAdvanced = (next: TabId, proposalId?: string) => {
    if (proposalId) setFocusProposal(proposalId);
    onTab(next);
    setMode("advanced");
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
            <h1 title={project.path}>
              {project.name}
              <button className="icon" aria-label="이름 바꾸기" title="이름 바꾸기" onClick={() => setEditingName(true)}>
                ✎
              </button>
            </h1>
          )}
          {mode === "advanced" && (
            <button className="link back-link" onClick={() => setMode("flow")}>
              ← 단계 화면으로
            </button>
          )}
        </div>
        {mode === "advanced" ? (
          <div className="chips" aria-label="상태">
            <Chip label="자동 검사" text={automated.text} tone={automated.tone} />
            <Chip label="엔지니어링 검토" text={review.text} tone={review.tone} />
            <Chip text={data.revision ? `revision ${data.revision}` : "저장된 회로 없음"} tone="muted" />
          </div>
        ) : (
          <button className="ghost head-btn" onClick={() => setMode("advanced")} title="기존의 모든 세부 기능이 있는 탭 화면">
            <Icon name="panel" /> 고급 화면(탭)
          </button>
        )}
      </header>

      {mode === "flow" ? (
        <>
          {project.board_list.length > 1 && (
            <div className="board-pills" role="tablist" aria-label="보드">
              {project.board_list.map((b) => (
                <button key={b.id} role="tab" aria-selected={b.id === board.id} className={`pill ${b.id === board.id ? "active" : ""}`}
                  onClick={() => onBoard(b.id)}>
                  {b.name}
                  <span className="pill-meta">{b.current_revision ? `rev ${b.current_revision}` : "비어 있음"}</span>
                </button>
              ))}
            </div>
          )}
          <StageFlow
            key={board.id}
            project={project}
            board={board}
            circuit={data.circuit}
            revision={data.revision}
            outputs={data.outputs}
            review={data.review}
            system={system}
            onChanged={afterChange}
            onAdvanced={openAdvanced}
            onError={onError}
          />
        </>
      ) : (
        <AdvancedTabs tab={tab} onTab={onTab}>
          <BoardBar
            project={project}
            boardId={board.id}
            onBoard={onBoard}
            onAdd={addBoard}
            onChanged={onProjectChanged}
            onError={onError}
            revision={data.revision}
          />
        </AdvancedTabs>
      )}

      {mode === "advanced" && <div className="tab-body" key={board.id}>
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
          <ReviewTab
            projectId={project.id}
            boardId={board.id}
            circuit={data.circuit}
            revision={data.revision}
            onChanged={afterChange}
            onOpenProposal={openProposal}
          />
        )}
        {tab === "agent" && (
          <AgentTab projectId={project.id} boardId={board.id} revision={data.revision} onOpenProposal={openProposal} onChanged={afterChange} />
        )}
        {tab === "documents" && <DocumentsTab projectId={project.id} boardId={board.id} revision={data.revision} />}
        {tab === "history" && (
          <HistoryTab projectId={project.id} boardId={board.id} revision={data.revision} onRestored={afterChange} />
        )}
      </div>}
    </section>
  );
}

/** Two-tier tabs: four groups on top, the group's own tabs below. A group remembers its last tab. */
function AdvancedTabs({ tab, onTab, children }: { tab: TabId; onTab(tab: TabId): void; children: React.ReactNode }) {
  const group = GROUPS.find((g) => g.tabs.includes(tab)) ?? GROUPS[0];
  const [last, setLast] = useState<Record<string, TabId>>({});
  useEffect(() => setLast((cur) => ({ ...cur, [group.id]: tab })), [group.id, tab]);
  return (
    <>
      {children}
      <div className="group-tabs" role="tablist" aria-label="묶음">
        {GROUPS.map((g) => (
          <button key={g.id} role="tab" aria-selected={g.id === group.id} className={`group-tab ${g.id === group.id ? "active" : ""}`}
            title={`${g.label}: ${g.tabs.map(tabLabel).join(", ")}`} onClick={() => onTab(last[g.id] ?? g.tabs[0])}>
            <Icon name={g.icon} /> {g.label}
          </button>
        ))}
      </div>
      <div className="tabs" role="tablist" aria-label={`${group.label} 탭`}>
        {group.tabs.map((id) => {
          const t = TABS.find((x) => x.id === id)!;
          return (
            <button key={id} role="tab" aria-selected={tab === id} className={`tab ${tab === id ? "active" : ""}`} onClick={() => onTab(id)}>
              <Icon name={t.icon} size={16} /> {t.label}
            </button>
          );
        })}
      </div>
    </>
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
