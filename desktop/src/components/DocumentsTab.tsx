import DOMPurify from "dompurify";
import { marked } from "marked";
import { useEffect, useState } from "react";

import { api, ApiError, errorText } from "../api";
import type { BomTable } from "../types";

interface Props {
  projectId: string;
  revision: number | null;
}

export function DocumentsTab({ projectId, revision }: Props) {
  const [html, setHtml] = useState<string>("");
  const [bom, setBom] = useState<BomTable | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!revision) {
      setHtml("");
      setBom(null);
      setMessage("저장된 회로가 없습니다.");
      return;
    }
    setMessage(null);
    Promise.all([api.markdown(projectId), api.bom(projectId)])
      .then(([md, table]) => {
        setHtml(DOMPurify.sanitize(marked.parse(md.markdown, { async: false }) as string));
        setBom(table);
      })
      .catch((e) => setMessage(e instanceof ApiError ? errorText(e) : String(e)));
  }, [projectId, revision]);

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
        <div className="toolbar">
          <strong>설계 문서 미리보기</strong>
          <span className="muted small">회로도 생성 시 outputs/circuit.md, circuit.json, bom.csv로도 저장됩니다.</span>
        </div>
        {message && <p className="muted">{message}</p>}
        <article className="markdown" dangerouslySetInnerHTML={{ __html: html }} />
      </div>
    </div>
  );
}
