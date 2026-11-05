import DOMPurify from "dompurify";
import { marked } from "marked";
import { useEffect, useState } from "react";

import { api, ApiError, errorText } from "../api";
import type { BomTable } from "../types";

interface Props {
  projectId: string;
  boardId: string;
  revision: number | null;
}

export function DocumentsTab({ projectId, boardId, revision }: Props) {
  const [html, setHtml] = useState<string>("");
  const [reportHtml, setReportHtml] = useState<string>("");
  const [view, setView] = useState<"design" | "rationale">("design");
  const [bom, setBom] = useState<BomTable | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!revision || view !== "rationale") return;
    api
      .rationale(projectId, boardId)
      .then((r) => setReportHtml(DOMPurify.sanitize(marked.parse(r.markdown, { async: false }) as string)))
      .catch((e) => setMessage(errorText(e)));
  }, [projectId, boardId, revision, view]);

  useEffect(() => {
    if (!revision) {
      setHtml("");
      setBom(null);
      setMessage("저장된 회로가 없습니다.");
      return;
    }
    setMessage(null);
    Promise.all([api.markdown(projectId, boardId), api.bom(projectId, boardId)])
      .then(([md, table]) => {
        setHtml(DOMPurify.sanitize(marked.parse(md.markdown, { async: false }) as string));
        setBom(table);
      })
      .catch((e) => setMessage(e instanceof ApiError ? errorText(e) : String(e)));
  }, [projectId, boardId, revision]);

  return (
    <div className="documents-layout">
      {bom && (
        <div className="card">
          <h3>BOM (revision {bom.revision})</h3>
          <table className="table">
            <thead>
              <tr>
                {bom.columns.map((c) => (
                  <th key={c}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {bom.rows.map((row, i) => (
                <tr key={i}>
                  {bom.columns.map((c) => (
                    <td key={c}>{row[c] || "—"}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="panel">
        <div className="toolbar" role="tablist" aria-label="문서 종류">
          <button role="tab" aria-selected={view === "design"} className={view === "design" ? "" : "ghost"} onClick={() => setView("design")}>
            설계 문서
          </button>
          <button role="tab" aria-selected={view === "rationale"} className={view === "rationale" ? "" : "ghost"}
            onClick={() => setView("rationale")}>
            근거 리포트
          </button>
          <span className="muted small">
            {view === "design"
              ? "회로도 생성 시 outputs/circuit.md, circuit.json, bom.csv로도 저장됩니다."
              : "설계 결정(요구 반영·요구 수정·AI 선정·템플릿·사람 결정)과 근거, 기술 검토, 변경 이력. 생성 시 outputs/rationale.md로도 저장됩니다."}
          </span>
        </div>
        {message && <p className="muted">{message}</p>}
        <article className="markdown" dangerouslySetInnerHTML={{ __html: view === "design" ? html : reportHtml }} />
      </div>
    </div>
  );
}
