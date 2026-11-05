import { useEffect, useState } from "react";

import { api, errorText } from "../api";
import { FINDING_SEVERITY } from "../format";
import type { TechnicalReview } from "../types";
import { Chip } from "./Chip";

/** Calculated technical review of the current revision (no AI): what to look at, not a verification. */
export function TechnicalReviewCard({ projectId, boardId, revision }: { projectId: string; boardId: string; revision: number | null }) {
  const [review, setReview] = useState<TechnicalReview | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [showOk, setShowOk] = useState(false);

  useEffect(() => {
    if (!revision) {
      setReview(null);
      return;
    }
    setMessage(null);
    api.technicalReview(projectId, boardId).then(setReview).catch((e) => setMessage(errorText(e)));
  }, [projectId, boardId, revision]);

  if (!revision) return null;
  const rows = review?.findings.filter((f) => showOk || f.severity !== "ok") ?? [];
  return (
    <div className="card" aria-label="기술 검토">
      <h3>기술 검토 (자동 계산)</h3>
      <p className="muted small">
        회로 데이터로 직접 계산한 결과입니다(AI 아님): 콘덴서 정격 전압·극성, LED 방향·전류, 저항 소비 전력, 레귤레이터 드롭아웃·발열, IC
        디커플링. 전압은 Net 이름(+5V, +3V3)에서 읽습니다. 확인할 곳을 알려 줄 뿐, 엔지니어 검토를 대신하지 않습니다.
      </p>
      {message && <p className="small bad-text">{message}</p>}
      {review && (
        <>
          <div className="toolbar">
            {(["fail", "warn", "info", "ok"] as const).map((k) => (
              <Chip key={k} text={`${FINDING_SEVERITY[k].text} ${review.summary[k]}`} tone={FINDING_SEVERITY[k].tone} />
            ))}
            <label className="small">
              <input type="checkbox" checked={showOk} onChange={(e) => setShowOk(e.target.checked)} /> 통과 항목도 보기
            </label>
          </div>
          {rows.length === 0 ? (
            <p className="small">주의할 항목이 없습니다.</p>
          ) : (
            <table className="table compact">
              <thead>
                <tr><th>결과</th><th>항목</th><th>내용</th><th>계산</th></tr>
              </thead>
              <tbody>
                {rows.map((f, i) => (
                  <tr key={i}>
                    <td><Chip text={FINDING_SEVERITY[f.severity].text} tone={FINDING_SEVERITY[f.severity].tone} /></td>
                    <td>{f.title}</td>
                    <td className="small">{f.detail}</td>
                    <td className="small mono">{f.calc ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  );
}
