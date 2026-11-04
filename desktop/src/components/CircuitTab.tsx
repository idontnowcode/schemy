import { useEffect, useRef, useState } from "react";

import { api, ApiError, errorText } from "../api";
import { parseJson, prettyJson } from "../format";
import type { CircuitIR, Example, ValidationReport } from "../types";
import { IssueList } from "./IssueList";

interface Props {
  projectId: string;
  circuit: CircuitIR | null;
  revision: number | null;
  onSaved(): Promise<unknown>;
}

const PLACEHOLDER = `{
  "schema_version": "0.1.0",
  "project_id": "…",
  "components": [ … ],
  "nets": [ … ]
}
`;

export function CircuitTab({ projectId, circuit, revision, onSaved }: Props) {
  const savedText = circuit ? prettyJson(circuit) : "";
  const [text, setText] = useState(savedText);
  const [source, setSource] = useState<"user_edit" | "import">("user_edit");
  const [report, setReport] = useState<ValidationReport | null>(null);
  const [syntaxError, setSyntaxError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [examples, setExamples] = useState<Example[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);
  const lastSaved = useRef(savedText);

  // Follow the saved circuit when the editor has no unsaved edits.
  useEffect(() => {
    if (text === lastSaved.current) setText(savedText);
    lastSaved.current = savedText;
  }, [savedText]);

  useEffect(() => {
    api.examples().then(setExamples).catch(() => setExamples([]));
  }, []);

  const dirty = text !== savedText;

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
      const r = await api.validateCircuit(projectId, value);
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
      const r = await api.saveCircuit(projectId, value, source);
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

  const loadText = (value: string, from: "import") => {
    setText(value);
    setSource(from);
    setReport(null);
    setSyntaxError(null);
    setMessage("불러왔습니다. 검사 후 저장하세요.");
  };

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (file) loadText(await file.text(), "import");
  };

  return (
    <div className="circuit-layout">
      <div className="panel">
        <div className="toolbar">
          <button onClick={validate}>검사</button>
          <button onClick={save} disabled={!text.trim()}>
            저장
          </button>
          <button className="ghost" onClick={() => fileInput.current?.click()}>
            파일 가져오기
          </button>
          <input ref={fileInput} type="file" accept=".json,application/json" hidden onChange={onFile} />
          {examples.length > 0 && (
            <select
              aria-label="예제 불러오기"
              value=""
              onChange={(e) => {
                const ex = examples.find((x) => x.id === e.target.value);
                if (ex) loadText(prettyJson({ ...ex.circuit, project_id: projectId }), "import");
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
          <span className="muted small">{dirty ? "저장되지 않은 변경" : revision ? `revision ${revision}` : "저장된 회로 없음"}</span>
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
        {circuit && <CircuitSummary circuit={circuit} />}
      </div>
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
