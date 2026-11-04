import type { MarkdownImportResult } from "../types";

const SEVERITY_LABEL = { error: "오류", warning: "확인 필요", info: "기록" } as const;

export function ImportReport({ result, aiMarkdown }: { result: MarkdownImportResult; aiMarkdown: string | null }) {
  const order = ["error", "warning", "info"] as const;
  return (
    <div className="card">
      <h3>가져오기 결과</h3>
      {result.ok ? (
        <p className="small">
          부품 {result.stats.components}개 · Net {result.stats.nets}개 · 페이지 {result.stats.pages} · 불확실 항목{" "}
          {result.stats.uncertain} · 확인 필요 {result.stats.warnings}건
        </p>
      ) : (
        <p className="small bad-text">회로를 만들지 못했습니다. 아래 오류를 Markdown에서 고친 뒤 다시 가져오세요.</p>
      )}
      <p className="muted small">
        AI·코파일럿이 옮겨 적은 회로는 틀릴 수 있습니다. IC 핀 번호와 극성 부품 방향은 원본 PDF와 꼭 대조하세요.
      </p>
      {order.map((sev) => {
        const items = result.issues.filter((i) => i.severity === sev);
        if (items.length === 0) return null;
        return (
          <details key={sev} open={sev !== "info"}>
            <summary className="small">
              {SEVERITY_LABEL[sev]} {items.length}건
            </summary>
            <ul className="issues">
              {items.map((i, k) => (
                <li key={k} className={`issue issue-${sev === "info" ? "info" : sev}`}>
                  <span className="issue-code">{i.code}</span> {i.message}
                  {i.line ? <span className="muted"> (줄 {i.line})</span> : null}
                </li>
              ))}
            </ul>
          </details>
        );
      })}
      {result.uncertain.length > 0 && (
        <details open>
          <summary className="small">불확실 항목 {result.uncertain.length}개 (원본과 대조)</summary>
          <table className="table">
            <thead>
              <tr>
                <th>페이지</th>
                <th>위치</th>
                <th>내용</th>
                <th>후보</th>
              </tr>
            </thead>
            <tbody>
              {result.uncertain.map((u, k) => (
                <tr key={k}>
                  <td>{u["페이지"]}</td>
                  <td>{u["위치"]}</td>
                  <td>{u["내용"]}</td>
                  <td>{u["후보"]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      {aiMarkdown && (
        <details>
          <summary className="small">AI가 만든 Markdown 보기</summary>
          <pre className="small md-source">{aiMarkdown}</pre>
        </details>
      )}
    </div>
  );
}
