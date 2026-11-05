import { useEffect, useState } from "react";

import { api, errorText } from "../api";
import type { CircuitTemplate } from "../types";

interface Props {
  projectId: string;
  boardId: string;
  onOpenProposal(id: string): void;
}

/** Reviewed circuit templates: pick one, set its nets, and place it on the board as a proposal. */
export function TemplatesTab({ projectId, boardId, onOpenProposal }: Props) {
  const [templates, setTemplates] = useState<CircuitTemplate[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [params, setParams] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api
      .templates()
      .then((r) => {
        setTemplates(r.templates);
        if (r.templates.length) choose(r.templates[0]);
      })
      .catch((e) => setMessage(errorText(e)));
  }, []);

  const choose = (t: CircuitTemplate) => {
    setSelected(t.id);
    setParams(Object.fromEntries(t.params.map((p) => [p.name, p.default])));
    setMessage(null);
  };

  const t = templates?.find((x) => x.id === selected) ?? null;

  const place = async () => {
    if (!t) return;
    setBusy(true);
    setMessage(null);
    try {
      const r = await api.placeTemplate(projectId, boardId, t.id, params);
      onOpenProposal(r.proposal.id);
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  if (!templates) return <div className="empty">{message ?? "불러오는 중…"}</div>;

  return (
    <div className="requirements-layout">
      <div className="panel">
        <p className="muted small">
          제조사 데이터시트의 응용 회로로 만든 검토된 회로 블록입니다. 보드에 넣으면 「제안」으로 만들어지고, 승인해야 회로에 들어갑니다.
          템플릿은 시작점이며, 넣은 뒤 「검토」 탭에서 체크리스트를 확인해 기록해야 엔지니어 검토 상태가 바뀝니다.
        </p>
        <ul className="template-list" aria-label="템플릿 목록">
          {templates.map((x) => (
            <li key={x.id}>
              <button className={`template-item ${x.id === selected ? "active" : ""}`} onClick={() => choose(x)}>
                <span className="chip">{x.category}</span> <b>{x.title}</b>
                <span className="muted small"> v{x.version}</span>
                <div className="small muted">{x.summary}</div>
              </button>
            </li>
          ))}
        </ul>
      </div>

      {t && (
        <div className="side">
          <div className="card">
            <h3>
              {t.title} <span className="muted small">{t.ref}</span>
            </h3>
            <p className="small">{t.summary}</p>
            <h4>연결할 Net</h4>
            <div className="form-grid">
              {t.params.map((p) => (
                <label key={p.name} className="small">
                  {p.label}
                  {p.kind === "flag" ? (
                    <select
                      aria-label={p.label}
                      value={params[p.name] ?? p.default}
                      onChange={(e) => setParams({ ...params, [p.name]: e.target.value })}
                    >
                      <option value="yes">예</option>
                      <option value="no">아니오</option>
                    </select>
                  ) : (
                    <input
                      aria-label={p.label}
                      value={params[p.name] ?? ""}
                      onChange={(e) => setParams({ ...params, [p.name]: e.target.value })}
                    />
                  )}
                </label>
              ))}
            </div>
            <div className="toolbar">
              <button onClick={place} disabled={busy}>
                {busy ? "제안 만드는 중…" : "이 보드에 넣기 (제안 만들기)"}
              </button>
            </div>
            {message && (
              <p className="small warn-text" role="status">
                {message}
              </p>
            )}
            <p className="muted small">같은 이름의 Net이 보드에 있으면 그 Net에 이어집니다. 부품 기호는 보드에 없는 번호로 정해집니다.</p>
          </div>

          <div className="card">
            <h3>부품</h3>
            <table className="table compact">
              <thead>
                <tr>
                  <th>역할</th>
                  <th>값</th>
                  <th>KiCad 심볼</th>
                  <th>풋프린트</th>
                </tr>
              </thead>
              <tbody>
                {t.parts.map((p) => (
                  <tr key={p.key}>
                    <td>{p.role}</td>
                    <td>{p.value}</td>
                    <td className="small">{p.symbol_id}</td>
                    <td className="small">{p.footprint_id ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <TemplateEvidence template={t} />
        </div>
      )}
    </div>
  );
}

export function TemplateEvidence({ template }: { template: Pick<CircuitTemplate, "basis" | "checklist"> }) {
  return (
    <div className="card">
      <h3>근거와 체크리스트</h3>
      <h4>데이터시트 근거</h4>
      <ul className="small">
        {template.basis.map((b, i) => (
          <li key={i}>
            {b.document} — <b>{b.section}</b>: {b.supports}{" "}
            <span className="muted">(쪽: {b.page ?? "엔지니어 검토 때 기록"})</span>
            {b.url && (
              <>
                {" "}
                <a href={b.url} target="_blank" rel="noreferrer">
                  문서 링크
                </a>
              </>
            )}
          </li>
        ))}
      </ul>
      <h4>검토 체크리스트</h4>
      <ol className="small">
        {template.checklist.map((c) => (
          <li key={c.id}>
            {c.text} <span className="muted">— {c.basis}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
