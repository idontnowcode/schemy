import { useCallback, useEffect, useState } from "react";

import { api, errorText } from "../api";
import { automatedLabel, formatTime, shortHash, sourceLabel } from "../format";
import type { Job, RevisionInfo } from "../types";
import { Chip } from "./Chip";

interface Props {
  projectId: string;
  revision: number | null;
  onRestored(): Promise<unknown>;
}

export function HistoryTab({ projectId, revision, onRestored }: Props) {
  const [revisions, setRevisions] = useState<RevisionInfo[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [r, j] = await Promise.all([api.revisions(projectId), api.jobs(projectId)]);
      setRevisions(r);
      setJobs(j);
    } catch (e) {
      setMessage(errorText(e));
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load, revision]);

  const restore = async (rev: number) => {
    if (!window.confirm(`revision ${rev}의 회로를 현재 회로로 되돌릴까요?\n새 revision으로 기록되며 기존 이력은 지워지지 않습니다.`)) return;
    try {
      const r = await api.restoreRevision(projectId, rev);
      setMessage(r.created ? `revision ${r.revision.revision}으로 복원했습니다.` : "이미 현재 회로와 같습니다.");
      await onRestored();
      await load();
    } catch (e) {
      setMessage(errorText(e));
    }
  };

  return (
    <div className="history-layout">
      <div className="card">
        <h3>회로 revision</h3>
        {message && <p className="small" role="status">{message}</p>}
        {revisions.length === 0 ? (
          <p className="muted">아직 저장된 revision이 없습니다.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>rev</th>
                <th>시각</th>
                <th>방법</th>
                <th>부품/Net</th>
                <th>SHA-256</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {revisions.map((r) => (
                <tr key={r.revision} className={r.revision === revision ? "current" : ""}>
                  <td>
                    {r.revision}
                    {r.revision === revision && <span className="badge">현재</span>}
                  </td>
                  <td>{formatTime(r.created_at)}</td>
                  <td>
                    {sourceLabel(r.source)}
                    {r.restored_from ? ` (rev ${r.restored_from})` : ""}
                  </td>
                  <td>
                    {r.component_count} / {r.net_count}
                  </td>
                  <td>
                    <code title={r.checksum}>{shortHash(r.checksum)}</code>
                  </td>
                  <td>
                    {r.revision !== revision && (
                      <button className="ghost" onClick={() => restore(r.revision)}>
                        복원
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h3>생성 작업</h3>
        {jobs.length === 0 ? (
          <p className="muted">아직 실행한 작업이 없습니다.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>시작</th>
                <th>상태</th>
                <th>결과</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => {
                const check = j.result?.automated_check;
                const label = automatedLabel(check?.status);
                return (
                  <tr key={j.id}>
                    <td>{formatTime(j.created_at)}</td>
                    <td>{{ queued: "대기", running: "실행 중", succeeded: "완료", failed: "실패" }[j.status]}</td>
                    <td>
                      {j.error ? (
                        <span className="bad-text">
                          [{j.error.error_code}] {j.error.message}
                        </span>
                      ) : check ? (
                        <>
                          rev {j.result?.source_revision} · <Chip text={label.text} tone={label.tone} />
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
