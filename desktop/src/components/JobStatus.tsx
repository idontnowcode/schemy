import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import type { ActiveJob } from "../types";
import { Icon } from "./Icons";

export const JOB_KIND_LABEL: Record<string, string> = {
  ai_design: "AI 설계",
  ai_revision: "AI 수정안",
  ai_review: "AI 검토",
  ai_auto_review: "자동 반복 검토",
  ai_eval: "AI 성능 평가",
  requirements_structure: "요구사항 정리",
  research_note: "연구노트 쓰기",
  guide_draft: "지침 초안",
  export: "회로도 생성",
  proposal_check: "제안 검사",
  pdf_import: "PDF 가져오기",
  inventory_suggest: "대체 자재 추천",
  bom_availability: "BOM 재고 조회",
  datasheet_fetch: "데이터시트 받기",
  datasheet_search: "데이터시트 찾기",
  datasheet_summary: "데이터시트 요약",
};

const running = (j: ActiveJob) => j.status === "queued" || j.status === "running";

function elapsed(from: string | null, to?: string | null): string {
  if (!from) return "대기 중";
  const s = Math.max(0, Math.round(((to ? Date.parse(to) : Date.now()) - Date.parse(from)) / 1000));
  return s < 60 ? `${s}초` : `${Math.floor(s / 60)}분 ${s % 60}초`;
}

/**
 * Work running in any project, visible on every screen (bottom right). Jobs run in the backend: leaving the screen
 * that started one does not stop it. A finished job stays here for a while and opens its result.
 */
export function JobStatus({ onOpen }: { onOpen(job: ActiveJob): void }) {
  const [jobs, setJobs] = useState<ActiveJob[]>([]);
  const [open, setOpen] = useState(true);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [, tick] = useState(0);
  const busy = jobs.some(running);
  const busyRef = useRef(busy);
  busyRef.current = busy;

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const list = await api.activeJobs();
        if (alive) setJobs(list);
      } catch {
        /* the backend may be restarting; try again */
      }
      if (alive) timer = setTimeout(poll, busyRef.current ? 1500 : 6000);
    };
    void poll();
    const onFocus = () => void api.activeJobs().then((l) => alive && setJobs(l)).catch(() => undefined);
    window.addEventListener("focus", onFocus);
    return () => {
      alive = false;
      clearTimeout(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, []);

  // Keep the elapsed time moving between polls.
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [busy]);

  const shown = jobs.filter((j) => running(j) || !dismissed.has(j.id));
  if (shown.length === 0) return null;
  const count = shown.filter(running).length;

  return (
    <aside className={`job-status ${open ? "open" : ""}`} aria-label="작업 상태">
      <button type="button" className="job-status-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        {count > 0 ? <span className="tick run" aria-hidden /> : <Icon name="check" size={16} />}
        <b>{count > 0 ? `작업 ${count}개 진행 중` : "작업 끝남"}</b>
        <span className="muted small">{open ? "접기" : "펼치기"}</span>
      </button>
      {open && (
        <ul className="job-status-list">
          {shown.map((j) => {
            const where = [j.project_name, j.board_name].filter(Boolean).join(" · ");
            return (
              <li key={j.id} className={`job-row ${j.status}`}>
                <button type="button" className="job-open" onClick={() => onOpen(j)}
                  title="이 프로젝트로 가기">
                  <span className="job-title">
                    <b>{JOB_KIND_LABEL[j.kind] ?? j.kind}</b>
                    <span className="muted small"> · {where}</span>
                  </span>
                  <span className="small job-step">
                    {running(j)
                      ? `${j.step ?? "시작하는 중…"} · ${elapsed(j.started_at)}`
                      : j.status === "succeeded"
                        ? `끝남 (${elapsed(j.started_at, j.finished_at)})${j.proposal_id ? ` · 제안 #${Number(j.proposal_id)} 열기` : ""}`
                        : `실패: ${j.error ?? "이유 모름"}`}
                  </span>
                </button>
                {!running(j) && (
                  <button type="button" className="icon" aria-label="목록에서 지우기"
                    onClick={() => setDismissed(new Set([...dismissed, j.id]))}>
                    ✕
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
}
