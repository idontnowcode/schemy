import { useEffect, useRef, useState } from "react";

import { api, ApiError, errorText, fileToBase64, waitForJob } from "../api";
import { addPage, AI_SETUP_HINT, moveToPage, pagesOf, parseJson, prettyJson, providerLabel, removePage, renamePage } from "../format";
import type { AiStatus, CircuitIR, Example, MarkdownImportResult, ValidationReport } from "../types";
import { ImportReport } from "./ImportReport";
import { IssueList } from "./IssueList";

interface Props {
  projectId: string;
  boardId: string;
  circuit: CircuitIR | null;
  revision: number | null;
  onSaved(): Promise<unknown>;
  onOpenProposal?(id: string): void;
}

const PLACEHOLDER = `{
  "schema_version": "0.3.0",
  "project_id": "…",
  "components": [ … ],
  "nets": [ … ]
}

또는 위의 "회로도 MD 가져오기"로 코파일럿이 만든 Markdown을 불러오세요.
`;

export function CircuitTab({ projectId, boardId, circuit, revision, onSaved, onOpenProposal }: Props) {
  const savedText = circuit ? prettyJson(circuit) : "";
  const [text, setText] = useState(savedText);
  const [source, setSource] = useState<"user_edit" | "import">("user_edit");
  const [report, setReport] = useState<ValidationReport | null>(null);
  const [importReport, setImportReport] = useState<MarkdownImportResult | null>(null);
  const [aiMarkdown, setAiMarkdown] = useState<string | null>(null);
  const [pdfProposal, setPdfProposal] = useState<string | null>(null);
  const [syntaxError, setSyntaxError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [examples, setExamples] = useState<Example[]>([]);
  const [ai, setAi] = useState<AiStatus | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const mdInput = useRef<HTMLInputElement>(null);
  const pdfInput = useRef<HTMLInputElement>(null);
  const lastSaved = useRef(savedText);

  // Follow the saved circuit when the editor has no unsaved edits.
  useEffect(() => {
    if (text === lastSaved.current) setText(savedText);
    lastSaved.current = savedText;
  }, [savedText]);

  useEffect(() => {
    api.examples().then(setExamples).catch(() => setExamples([]));
    api.aiStatus().then(setAi).catch(() => setAi(null));
  }, []);

  const dirty = text !== savedText;
  const parsedIr = (() => {
    const r = parseJson(text);
    return r.ok && r.value && typeof r.value === "object" && Array.isArray((r.value as CircuitIR).components)
      ? (r.value as CircuitIR)
      : null;
  })();

  const parsed = () => {
    const result = parseJson(text);
    if (!result.ok) {
      setSyntaxError(result.error);
      setReport(null);
      return undefined;
    }
    setSyntaxError(null);
    return result.value;
  };

  const validate = async () => {
    setMessage(null);
    const value = parsed();
    if (value === undefined) return;
    try {
      const r = await api.validateCircuit(projectId, boardId, value);
      setReport(r);
      setMessage(r.valid ? "검사 통과: 오류가 없습니다." : "오류가 있습니다.");
    } catch (e) {
      setMessage(errorText(e));
    }
  };

  const save = async () => {
    setMessage(null);
    const value = parsed();
    if (value === undefined) return;
    try {
      const r = await api.saveCircuit(projectId, boardId, value, source);
      setReport(r.validation);
      const head = r.created ? `revision ${r.revision.revision}으로 저장했습니다.` : `변경 없음 (revision ${r.revision.revision}).`;
      setMessage([head, ...r.warnings].join(" "));
      setSource("user_edit");
      await onSaved();
    } catch (e) {
      if (e instanceof ApiError && e.body.error_code === "INVALID_CIRCUIT" && e.body.details) {
        setReport(e.body.details as ValidationReport);
      }
      setMessage(errorText(e));
    }
  };

  const loadText = (value: string, note: string) => {
    setText(value);
    setSource("import");
    setReport(null);
    setSyntaxError(null);
    setMessage(note);
  };

  const onJsonFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (file) {
      setImportReport(null);
      loadText(await file.text(), "불러왔습니다. 검사 후 저장하세요.");
    }
  };

  const applyImport = (result: MarkdownImportResult, what: string) => {
    setImportReport(result);
    if (result.circuit) {
      loadText(prettyJson(result.circuit), `${what}에서 회로를 만들었습니다. 가져오기 결과를 확인한 뒤 저장하세요.`);
    } else {
      setMessage(`${what}을(를) 회로로 바꾸지 못했습니다. 가져오기 결과의 오류를 확인하세요.`);
    }
  };

  const onMarkdownFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setBusy("Markdown을 읽는 중…");
    setAiMarkdown(null);
    try {
      applyImport(await api.importMarkdown(projectId, boardId, file.name, await file.text()), "회로도 Markdown");
    } catch (err) {
      setMessage(errorText(err));
    } finally {
      setBusy(null);
    }
  };

  const onPdfFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || !ai) return;
    const ok = window.confirm(
      `PDF를 AI로 회로도 Markdown으로 바꿉니다.\n\n` +
        `전송할 데이터: '${file.name}' (${(file.size / 1024 / 1024).toFixed(2)} MB) 전체와 변환 지시문\n` +
        `받는 곳: ${providerLabel(ai.settings.provider)} (모델 ${ai.settings.model})\n\n` +
        `회사 규정상 외부 AI로 보내도 되는 도면인지 확인하세요. 계속할까요?`,
    );
    if (!ok) return;
    setBusy("AI가 PDF를 읽는 중… (회로 크기에 따라 1~3분)");
    setAiMarkdown(null);
    try {
      const job = await api.importPdf(projectId, boardId, file.name, await fileToBase64(file));
      const done = await waitForJob(job.id, undefined, 1500);
      if (done.status === "failed") {
        setMessage(done.error ? `[${done.error.error_code}] ${done.error.message}${done.error.recovery_hint ? ` — ${done.error.recovery_hint}` : ""}` : "실패했습니다.");
        return;
      }
      setAiMarkdown(done.result.markdown);
      if (done.result.proposal_id) {
        // AI output goes through the proposal approval path, not straight into the editor.
        setImportReport(done.result.import);
        setPdfProposal(done.result.proposal_id);
        setMessage(
          `AI가 옮겨 적은 회로를 제안 #${Number(done.result.proposal_id)}(으)로 만들었습니다. 「제안」 탭에서 검사 결과를 보고 승인하세요.` +
            (done.result.warnings?.length ? ` ${done.result.warnings.join(" ")}` : ""),
        );
      } else {
        applyImport(done.result.import, "PDF(AI 변환)");
        if (done.result.warnings?.length) setMessage(done.result.warnings.join(" "));
      }
    } catch (err) {
      setMessage(errorText(err));
    } finally {
      setBusy(null);
    }
  };

  const editPages = (next: CircuitIR) => {
    setText(prettyJson(next));
    setMessage("페이지 구성을 바꿨습니다. 저장하면 반영됩니다. (연결은 바뀌지 않습니다)");
  };

  const aiReady = Boolean(ai?.settings.enabled && ai.key.configured);

  return (
    <div className="circuit-layout">
      <div className="panel">
        <div className="toolbar">
          <button onClick={validate}>검사</button>
          <button onClick={save} disabled={!text.trim()}>
            저장
          </button>
          <span className="toolbar-sep" />
          <button className="ghost" onClick={() => mdInput.current?.click()}>
            회로도 MD 가져오기
          </button>
          <button
            className="ghost"
            onClick={() => pdfInput.current?.click()}
            disabled={!aiReady}
            title={aiReady ? "AI로 PDF를 읽어 회로도 Markdown으로 바꿉니다" : AI_SETUP_HINT}
          >
            PDF 가져오기 (AI)
          </button>
          <button className="ghost" onClick={() => fileInput.current?.click()}>
            JSON 가져오기
          </button>
          <input ref={fileInput} type="file" accept=".json,application/json" hidden onChange={onJsonFile} />
          <input ref={mdInput} type="file" accept=".md,.markdown,.txt,text/markdown" hidden onChange={onMarkdownFile} />
          <input ref={pdfInput} type="file" accept=".pdf,application/pdf" hidden onChange={onPdfFile} />
          {examples.length > 0 && (
            <select
              aria-label="예제 불러오기"
              value=""
              onChange={(e) => {
                const ex = examples.find((x) => x.id === e.target.value);
                if (ex) {
                  setImportReport(null);
                  loadText(prettyJson({ ...ex.circuit, project_id: projectId }), "예제를 불러왔습니다. 검사 후 저장하세요.");
                }
              }}
            >
              <option value="">예제 불러오기…</option>
              {examples.map((ex) => (
                <option key={ex.id} value={ex.id}>
                  {ex.title}
                </option>
              ))}
            </select>
          )}
        </div>
        <div className="muted small">
          {busy ?? (dirty ? "저장되지 않은 변경" : revision ? `revision ${revision}` : "저장된 회로 없음")}
          {!aiReady && " · PDF 가져오기는 설정에서 AI(Gemini 또는 Claude)를 켜면 쓸 수 있습니다. 코파일럿으로 만든 회로도 Markdown은 「회로도 MD 가져오기」로 바로 넣을 수 있습니다."}
        </div>
        <textarea
          className="editor code"
          aria-label="Circuit IR JSON"
          spellCheck={false}
          placeholder={PLACEHOLDER}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </div>

      <div className="side">
        <div className="card">
          <h3>검사 결과</h3>
          {message && <p className="small" role="status">{message}</p>}
          {report && (
            <p className="muted small">
              IR 규칙(L0/L1){report.library_checked ? " + KiCad 라이브러리 핀 대조" : " (라이브러리 미설정: 핀 대조 생략)"}
            </p>
          )}
          <IssueList issues={report?.issues} schemaErrors={report?.schema_errors} syntaxError={syntaxError} />
          <p className="muted small">
            IR 규칙 오류가 있으면 저장되지 않습니다. 라이브러리 핀 불일치는 저장은 되지만 회로도 생성이 거부됩니다.
          </p>
        </div>
        {pdfProposal && onOpenProposal && (
          <div className="card">
            <button onClick={() => onOpenProposal(pdfProposal)}>제안 #{Number(pdfProposal)} 열기</button>
          </div>
        )}
        {importReport && <ImportReport result={importReport} aiMarkdown={aiMarkdown} />}
        {parsedIr && (
          <PagesCard ir={parsedIr} onChange={editPages} />
        )}
        {circuit && <CircuitSummary circuit={circuit} />}
      </div>
    </div>
  );
}

function PagesCard({ ir, onChange }: { ir: CircuitIR; onChange(next: CircuitIR): void }) {
  const pages = pagesOf(ir);
  const refs = new Map(ir.components.map((c) => [c.id, c.reference]));
  const pageOf = new Map(pages.flatMap((p) => p.components.map((c) => [c, p.id] as const)));
  return (
    <div className="card">
      <h3>페이지 ({pages.length})</h3>
      <p className="muted small">한 보드의 회로도를 여러 장으로 나눕니다. 페이지를 오가는 Net은 자동으로 연결됩니다.</p>
      <ul className="pages">
        {pages.map((p) => (
          <li key={p.id}>
            <input
              aria-label={`페이지 ${p.id} 이름`}
              value={p.name}
              maxLength={60}
              onChange={(e) => onChange(renamePage(ir, p.id, e.target.value || p.id))}
            />
            <span className="muted small">{p.components.map((c) => refs.get(c) ?? c).join(", ") || "부품 없음"}</span>
            {pages.length > 1 && (
              <button className="icon" aria-label={`${p.name} 페이지 삭제`} onClick={() => onChange(removePage(ir, p.id))}>
                ✕
              </button>
            )}
          </li>
        ))}
      </ul>
      <div className="toolbar">
        <button className="ghost small-btn" onClick={() => onChange(addPage(ir))}>
          + 페이지
        </button>
      </div>
      {pages.length > 1 && (
        <table className="table">
          <thead>
            <tr>
              <th>부품</th>
              <th>페이지</th>
            </tr>
          </thead>
          <tbody>
            {ir.components.map((c) => (
              <tr key={c.id}>
                <td>{c.reference}</td>
                <td>
                  <select
                    aria-label={`${c.reference} 페이지`}
                    value={pageOf.get(c.id) ?? pages[0].id}
                    onChange={(e) => onChange(moveToPage(ir, c.id, e.target.value))}
                  >
                    {pages.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function CircuitSummary({ circuit }: { circuit: CircuitIR }) {
  const refs = new Map(circuit.components.map((c) => [c.id, c.reference]));
  return (
    <div className="card">
      <h3>저장된 회로</h3>
      <table className="table">
        <thead>
          <tr>
            <th>Ref</th>
            <th>값</th>
            <th>심볼</th>
            <th>핀</th>
          </tr>
        </thead>
        <tbody>
          {circuit.components.map((c) => (
            <tr key={c.id}>
              <td>{c.reference}</td>
              <td>{c.value}</td>
              <td>
                <code>{c.symbol_id}</code>
              </td>
              <td>{c.pins.map((p) => (p.name ? `${p.number}=${p.name}` : p.number)).join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <table className="table">
        <thead>
          <tr>
            <th>Net</th>
            <th>연결</th>
          </tr>
        </thead>
        <tbody>
          {circuit.nets.map((n) => (
            <tr key={n.id}>
              <td>{n.name}</td>
              <td>{n.nodes.map((x) => `${refs.get(x.component_id) ?? x.component_id}.${x.pin}`).join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
