import DOMPurify from "dompurify";
import { marked } from "marked";
import { useCallback, useEffect, useState } from "react";

import { api, errorText, waitForJob } from "../api";
import { AI_SETUP_HINT, formatTime, providerLabel } from "../format";
import type { EvalCases, EvalChange, EvalRunInfo, EvalRunPage, EvalSuite, EvalSummary } from "../types";
import { Chip } from "./Chip";

const SUITE_LABEL: Record<EvalSuite, string> = { design: "설계", review: "검토", revise: "수정", notes: "연구노트" };

/** The four numbers shown on each run in the history (the full set is in the run's report). */
export const HEADLINE: { key: string; label: string; kind: EvalChange["kind"] }[] = [
  { key: "design.completed", label: "설계 완료", kind: "rate" },
  { key: "design.requirements_met", label: "요구 충족", kind: "rate" },
  { key: "review.found", label: "결함 탐지", kind: "rate" },
  { key: "revise.fixed", label: "결함 고침", kind: "rate" },
  { key: "notes.cited", label: "노트 근거 인용", kind: "rate" },
];

export function metricValue(summary: EvalSummary | undefined, key: string): number | null {
  const [group, name] = key.split(".");
  const v = summary?.[group]?.[name];
  return v === undefined ? null : v;
}

export function formatMetric(value: number | null, kind: EvalChange["kind"]): string {
  if (value === null || value === undefined) return "—";
  if (kind === "rate") return `${Math.round(value * 100)}%`;
  if (kind === "count") return Math.round(value).toLocaleString();
  return String(value);
}

export function estimateCalls(cases: EvalCases | null, suites: EvalSuite[], repeats: number): number {
  if (!cases) return 0;
  return suites.reduce((n, s) => n + (cases.calls_per_repeat[s] ?? 0), 0) * repeats;
}

function download(name: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function RunDetail({ id, onDeleted }: { id: string; onDeleted(): void }) {
  const [page, setPage] = useState<EvalRunPage | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api.evalRun(id).then(setPage).catch((e) => setMessage(errorText(e)));
  }, [id]);

  if (!page) return message ? <p className="small bad-text">{message}</p> : <p className="small muted">불러오는 중…</p>;
  const html = DOMPurify.sanitize(marked.parse(page.markdown, { async: false }) as string);
  const moved = page.changes.filter((c) => c.better !== null);
  return (
    <section aria-label={`평가 ${id}`}>
      <div className="toolbar">
        <button className="ghost" onClick={() => void api.evalReport(id).then((t) => download(`ai-eval-${id}.md`, t)).catch((e) => setMessage(errorText(e)))}>
          보고서 받기 (Markdown)
        </button>
        <button className="ghost danger"
          onClick={() => window.confirm("이 평가 기록을 지울까요?") && void api.deleteEvalRun(id).then(onDeleted).catch((e) => setMessage(errorText(e)))}>
          기록 지우기
        </button>
        {page.previous_id && <span className="small muted">이전 평가 {page.previous_id}와 비교</span>}
      </div>
      {moved.length > 0 && (
        <div className="chips" aria-label="이전 평가와 달라진 지표">
          {moved.map((c) => (
            <Chip key={c.key} label={c.label} text={`${formatMetric(c.old, c.kind)} → ${formatMetric(c.new, c.kind)}`} tone={c.better ? "ok" : "bad"} />
          ))}
        </div>
      )}
      {message && <p className="small bad-text">{message}</p>}
      <article className="markdown" dangerouslySetInnerHTML={{ __html: html }} />
    </section>
  );
}

/**
 * AI performance evaluation: fixed cases from the example boards, scored by code. Each run is kept so models,
 * prompts and app versions can be compared.
 */
export function EvalPage({ onError }: { onError(message: string | null): void }) {
  const [cases, setCases] = useState<EvalCases | null>(null);
  const [runs, setRuns] = useState<EvalRunInfo[]>([]);
  const [suites, setSuites] = useState<EvalSuite[]>(["design", "review", "revise", "notes"]);
  const [repeats, setRepeats] = useState(1);
  const [ready, setReady] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number; current: string | null } | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [c, r, ai] = await Promise.all([api.evalCases(), api.evalRuns(), api.aiStatus()]);
      setCases(c);
      setRuns(r.runs);
      setReady(Boolean(ai.settings.enabled && ai.key.configured));
    } catch (e) {
      onError(errorText(e));
    }
  }, [onError]);

  useEffect(() => {
    void load();
  }, [load]);

  const calls = estimateCalls(cases, suites, repeats);

  const start = async () => {
    if (!cases) return;
    setMessage(null);
    const go = window.confirm(
      `AI 성능 평가\n\n받는 곳: ${providerLabel(cases.provider)} · 모델 ${cases.model}\n` +
        `보내는 것: 앱에 들어 있는 예제 보드의 요구사항·회로와 채택된 설계 지침(사용자 프로젝트는 보내지 않음)\n` +
        `묶음: ${suites.map((s) => SUITE_LABEL[s]).join(", ")} · 반복 ${repeats}회 · AI 호출 약 ${calls}회(설계는 사례당 2~4회)\n` +
        "요금제·API 크레딧이 그만큼 쓰입니다. 채점은 코드가 합니다.\n\n계속할까요?",
    );
    if (!go) return;
    try {
      const job = await api.startEval(suites, repeats);
      setProgress({ done: 0, total: 0, current: null });
      const done = await waitForJob(job.id, () => void api.evalProgress(job.id).then(setProgress).catch(() => undefined), 1500);
      if (done.status === "failed") {
        setMessage(done.error ? `[${done.error.error_code}] ${done.error.message}` : "평가가 실패했습니다.");
        return;
      }
      setOpen(done.result.run_id);
      setMessage(`평가 ${done.result.run_id}를 마쳤습니다.`);
      await load();
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setProgress(null);
    }
  };

  return (
    <section className="page evals-page" aria-label="AI 성능 평가">
      <div className="card">
        <h2>AI 성능 평가</h2>
        <p className="small">
          지금 고른 AI(공급자·모델)가 이 앱에서 얼마나 잘 하는지 같은 시험으로 잽니다. 시험은 앱의 예제 보드로 만들었고, <b>채점은 모두 코드</b>가
          합니다(AI가 AI를 채점하지 않음). 기록이 남으므로 모델을 바꾸거나 앱을 고친 뒤 다시 돌려 나아졌는지 비교하세요.
        </p>
        <ul className="small">
          <li>
            <b>설계</b>: 예제 요구사항으로 AI가 회로를 설계 → 완성 여부, IR 규칙 오류, KiCad 검사(있으면), 기술 검토 실패, 근거 기록, 사례별 요구
            충족(예: SCL·SDA 풀업이 3.3 V로)
          </li>
          <li>
            <b>검토</b>: 예제 회로에 결함을 심고(LED 방향, 풀업 누락, 콘덴서 정격, 디커플링 누락, IC 접지 미연결, 플라이백 다이오드 누락, USB-C CC 공유, RS-485 DE/RE 풀업, 절연 GND 연결, ADC 클램프 누락) AI 검토가 찾는지, 결함 없는 회로에서
            오류를 얼마나 잘못 지적하는지. 같은 회로에 앱의 계산 검토도 돌려 비교
          </li>
          <li>
            <b>연구노트</b>: 근거 번호를 단 문장 비율, 없는 근거 번호, 근거 없는 쪽 번호, 절 채움
          </li>
        </ul>
        {cases && (
          <>
            <div className="toolbar">
              {(Object.keys(SUITE_LABEL) as EvalSuite[]).map((s) => (
                <label key={s} className="small">
                  <input type="checkbox" checked={suites.includes(s)} aria-label={`${SUITE_LABEL[s]} 묶음`}
                    onChange={(e) => setSuites(e.target.checked ? [...suites, s] : suites.filter((x) => x !== s))} />{" "}
                  {SUITE_LABEL[s]} ({s === "design" ? cases.design.length : s === "review" ? cases.review.length * 2 : s === "revise" ? (cases.revise?.length ?? 0) : cases.notes.length}건)
                </label>
              ))}
              <label className="small">
                반복{" "}
                <select aria-label="반복 횟수" value={repeats} onChange={(e) => setRepeats(Number(e.target.value))}>
                  {[1, 2, 3, 5].map((n) => (
                    <option key={n} value={n}>
                      {n}회
                    </option>
                  ))}
                </select>
              </label>
              <button onClick={() => void start()} disabled={!ready || suites.length === 0 || progress !== null}
                title={ready ? "평가를 시작합니다(실행 전에 확인)" : AI_SETUP_HINT}>
                {progress ? "평가 중…" : "평가 실행"}
              </button>
            </div>
            <p className="small muted">
              {providerLabel(cases.provider)} · {cases.model} · AI 호출 약 {calls}회 · KiCad 검사 {cases.kicad ? "함" : "안 함(KiCad 없음: 「통과」로 세지 않음)"}
              {repeats > 1 && " · 같은 시험을 여러 번 돌리면 결과가 얼마나 흔들리는지도 보입니다"}
            </p>
          </>
        )}
        {progress && (
          <p className="small" role="status">
            {progress.total ? `${progress.done}/${progress.total}` : "시작하는 중"} {progress.current ? `· ${progress.current}` : ""}
          </p>
        )}
        {message && (
          <p className="small" role="status">
            {message}
          </p>
        )}
      </div>

      {runs.length === 0 ? (
        <div className="empty">평가 기록이 없습니다. AI 기능을 켜고 「평가 실행」을 누르세요.</div>
      ) : (
        <div className="card">
          <h3>평가 기록</h3>
          <table className="table compact" aria-label="평가 기록">
            <thead>
              <tr>
                <th>평가</th>
                <th>AI</th>
                <th>묶음</th>
                {HEADLINE.map((h) => (
                  <th key={h.key}>{h.label}</th>
                ))}
                <th>호출</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className={open === r.id ? "selected" : ""}>
                  <td>
                    <button className="link" onClick={() => setOpen(open === r.id ? null : r.id)} aria-expanded={open === r.id}>
                      {formatTime(r.created_at)}
                    </button>
                  </td>
                  <td className="small">
                    {r.provider} · {r.model}
                  </td>
                  <td className="small">
                    {r.suites.map((s) => SUITE_LABEL[s]).join(", ")} ×{r.repeats}
                  </td>
                  {HEADLINE.map((h) => (
                    <td key={h.key}>{formatMetric(metricValue(r.summary, h.key), h.kind)}</td>
                  ))}
                  <td className="small">{formatMetric(metricValue(r.summary, "cost.calls"), "count")}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {open && (
            <RunDetail key={open} id={open} onDeleted={() => {
              setOpen(null);
              void load();
            }} />
          )}
        </div>
      )}
    </section>
  );
}
