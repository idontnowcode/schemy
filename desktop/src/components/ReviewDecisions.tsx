import { useEffect, useState } from "react";

import { api, errorText } from "../api";
import type { AiFinding, Proposal, ReviewCheck, ReviewDecision } from "../types";
import { Chip } from "./Chip";

/** The 수정 필요/권장 findings still open on a proposal: confirmed by the app and not decided by a person. */
export function openFindings(p: Proposal | null): AiFinding[] {
  const findings = p?.ai?.review?.findings ?? [];
  return findings.filter((f) => (f.severity === "error" || f.severity === "warning") && f.verified !== false && !f.decided);
}

/** The checklist items the perspective passes could not judge for lack of material (a rating, a part number, a datasheet). */
export function unknownChecks(p: Proposal | null): (ReviewCheck & { perspective: string })[] {
  // Item numbers repeat across passes, so a mixed list names the perspective instead of the number.
  return (p?.ai?.review?.passes ?? []).flatMap((x) => x.checks.filter((c) => c.result === "unknown")
    .map((c) => ({ ...c, item: c.item.replace(/^\(\d+\)\s*/, ""), perspective: x.label })));
}

/**
 * What is left after the automatic loop, and the person's two ways out: have it fixed (a revision with their own
 * direction) or keep it as designed with a reason. A kept finding is not counted again and later reviews are told
 * not to raise it, so the loop can finish at zero without the AI pretending to fix it.
 */
export function ReviewDecisions({ pid, bid, proposal, onChanged, onFix }: {
  pid: string;
  bid: string;
  proposal: Proposal | null;
  onChanged(): Promise<void> | void;
  onFix(): void;
}) {
  const [decisions, setDecisions] = useState<ReviewDecision[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const left = openFindings(proposal);
  const unknown = unknownChecks(proposal);
  const declined = proposal?.ai?.not_addressed ?? [];

  const load = () => api.reviewDecisions(pid, bid).then(setDecisions).catch(() => setDecisions([]));
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid, bid, proposal?.id]);

  const keep = async (f: AiFinding) => {
    setBusy(true);
    setMessage(null);
    try {
      await api.addReviewDecision(pid, bid, { title: f.title, reason: reason.trim(), proposal_id: proposal?.id });
      setOpen(null);
      setReason("");
      await load();
      await onChanged();
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const undo = async (d: ReviewDecision) => {
    setBusy(true);
    try {
      await api.removeReviewDecision(pid, bid, d.id);
      await load();
      await onChanged();
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  if (!proposal?.ai?.review && decisions.length === 0) return null;
  return (
    <div className="review-decisions" aria-label="남은 의견">
      <h3>남은 의견 {left.length}개{left.length === 0 ? " — AI가 고칠 것이 없습니다" : ""}</h3>
      {left.length > 0 && (
        <p className="muted small">
          AI가 고치지 못했거나 사람이 정해야 하는 의견입니다. 고칠 방향을 정해 수정안을 만들거나, 의도한 설계라면 이유를 남겨 두세요.
          남긴 의견은 다시 세지 않고, 다음 검토에서 AI에게도 알립니다.
        </p>
      )}
      <ul className="review-left">
        {left.map((f, i) => {
          const why = f.ai_declined || declined.find((d) => d.title === f.title)?.why;
          return (
            <li key={`${f.title}-${i}`} className="review-left-item">
              <div className="review-left-head">
                <Chip text={f.severity === "error" ? "수정 필요" : "수정 권장"} tone={f.severity === "error" ? "bad" : "warn"} />
                <b>{f.title}</b>
              </div>
              <p className="small">{f.detail}</p>
              {why && <p className="small muted">AI가 고치지 않은 이유: {why}</p>}
              {open === i ? (
                <div className="review-keep">
                  <label className="small">
                    의도한 설계로 두는 이유
                    <textarea aria-label={`${f.title} 이유`} value={reason} onChange={(e) => setReason(e.target.value)}
                      placeholder="예: 제어반 내부용이라 서지 요구가 없음. TVS는 ESD용으로 둠" />
                  </label>
                  <div className="flow-row">
                    <button type="button" onClick={() => keep(f)} disabled={busy || reason.trim().length < 2}>기록</button>
                    <button type="button" className="ghost" onClick={() => setOpen(null)}>취소</button>
                  </div>
                </div>
              ) : (
                <div className="flow-row">
                  <button type="button" className="ghost" onClick={onFix}>고칠 방향 정해 수정안 만들기</button>
                  <button type="button" className="ghost" onClick={() => { setOpen(i); setReason(""); }}>의도한 설계로 두기</button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {unknown.length > 0 && (
        <details className="small review-unknown" open={left.length === 0}>
          <summary>AI가 판단하지 못한 것 {unknown.length}개 — 사람이 확인해야 합니다</summary>
          <p className="muted">
            자료가 없어 AI가 맞는지 틀린지 답하지 못한 확인 항목입니다. 지적으로 세지 않았습니다.
            품번·정격을 넣거나 데이터시트를 받은 뒤 다시 검토하면 판단합니다.
          </p>
          <ul>
            {unknown.map((c, i) => (
              <li key={i}>
                <span className="muted">{c.perspective} · </span><b>{c.item}</b>
                {c.references.length > 0 && <span className="muted"> ({c.references.join(", ")})</span>}
                {c.note && <> — {c.note}</>}
              </li>
            ))}
          </ul>
        </details>
      )}
      {decisions.length > 0 && (
        <details className="small">
          <summary>사람이 정한 것 {decisions.length}개</summary>
          <ul className="review-decided">
            {decisions.map((d) => (
              <li key={d.id}>
                <b>{d.title}</b> — {d.reason}{" "}
                <button type="button" className="link" onClick={() => undo(d)} disabled={busy}>결정 취소</button>
              </li>
            ))}
          </ul>
        </details>
      )}
      {message && <p className="small bad-text" role="alert">{message}</p>}
    </div>
  );
}
