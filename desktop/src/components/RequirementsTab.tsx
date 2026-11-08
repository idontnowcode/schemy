import { useCallback, useEffect, useState } from "react";

import { api, errorText, waitForJob } from "../api";
import { providerLabel } from "../format";
import type { AiStatus, Job, RequirementsSpec } from "../types";
import { canResume, DesignChoices } from "./DesignChoices";

interface Props {
  projectId: string;
  boardId: string;
  boardName: string;
  onSaved(): Promise<unknown>;
  onOpenProposal(id: string): void;
}

const PLACEHOLDER = `예)
USB-C 5V 입력으로 동작하는 CAN 노드 보드.
- 3.3V로 낮춰 CAN 트랜시버(SN65HVD230)에 공급
- MCU 보드와는 2.54mm 헤더로 UART(TXD/RXD), 3.3V, GND 연결
- CAN 버스 쪽은 2핀 커넥터, 버스 끝단이므로 120Ω 종단 저항 포함
- 칩 부품은 0603, 보유 자재 우선 사용`;

export function RequirementsTab({ projectId, boardId, boardName, onSaved, onOpenProposal }: Props) {
  const [text, setText] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [spec, setSpec] = useState<RequirementsSpec | null>(null);
  const [draftSpec, setDraftSpec] = useState<RequirementsSpec | null>(null);
  const [ai, setAi] = useState<AiStatus | null>(null);
  const [instructions, setInstructions] = useState("");
  const [useInventory, setUseInventory] = useState(true);
  const [review, setReview] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [aiMessage, setAiMessage] = useState<string | null>(null);
  const [failure, setFailure] = useState<{ problems: string[]; questions: string[] } | null>(null);
  const [stopped, setStopped] = useState<Job | null>(null);

  useEffect(() => {
    api
      .requirements(projectId)
      .then((r) => {
        // Keep anything typed before the saved text arrived (it then shows as unsaved).
        setText((cur) => (cur ? cur : r.text));
        setSaved(r.text);
      })
      .catch((e) => setMessage(errorText(e)));
    api.requirementsSpec(projectId).then((r) => setSpec(r.spec)).catch(() => setSpec(null));
    api.aiStatus().then(setAi).catch(() => setAi(null));
  }, [projectId]);

  const dirty = saved !== null && text !== saved;
  const aiReady = Boolean(ai?.settings.enabled && ai.key.configured);

  const save = useCallback(async () => {
    try {
      await api.saveRequirements(projectId, text);
      setSaved(text);
      setMessage("저장했습니다.");
      await onSaved();
    } catch (e) {
      setMessage(errorText(e));
    }
  }, [projectId, text, onSaved]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") {
      e.preventDefault();
      if (dirty) void save();
    }
  };

  const jobError = (job: Job) =>
    job.error ? `[${job.error.error_code}] ${job.error.message}${job.error.recovery_hint ? ` — ${job.error.recovery_hint}` : ""}` : "실패했습니다.";

  const run = async (label: string, fn: () => Promise<void>) => {
    setAiMessage(null);
    setFailure(null);
    setStopped(null);
    setBusy(label);
    try {
      if (dirty) await save();
      await fn();
    } catch (e) {
      setAiMessage(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const structure = () =>
    run("AI가 요구사항을 정리하는 중…", async () => {
      const ok = window.confirm(
        `요구사항 정리\n\n전송할 데이터: 요구사항 글(${text.trim().length}자)\n받는 곳: ${providerLabel(ai?.settings.provider)} (모델 ${ai?.settings.model})\n\n계속할까요?`,
      );
      if (!ok) return;
      const job = await waitForJob((await api.structureRequirements(projectId)).id, undefined, 1000);
      if (job.status === "failed") return setAiMessage(jobError(job));
      setDraftSpec(job.result.spec);
      setAiMessage("정리 결과를 확인하고 저장하세요. 저장한 정리는 AI 설계에 함께 쓰입니다.");
    });

  const saveSpec = (next: RequirementsSpec | null) =>
    run("저장하는 중…", async () => {
      await api.saveRequirementsSpec(projectId, next);
      setSpec(next);
      setDraftSpec(null);
      setAiMessage(next ? "정리한 요구사항을 저장했습니다." : "정리한 요구사항을 지웠습니다.");
    });

  const design = () =>
    run("AI가 회로를 설계하는 중… 부품 선택 → 연결 → KiCad 검사 → 검토 순서로 진행합니다 (1~3분).", async () => {
      const pv = await api.designPreview(projectId, boardId);
      const ok = window.confirm(
        `AI 회로 설계 — ${boardName}\n\n전송할 데이터: 요구사항 ${pv.requirements_chars}자` +
          `${pv.spec ? ", 정리한 요구사항" : ""}${instructions.trim() ? ", 추가 요청" : ""}` +
          `${useInventory && pv.inventory_parts ? `, 보유 자재 ${pv.inventory_parts}종의 사양` : ""}, 고른 부품의 KiCad 핀 표와 데이터시트 요약\n` +
          `받는 곳: ${providerLabel(pv.provider)} (모델 ${pv.model})\n\n` +
          "결과는 「제안」으로 만들어지며, 승인하기 전에는 회로가 바뀌지 않습니다. 계속할까요?",
      );
      if (!ok) return;
      const job = await waitForJob(
        (await api.design(projectId, boardId, { instructions: instructions.trim() || undefined, use_inventory: useInventory, review })).id,
        undefined,
        1500,
      );
      if (job.status === "failed") {
        if (canResume(job)) {
          setStopped(job);
          return;
        }
        setAiMessage(jobError(job));
        const details = job.error?.details as { problems?: string[]; questions?: string[] } | undefined;
        if (details?.problems?.length || details?.questions?.length) {
          setFailure({ problems: details.problems ?? [], questions: details.questions ?? [] });
        }
        return;
      }
      onOpenProposal(job.result.proposal_id);
    });

  return (
    <div className="requirements-layout">
      <div className="panel">
        <div className="toolbar">
          <button onClick={save} disabled={!dirty}>
            저장
          </button>
          <span className="muted small">{dirty ? "저장되지 않은 변경이 있습니다 (Ctrl+S)" : message ?? "requirements.md"}</span>
        </div>
        <p className="muted small">
          만들고 싶은 회로를 자유롭게 적으세요. 전원(입력 전압·전류), 연결할 장치·인터페이스, 꼭 쓸 부품(품번), 크기·패키지 같은 조건을 적을수록
          설계가 정확해집니다.
        </p>
        <textarea
          className="editor"
          aria-label="요구사항"
          spellCheck={false}
          value={text}
          placeholder={PLACEHOLDER}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
        />
      </div>

      <div className="side">
        <div className="card">
          <h3>AI 회로 설계</h3>
          {!aiReady ? (
            <p className="small warn-text">
              설정에서 AI 기능을 켜고 Gemini API 키를 저장하거나 Claude에 로그인하면, 이 요구사항으로 회로를 자동으로 설계하고 검토합니다.
            </p>
          ) : (
            <p className="small">
              요구사항으로 <b>{boardName}</b>의 회로를 설계합니다. AI가 부품을 고르면 앱이 실제 KiCad 심볼과 핀 번호로 바꾸고, AI가 그 핀으로
              연결을 정합니다. 결과는 KiCad 검사와 AI 검토를 거친 <b>제안</b>이 되고, 승인해야 회로에 반영됩니다.
            </p>
          )}
          <label className="small block">
            추가 요청 (선택)
            <textarea
              className="small-textarea"
              aria-label="AI 설계 추가 요청"
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder="예: 커넥터는 JST-XH로, LED 상태 표시 추가"
            />
          </label>
          <label className="small">
            <input type="checkbox" checked={useInventory} onChange={(e) => setUseInventory(e.target.checked)} /> 보유 자재 우선
          </label>{" "}
          <label className="small">
            <input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} /> 설계 후 AI 검토
          </label>
          <div className="toolbar">
            <button onClick={design} disabled={!aiReady || busy !== null || (!text.trim() && !instructions.trim())}>
              AI로 회로 설계
            </button>
            <button className="ghost" onClick={structure} disabled={!aiReady || busy !== null || text.trim().length < 10}>
              요구사항 정리
            </button>
          </div>
          {busy && <p className="small">{busy}</p>}
          {aiMessage && (
            <p className="small" role="status">
              {aiMessage}
            </p>
          )}
          {stopped && (
            <DesignChoices key={stopped.id} job={stopped} projectId={projectId} boardId={boardId}
              onStarted={(next) =>
                run("고른 방법으로 이어서 설계하는 중… (1~3분)", async () => {
                  const done = await waitForJob(next.id, undefined, 1500);
                  if (done.status === "failed") {
                    if (canResume(done)) return setStopped(done);
                    return setAiMessage(jobError(done));
                  }
                  onOpenProposal(done.result.proposal_id);
                })} />
          )}
          {failure && (
            <div className="small">
              {failure.problems.length > 0 && (
                <>
                  <b>완성하지 못한 이유</b>
                  <ul>{failure.problems.map((p, i) => <li key={i}>{p}</li>)}</ul>
                </>
              )}
              {failure.questions.length > 0 && (
                <>
                  <b>AI의 질문 — 요구사항에 답을 적고 다시 시도하세요</b>
                  <ul>{failure.questions.map((q, i) => <li key={i}>{q}</li>)}</ul>
                </>
              )}
            </div>
          )}
          <p className="muted small">AI 설계는 틀릴 수 있습니다. 제안 화면의 KiCad 검사·데이터시트 대조와 검토 의견을 보고 판단하세요.</p>
        </div>

        {(draftSpec || spec) && (
          <div className="card">
            <h3>{draftSpec ? "정리 결과 (저장 전)" : "정리한 요구사항"}</h3>
            <SpecView spec={(draftSpec ?? spec)!} />
            <div className="toolbar">
              {draftSpec ? (
                <>
                  <button onClick={() => saveSpec(draftSpec)} disabled={busy !== null}>
                    이 정리 저장
                  </button>
                  <button className="ghost" onClick={() => setDraftSpec(null)}>
                    버리기
                  </button>
                </>
              ) : (
                <button className="ghost small-btn" onClick={() => saveSpec(null)} disabled={busy !== null}>
                  정리 지우기
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function SpecView({ spec }: { spec: RequirementsSpec }) {
  const list = (title: string, items: string[]) =>
    items.length > 0 && (
      <>
        <h4>{title}</h4>
        <ul className="small">{items.map((x, i) => <li key={i}>{x}</li>)}</ul>
      </>
    );
  return (
    <div className="spec">
      {spec.summary && <p className="small">{spec.summary}</p>}
      {list("기능", spec.functions)}
      {spec.power.length > 0 && (
        <>
          <h4>전원</h4>
          <ul className="small">
            {spec.power.map((p, i) => (
              <li key={i}>
                {p.name}: {p.source} {p.voltage} {p.current ? `· ${p.current}` : ""} {p.notes ? `(${p.notes})` : ""}
              </li>
            ))}
          </ul>
        </>
      )}
      {spec.interfaces.length > 0 && (
        <>
          <h4>인터페이스</h4>
          <ul className="small">
            {spec.interfaces.map((x, i) => (
              <li key={i}>
                {x.name} ({x.kind}) {x.details ? `— ${x.details}` : ""}
              </li>
            ))}
          </ul>
        </>
      )}
      {list("부품 선호", spec.parts_preferences)}
      {list("제약", spec.constraints)}
      {spec.open_questions.length > 0 && (
        <>
          <h4 className="warn-text">확인할 질문</h4>
          <ul className="small">{spec.open_questions.map((x, i) => <li key={i}>{x}</li>)}</ul>
          <p className="muted small">답을 요구사항 글에 적고 다시 정리하면 설계가 정확해집니다.</p>
        </>
      )}
    </div>
  );
}
