import { useState } from "react";

import { api, errorText } from "../api";
import type { DesignChoice, DesignOption, Job } from "../types";
import { Chip } from "./Chip";

/** True when a failed AI design stopped at parts with no KiCad symbol and can continue with the person's choices. */
export function canResume(job: Job | null): boolean {
  const d = job?.error?.details as { choices?: DesignChoice[] } | undefined;
  return job?.status === "failed" && Array.isArray(d?.choices) && d!.choices.length > 0;
}

interface Pick {
  option?: string;
  /** 「기타 의견」 */
  own?: boolean;
  text?: string;
}

/**
 * When the AI's parts plan names a part this library has no symbol for, the design stops here instead of failing:
 * for each such part the person picks 1) another part (checked in the library), 2) another way (a module header,
 * or the AI's idea), or 3) their own direction, answers the AI's questions, and the design continues.
 */
export function DesignChoices({ job, projectId, boardId, onStarted, onBack }: {
  job: Job;
  projectId: string;
  boardId: string;
  onStarted(job: Job): void;
  onBack?(): void;
}) {
  const details = (job.error?.details ?? {}) as { choices?: DesignChoice[]; questions?: string[] };
  const choices = details.choices ?? [];
  const questions = details.questions ?? [];
  const [picks, setPicks] = useState<Record<string, Pick>>(() =>
    Object.fromEntries(choices.map((c) => [c.ref, c.options[0] ? { option: c.options[0].id } : { own: true }])));
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const ready = choices.every((c) => {
    const p = picks[c.ref];
    return p && (p.own ? Boolean(p.text?.trim()) : Boolean(p.option));
  });
  const chosen = choices.map((c) => c.options.find((o) => !picks[c.ref]?.own && o.id === picks[c.ref]?.option) ?? null);
  const continues = chosen.every((o) => o?.executable);

  const start = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const next = await api.resumeDesign(projectId, boardId, {
        job_id: job.id,
        choices: choices.map((c) => {
          const p = picks[c.ref];
          return p.own ? { ref: c.ref, text: p.text?.trim() } : { ref: c.ref, option_id: p.option };
        }),
        answers: questions.map((q, i) => ({ question: q, answer: (answers[i] ?? "").trim() })).filter((a) => a.answer),
      });
      onStarted(next);
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const optionRow = (c: DesignChoice, o: DesignOption) => (
    <label key={o.id} className={`choice-option ${picks[c.ref]?.option === o.id && !picks[c.ref]?.own ? "on" : ""}`}>
      <input type="radio" name={`choice-${job.id}-${c.ref}`} checked={!picks[c.ref]?.own && picks[c.ref]?.option === o.id}
        onChange={() => setPicks({ ...picks, [c.ref]: { option: o.id } })} />
      <span className="choice-text">
        <span className="choice-title">
          <b>{o.title}</b>
          {o.executable ? (
            <Chip text={o.kind === "replace" ? "KiCad 심볼 있음" : "바로 진행"} tone="ok" />
          ) : (
            <Chip text="AI가 다시 설계" tone="info" />
          )}
          {o.source === "library" && <span className="muted small">라이브러리 검색</span>}
        </span>
        {o.symbol_id && <code className="small">{o.symbol_id}</code>}
        <span className="muted small">{o.detail}</span>
      </span>
    </label>
  );

  return (
    <div className="design-choices">
      <p>
        <b>AI가 고른 부품 중 {choices.length}개를 이 컴퓨터의 KiCad 라이브러리에서 찾지 못했습니다.</b> 부품마다 진행할 방법을 고르면 이어서
        설계합니다.
      </p>
      {choices.map((c) => {
        const replace = c.options.filter((o) => o.kind === "replace");
        const approach = c.options.filter((o) => o.kind === "approach");
        const p = picks[c.ref] ?? {};
        return (
          <fieldset key={c.ref} className="choice-part">
            <legend>
              <span className="mono">{c.ref}</span> · {c.role || "부품"} — {[c.value, c.mpn].filter(Boolean).join(" ")}
            </legend>
            <p className="muted small">{c.why}</p>
            <div className="choice-group">
              <h4>1) 다른 부품으로 대체</h4>
              {replace.length ? replace.map((o) => optionRow(c, o)) : <p className="muted small">라이브러리에서 쓸 수 있는 대체 부품을 찾지 못했습니다.</p>}
            </div>
            <div className="choice-group">
              <h4>2) 다른 방법으로 진행</h4>
              {approach.length ? approach.map((o) => optionRow(c, o)) : <p className="muted small">제안된 방법이 없습니다.</p>}
            </div>
            <div className="choice-group">
              <h4>3) 기타 의견</h4>
              <label className={`choice-option ${p.own ? "on" : ""}`}>
                <input type="radio" name={`choice-${job.id}-${c.ref}`} checked={Boolean(p.own)}
                  onChange={() => setPicks({ ...picks, [c.ref]: { own: true, text: p.text } })} />
                <span className="choice-text">
                  <b>직접 방향 적기</b>
                  <textarea aria-label={`${c.ref} 기타 의견`} className="small-textarea" value={p.text ?? ""}
                    placeholder="예: 같은 기능의 ST 제품으로, 또는 이 기능은 빼고 진행"
                    onFocus={() => !p.own && setPicks({ ...picks, [c.ref]: { own: true, text: p.text } })}
                    onChange={(e) => setPicks({ ...picks, [c.ref]: { own: true, text: e.target.value } })} />
                </span>
              </label>
            </div>
          </fieldset>
        );
      })}
      {questions.length > 0 && (
        <div className="flow-questions">
          <h3>AI의 질문 (답하면 요구사항에 붙어 설계에 쓰임)</h3>
          {questions.map((q, i) => (
            <label key={i} className="flow-question">
              <span>{q}</span>
              <input aria-label={q} value={answers[i] ?? ""} placeholder="답 (선택)" onChange={(e) => setAnswers({ ...answers, [i]: e.target.value })} />
            </label>
          ))}
        </div>
      )}
      <p className="flow-note">
        {continues
          ? "고른 부품으로 부품 계획을 고쳐 핀 연결부터 이어서 설계합니다 (AI 호출 약 2~4회)."
          : "적은 방향(또는 AI 방안)을 요구사항에 더해 부품 계획부터 다시 설계합니다 (AI 호출 약 3~5회)."}{" "}
        결과는 제안으로 만들어지고, 승인하기 전에는 회로가 바뀌지 않습니다.
      </p>
      {message && <p className="small bad-text" role="alert">{message}</p>}
      <div className="flow-row">
        <button type="button" onClick={start} disabled={!ready || busy}>
          고른 방법으로 이어서 설계
        </button>
        {onBack && (
          <button type="button" className="ghost" onClick={onBack}>
            설계 준비로 돌아가기
          </button>
        )}
      </div>
    </div>
  );
}
