import { useCallback, useEffect, useState } from "react";

import { api, errorText } from "../api";
import { outputsLabel, reviewLabel } from "../format";
import type { CircuitIR, OutputsStatus, ProjectSummary, SystemInfo } from "../types";
import { Chip } from "./Chip";
import { CircuitTab } from "./CircuitTab";
import { DocumentsTab } from "./DocumentsTab";
import { HistoryTab } from "./HistoryTab";
import { RequirementsTab } from "./RequirementsTab";
import { SchematicTab } from "./SchematicTab";

export const TABS = [
  { id: "requirements", label: "요구사항" },
  { id: "circuit", label: "회로 (IR)" },
  { id: "schematic", label: "회로도 · 검증" },
  { id: "documents", label: "문서 · BOM" },
  { id: "history", label: "이력" },
] as const;

export type TabId = (typeof TABS)[number]["id"];

export function isTab(value: unknown): value is TabId {
  return TABS.some((t) => t.id === value);
}

interface Props {
  project: ProjectSummary;
  system: SystemInfo | null;
  tab: TabId;
  onTab(tab: TabId): void;
  onProjectChanged(): Promise<unknown>;
  onError(message: string | null): void;
}

export interface ProjectData {
  circuit: CircuitIR | null;
  revision: number | null;
  outputs: OutputsStatus | null;
}

export function ProjectView({ project, system, tab, onTab, onProjectChanged, onError }: Props) {
  const [data, setData] = useState<ProjectData>({ circuit: null, revision: null, outputs: null });
  const [editingName, setEditingName] = useState(false);
  const [name, setName] = useState(project.name);

  const reload = useCallback(async () => {
    try {
      const [c, o] = await Promise.all([api.circuit(project.id), api.outputs(project.id)]);
      setData({ circuit: c.circuit, revision: c.revision, outputs: o });
    } catch (e) {
      onError(errorText(e));
    }
  }, [project.id, onError]);

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

  const automated = outputsLabel(data.outputs);
  const review = reviewLabel(data.circuit?.validation?.status ?? (data.circuit ? "unverified" : null));

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
            <code>{project.id}</code> · {data.revision ? `revision ${data.revision}` : "저장된 회로 없음"} ·{" "}
            <span title={project.path} className="truncate">
              {project.path}
            </span>
          </div>
        </div>
        <div className="chips" aria-label="상태">
          <Chip label="자동 검사" text={automated.text} tone={automated.tone} />
          <Chip label="엔지니어링 검토" text={review.text} tone={review.tone} />
        </div>
      </header>

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

      <div className="tab-body">
        {tab === "requirements" && <RequirementsTab projectId={project.id} onSaved={onProjectChanged} />}
        {tab === "circuit" && (
          <CircuitTab projectId={project.id} circuit={data.circuit} revision={data.revision} onSaved={afterChange} />
        )}
        {tab === "schematic" && (
          <SchematicTab
            projectId={project.id}
            revision={data.revision}
            outputs={data.outputs}
            system={system}
            onChanged={afterChange}
          />
        )}
        {tab === "documents" && <DocumentsTab projectId={project.id} revision={data.revision} />}
        {tab === "history" && <HistoryTab projectId={project.id} revision={data.revision} onRestored={afterChange} />}
      </div>
    </section>
  );
}
