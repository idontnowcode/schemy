import { useEffect, useState } from "react";

import { api, ApiError, errorText, outputText, waitForJob } from "../api";
import { desktop } from "../desktop";
import { AI_SETUP_HINT, automatedLabel, cropSvg, ERC_EXPLAIN, ercWarningText, formatTime, shortHash } from "../format";
import type { AiStatus, ApiErrorBody, BoardSummary, Issue, Job, OutputsStatus, SystemInfo } from "../types";
import { Chip } from "./Chip";
import { IssueList } from "./IssueList";
import { ErcViolations, QuickFixPanel, RevisionPanel } from "./ProposalsTab";

interface Props {
  projectId: string;
  board: BoardSummary;
  revision: number | null;
  outputs: OutputsStatus | null;
  system: SystemInfo | null;
  fixedParts?: number;
  onChanged(): Promise<unknown>;
  onOpenProposal?(id: string): void;
}

const ZOOMS = [
  { id: "fit", label: "맞춤" },
  { id: "1", label: "100%" },
  { id: "2", label: "200%" },
] as const;

export function SchematicTab({ projectId, board, revision, outputs, system, fixedParts = 0, onChanged, onOpenProposal }: Props) {
  const [job, setJob] = useState<Job | null>(null);
  const [layoutMessage, setLayoutMessage] = useState<string | null>(null);
  const [layoutProposal, setLayoutProposal] = useState<string | null>(null);
  const [layoutBusy, setLayoutBusy] = useState(false);

  const layoutAction = async (action: "import" | "edits" | "reset") => {
    setLayoutBusy(true);
    setLayoutMessage(null);
    setLayoutProposal(null);
    try {
      let skipped: string[] = [];
      let r;
      if (action === "edits") {
        const e = await api.importKicadEdits(projectId, board.id);
        skipped = e.skipped;
        r = e;
      } else {
        r = action === "import" ? await api.importKicadLayout(projectId, board.id) : await api.resetLayout(projectId, board.id);
      }
      setLayoutProposal(r.proposal.id);
      setLayoutMessage(`제안 #${Number(r.proposal.id)}을 만들었습니다. 승인하면 반영됩니다.` +
        (skipped.length ? ` 가져오지 않은 것: ${skipped.join(" ")}` : ""));
      await onChanged();
    } catch (e) {
      setLayoutMessage(errorText(e));
    } finally {
      setLayoutBusy(false);
    }
  };
  const [failure, setFailure] = useState<ApiErrorBody | string | null>(null);
  const [svgUrl, setSvgUrl] = useState<string | null>(null);
  const [zoom, setZoom] = useState<(typeof ZOOMS)[number]["id"]>("fit");
  const [pageIndex, setPageIndex] = useState(0);
  const [ai, setAi] = useState<AiStatus | null>(null);
  const [revising, setRevising] = useState(false);
  const [fixMessage, setFixMessage] = useState<{ text: string; proposal: string | null } | null>(null);
  const aiReady = Boolean(ai?.settings.enabled && ai.key.configured);

  useEffect(() => {
    api.aiStatus().then(setAi).catch(() => setAi(null));
  }, []);

  const manifest = outputs?.manifest ?? null;
  const pages = manifest?.pages ?? [];
  const page = pages[Math.min(pageIndex, Math.max(pages.length - 1, 0))] ?? null;
  const running = job !== null && (job.status === "queued" || job.status === "running");

  useEffect(() => {
    if (!page?.svg || !manifest) {
      setSvgUrl(null);
      return;
    }
    let url: string | null = null;
    let cancelled = false;
    // Rendered through <img> from a blob URL, so nothing inside the SVG can run.
    outputText(projectId, board.id, page.svg)
      .then((text) => {
        url = URL.createObjectURL(new Blob([cropSvg(text, page.bbox_mm)], { type: "image/svg+xml" }));
        if (!cancelled) setSvgUrl(url);
      })
      .catch(() => setSvgUrl(null));
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [projectId, board.id, page?.svg, page?.bbox_mm, manifest]);

  const generate = async (force = false) => {
    setFailure(null);
    let started: Job;
    try {
      started = await api.exportBoard(projectId, board.id, force);
    } catch (e) {
      if (e instanceof ApiError && e.body.error_code === "EXTERNAL_MODIFICATION") {
        const files = ((e.body.details as { paths?: string[] })?.paths ?? []).join(", ");
        const ok = window.confirm(
          `생성된 파일이 앱 밖에서 수정되었습니다: ${files}\n\n다시 생성하면 이 수정 내용을 덮어씁니다. 계속할까요?`,
        );
        if (ok) return generate(true);
        return;
      }
      setFailure(e instanceof ApiError ? e.body : errorText(e));
      return;
    }
    setJob(started);
    const done = await waitForJob(started.id, setJob).catch((e) => {
      setFailure(errorText(e));
      return null;
    });
    if (done?.status === "failed") setFailure(done.error);
    await onChanged();
  };

  const check = manifest?.automated_check;
  const label = automatedLabel(check?.status);
  // Fixes start from the saved circuit, so they are offered only for results of the current revision.
  const current = Boolean(outputs && manifest && !outputs.stale && manifest.source_revision === revision);
  const circuitProblems = (check?.violations ?? []).some((v) => ERC_EXPLAIN[String(v.type)]?.kind !== "setup")
    || (manifest?.warnings ?? []).length > 0;
  const fixed = async (id: string, how: string) => {
    setRevising(false);
    setFixMessage({ text: `${how} 제안 #${Number(id)}을 만들었습니다. 「제안」 탭에서 비교하고 승인하면 반영됩니다.`, proposal: id });
    await onChanged();
  };
  const failureIssues = typeof failure === "object" && failure ? ((failure.details as { issues?: Issue[] })?.issues ?? []) : [];

  return (
    <div className="schematic-layout">
      <div className="side">
        <div className="card">
          <h3>생성</h3>
          <p className="small">
            KiCad:{" "}
            {system?.kicad.available ? (
              <span className="ok-text">
                {system.kicad.version} <span className="muted">({system.kicad.runner})</span>
              </span>
            ) : (
              <span className="warn-text">kicad-cli 없음 — 회로도 파일은 만들지만 ERC·Netlist·SVG 검사는 하지 않습니다(Not checked).</span>
            )}
          </p>
          <button onClick={() => generate(false)} disabled={!revision || running}>
            {running ? "생성 중…" : manifest ? "다시 생성" : "회로도 생성"}
          </button>
          {!revision && <p className="muted small">먼저 회로 (IR) 탭에서 회로를 저장하세요.</p>}
          {running && <p className="small" role="status">작업 {job?.status === "queued" ? "대기 중" : "실행 중"}…</p>}
          {failure && (
            <div className="banner banner-bad" role="alert">
              {typeof failure === "string" ? failure : `[${failure.error_code}] ${failure.message}`}
              {typeof failure === "object" && failure.recovery_hint && <div className="small">→ {failure.recovery_hint}</div>}
              <IssueList issues={failureIssues} />
            </div>
          )}
        </div>

        {outputs && manifest && (
          <div className="card">
            <h3>자동 검사</h3>
            <Chip text={label.text} tone={label.tone} />
            {outputs.stale && (
              <div className="banner banner-warn">
                현재 revision {outputs.current_revision}과 다른 revision {manifest.source_revision}에서 생성된 결과입니다. 다시 생성하세요.
              </div>
            )}
            {outputs.externally_modified.length > 0 && (
              <div className="banner banner-warn">
                앱 밖에서 수정됨: {outputs.externally_modified.join(", ")}. 다시 생성하면 덮어씁니다. KiCad에서 고친 것이라면
                아래 「KiCad에서 고친 것 가져오기」로 회로에 반영하세요.
              </div>
            )}
            {outputs.missing.length > 0 && <div className="banner banner-warn">없어진 파일: {outputs.missing.join(", ")}</div>}
            <dl className="facts">
              <dt>revision</dt>
              <dd>{manifest.source_revision}</dd>
              <dt>생성 시각</dt>
              <dd>{formatTime(manifest.generated_at)}</dd>
              {check?.kicad_version && (
                <>
                  <dt>KiCad</dt>
                  <dd>{check.kicad_version}</dd>
                </>
              )}
              {check?.erc_errors !== null && check?.erc_errors !== undefined && (
                <>
                  <dt>ERC</dt>
                  <dd>
                    오류 {check.erc_errors}, {ercWarningText(check.erc_warnings, check.violations)}
                  </dd>
                </>
              )}
              {check?.netlist_matches_ir !== null && check?.netlist_matches_ir !== undefined && (
                <>
                  <dt>Netlist ↔ IR</dt>
                  <dd className={check.netlist_matches_ir ? "ok-text" : "bad-text"}>
                    {check.netlist_matches_ir ? "일치" : "불일치"}
                  </dd>
                </>
              )}
            </dl>
            {check?.reason && <p className="muted small">{check.reason}</p>}
            {check && check.violations.length > 0 && (
              <ErcViolations violations={check.violations}
                refresh={{ label: "다시 생성해 설정 경고 없애기", run: () => void generate(false), disabled: running || !revision }} />
            )}
            {current && revision && (
              <QuickFixPanel projectId={projectId} boardId={board.id} proposalId={null} checkedAt={manifest!.generated_at}
                busy={running || layoutBusy} onFixed={(id) => fixed(id, "떠 있는 입력 핀을 고친")} />
            )}
            {current && circuitProblems && (
              <div className="toolbar">
                <button className={revising ? "small-btn" : "ghost small-btn"} onClick={() => setRevising(!revising)}
                  disabled={!aiReady || running} title={aiReady ? "검사에서 나온 문제를 AI가 고친 새 제안을 만듭니다(승인 전에는 회로가 바뀌지 않음)" : AI_SETUP_HINT}>
                  AI로 검사 문제 고치기
                </button>
                {!aiReady && <span className="small muted">{AI_SETUP_HINT}</span>}
              </div>
            )}
            {revising && (
              <RevisionPanel projectId={projectId} boardId={board.id} proposalId={null}
                provider={ai?.settings.provider ?? "gemini"} model={ai?.settings.model ?? ""} preset={{ checks: true }}
                onCancel={() => setRevising(false)} onCreated={(id) => fixed(id, "검사 문제를 AI가 고친")} />
            )}
            {!current && check && check.violations.length > 0 && (
              <p className="muted small">지금 회로와 다른 revision의 결과입니다. 다시 생성한 뒤 고치기 버튼을 쓸 수 있습니다.</p>
            )}
            {fixMessage && (
              <p className="small" role="status">
                {fixMessage.text}{" "}
                {fixMessage.proposal && onOpenProposal && (
                  <button className="link" onClick={() => onOpenProposal(fixMessage.proposal!)}>제안 #{Number(fixMessage.proposal)} 열기</button>
                )}
              </p>
            )}
            {check?.netlist_diff && <pre className="small">{check.netlist_diff}</pre>}
            <IssueList issues={manifest.warnings} />
            <p className="muted small">자동 검사(ERC, Netlist 비교)는 전기적 설계 검토가 아닙니다.</p>
          </div>
        )}

        {outputs && manifest && (
          <div className="card">
            <h3>KiCad에서 고친 것 가져오기</h3>
            <p className="small">
              생성된 회로도를 KiCad에서 고치고 저장한 뒤 가져오면 제안으로 만들어집니다. 승인하면 회로에 들어가고 회로도를 다시 그립니다.
            </p>
            <ul className="small">
              <li>
                <b>고친 것 모두</b>: 바꾼 연결, 값·풋프린트, 추가·삭제한 부품, 옮긴 부품의 위치. 연결은 kicad-cli로 KiCad가 읽은 대로
                가져옵니다(KiCad 필요).
              </li>
              <li>
                <b>배치만</b>: 모든 부품의 위치·회전·반전을 고정합니다. 배선·라벨·전원 심볼은 다시 그립니다.
              </li>
            </ul>
            <p className="muted small">고정된 부품: {fixedParts}개 {fixedParts === 0 && "(모두 자동 배치)"}</p>
            <div className="toolbar">
              <button onClick={() => layoutAction("edits")} disabled={layoutBusy || running}>
                고친 것 모두 가져오기 (제안)
              </button>
              <button className="ghost" onClick={() => layoutAction("import")} disabled={layoutBusy || running}>
                KiCad 배치 가져오기 (제안)
              </button>
              {fixedParts > 0 && (
                <button className="ghost" onClick={() => layoutAction("reset")} disabled={layoutBusy || running}>
                  자동 배치로 되돌리기
                </button>
              )}
            </div>
            {layoutMessage && <p className="small">{layoutMessage}</p>}
            {layoutProposal && onOpenProposal && (
              <button className="ghost" onClick={() => onOpenProposal(layoutProposal)}>
                제안 #{Number(layoutProposal)} 열기
              </button>
            )}
          </div>
        )}
        {outputs && manifest && (
          <div className="card">
            <h3>산출물</h3>
            <p className="muted small truncate" title={outputs.path}>
              {outputs.path}
            </p>
            {desktop && (
              <div className="toolbar">
                <button className="ghost" onClick={() => desktop?.openPath(outputs.path)}>
                  폴더 열기
                </button>
                <button className="ghost" onClick={() => desktop?.openPath(`${outputs.path}/${manifest.kicad_project}.kicad_pro`)}>
                  KiCad에서 열기
                </button>
              </div>
            )}
            <table className="table">
              <thead>
                <tr>
                  <th>파일</th>
                  <th>유형</th>
                  <th>SHA-256</th>
                </tr>
              </thead>
              <tbody>
                {manifest.artifacts.map((a) => (
                  <tr key={a.path}>
                    <td>{a.path}</td>
                    <td>{a.artifact_type}</td>
                    <td>
                      <code title={a.checksum}>{shortHash(a.checksum)}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted small">KiCad에서는 .kicad_sch가 아니라 {manifest.kicad_project}.kicad_pro를 여세요.</p>
          </div>
        )}
      </div>

      <div className="panel preview">
        <div className="toolbar">
          <strong>미리보기</strong>
          {pages.length > 1 && (
            <select aria-label="페이지 선택" value={pageIndex} onChange={(e) => setPageIndex(Number(e.target.value))}>
              {pages.map((p, i) => (
                <option key={p.sheet_id} value={i}>
                  {p.number}/{pages.length} · {p.name} ({p.paper})
                </option>
              ))}
            </select>
          )}
          {ZOOMS.map((z) => (
            <button key={z.id} className={`ghost ${zoom === z.id ? "active" : ""}`} onClick={() => setZoom(z.id)}>
              {z.label}
            </button>
          ))}
        </div>
        <div className="preview-area">
          {svgUrl ? (
            <img
              src={svgUrl}
              alt="생성된 회로도 미리보기"
              className={`preview-img ${zoom === "fit" ? "fit" : ""}`}
              style={zoom === "fit" ? undefined : { width: `${Number(zoom) * (page?.bbox_mm?.[2] ?? 297)}mm` }}
            />
          ) : (
            <div className="empty small">
              {manifest ? "SVG 미리보기가 없습니다 (kicad-cli가 있어야 만들어집니다)." : "아직 생성하지 않았습니다."}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
