import { useCallback, useEffect, useState } from "react";

import { api, errorText } from "../api";
import { providerLabel } from "../format";
import type { AiProvider, AiStatus, ClaudeLoginStatus, SystemInfo } from "../types";

export function SettingsPage({ system, onError }: { system: SystemInfo | null; onError(message: string | null): void }) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [key, setKey] = useState("");
  const [model, setModel] = useState("");
  const [models, setModels] = useState<{ name: string; display_name: string | null }[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [login, setLogin] = useState<ClaudeLoginStatus | null>(null);

  const refresh = useCallback(async () => {
    const s = await api.aiStatus();
    setStatus(s);
    setModel(s.settings.model);
    return s;
  }, []);

  useEffect(() => {
    refresh().catch((e) => onError(errorText(e)));
  }, [onError, refresh]);

  const provider = status?.settings.provider;
  useEffect(() => {
    if (provider !== "claude") return;
    api.claudeLogin().then(setLogin).catch(() => setLogin(null));
  }, [provider]);

  // While `ant auth login` waits for the browser, poll until it finishes.
  useEffect(() => {
    if (!login?.running) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await api.claudeLogin();
        setLogin(next);
        if (!next.running) {
          await refresh();
          setMessage(next.logged_in ? "Claude에 로그인했습니다." : next.last_error ?? "로그인하지 못했습니다.");
        }
      } catch (e) {
        setMessage(errorText(e));
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [login?.running, refresh]);

  const run = async (fn: () => Promise<unknown>) => {
    setMessage(null);
    try {
      await fn();
    } catch (e) {
      setMessage(errorText(e));
    }
  };

  const toggle = () =>
    run(async () => {
      setStatus(await api.saveAiSettings({ enabled: !status!.settings.enabled }));
    });

  const choose = (next: AiProvider) =>
    run(async () => {
      const s = await api.saveAiSettings({ provider: next });
      setStatus(s);
      setModel(s.settings.model);
      setModels([]);
      setKey("");
    });

  const saveKey = () =>
    run(async () => {
      const r = await api.saveAiKey(key);
      setStatus((s) => (s ? { ...s, key: r.key } : s));
      setKey("");
      setMessage("API 키를 OS 보안 저장소에 저장했습니다.");
    });

  const deleteKey = () =>
    run(async () => {
      const r = await api.deleteAiKey();
      setStatus((s) => (s ? { ...s, key: r.key } : s));
    });

  const startLogin = () =>
    run(async () => {
      setLogin(await api.startClaudeLogin());
      setMessage("브라우저에서 Claude Console 계정으로 로그인하세요. 끝나면 여기에 자동으로 반영됩니다.");
    });

  const logout = () =>
    run(async () => {
      setLogin(await api.claudeLogout());
      await refresh();
    });

  const saveModel = () =>
    run(async () => {
      setStatus(await api.saveAiSettings({ model: model.trim() }));
      setMessage("모델을 저장했습니다.");
    });

  const loadModels = () =>
    run(async () => {
      setModels((await api.aiModels()).models);
    });

  if (!status) return <div className="empty">불러오는 중…</div>;
  const k = status.key;
  const claude = status.settings.provider === "claude";
  const envVar = claude ? "ANTHROPIC_API_KEY" : "GEMINI_API_KEY";
  const sourceText =
    k.source === "env"
      ? `환경 변수 ${envVar}`
      : k.source === "login"
        ? `Claude 로그인${k.login?.profile ? ` (프로필 ${k.login.profile})` : ""}`
        : "OS 보안 저장소";

  return (
    <section className="project">
      <header className="project-header">
        <div className="project-title">
          <h1>설정</h1>
        </div>
      </header>

      <div className="card">
        <h3>AI 기능 (선택)</h3>
        <p className="small">
          켜면 AI 회로 설계·검토, 「PDF 가져오기 (AI)」, 「AI 대체 추천」, 데이터시트 요약을 쓸 수 있습니다. AI는 Google Gemini 또는 Anthropic
          Claude 중에서 고릅니다. 끈 상태에서도 나머지 기능은 모두 동작하며, 코파일럿으로 만든 회로도 Markdown은 AI 없이 가져올 수 있습니다.
        </p>
        <label className="switch">
          <input type="checkbox" checked={status.settings.enabled} onChange={toggle} aria-label="AI 기능 사용" /> AI 기능 사용
        </label>
        <p className="muted small">
          AI를 부를 때마다 무엇을 보내는지 먼저 보여주고 확인을 받습니다. 호출 기록(시각, 모델, 파일 크기·해시, 사용 토큰)은 보드 폴더의
          ai_runs/에 남고, API 키나 로그인 토큰은 기록하지 않습니다.
        </p>
        <div className="toolbar" role="radiogroup" aria-label="AI 공급자">
          {(["gemini", "claude"] as AiProvider[]).map((p) => (
            <label key={p} className="small">
              <input type="radio" name="ai-provider" checked={status.settings.provider === p} onChange={() => choose(p)} />{" "}
              {providerLabel(p)}
            </label>
          ))}
        </div>
      </div>

      <div className="card">
        <h3>{claude ? "Claude 연결" : "Gemini API 키"}</h3>
        <p className="small">
          상태: {k.configured ? <span className="ok-text">설정됨 ({sourceText})</span> : <span className="warn-text">없음</span>}
        </p>

        {claude && (
          <>
            <h4>로그인 (Anthropic CLI)</h4>
            <p className="small">
              API 키 대신 Claude Console 계정으로 로그인합니다. 버튼을 누르면 Anthropic CLI(<code>ant auth login</code>)가 브라우저를 열고, 로그인
              정보는 Anthropic CLI의 폴더에 저장됩니다. 이 앱은 그 토큰을 읽거나 기록하지 않습니다.
            </p>
            <div className="toolbar">
              <button onClick={startLogin} disabled={login?.running || login?.cli_found === false}>
                {login?.running ? "브라우저에서 로그인하는 중…" : login?.logged_in ? "다시 로그인" : "Claude 로그인"}
              </button>
              {login?.logged_in && login.source === "ant_login" && (
                <button className="ghost" onClick={logout}>
                  로그아웃
                </button>
              )}
              {login && (
                <span className="small">
                  {login.logged_in ? (
                    <span className="ok-text">로그인됨{login.profile ? ` (프로필 ${login.profile})` : ""}</span>
                  ) : (
                    <span className="muted">로그인 안 됨</span>
                  )}
                </span>
              )}
            </div>
            {login?.cli_found === false && <p className="small warn-text">{login.install_hint}</p>}
            {login?.last_error && !login.running && <p className="small warn-text">{login.last_error}</p>}
            <details className="small">
              <summary>로그인과 요금 안내</summary>
              <ul>
                <li>
                  Claude Console(console.anthropic.com) 계정으로 로그인하며, 사용량은 그 계정의 <b>API 크레딧</b>으로 과금됩니다. claude.ai 구독
                  (Pro/Max) 로그인은 Anthropic 정책상 다른 앱에서 쓸 수 없습니다.
                </li>
                <li>환경 변수 ANTHROPIC_API_KEY가 설정되어 있으면 로그인보다 우선합니다. 로그인을 쓰려면 그 변수를 지우세요.</li>
                <li>로그인이 만료되면(오래 쓰지 않으면) 「다시 로그인」을 누르세요.</li>
                <li>회사 도면·BOM·요구사항을 보낸다면 사내 규정을 먼저 확인하세요.</li>
              </ul>
            </details>
            <h4>또는 API 키</h4>
          </>
        )}

        {k.keyring_available ? (
          <div className="toolbar">
            <input
              type="password"
              aria-label={claude ? "Claude API 키" : "Gemini API 키"}
              placeholder={claude ? "Claude Console에서 발급한 키 (sk-ant-…)" : "Google AI Studio에서 발급한 키"}
              value={key}
              onChange={(e) => setKey(e.target.value)}
              autoComplete="off"
              style={{ minWidth: 320 }}
            />
            <button onClick={saveKey} disabled={key.trim().length < 10}>
              저장
            </button>
            {k.configured && k.source === "keyring" && (
              <button className="ghost" onClick={deleteKey}>
                삭제
              </button>
            )}
          </div>
        ) : (
          <p className="small warn-text">
            이 컴퓨터에서는 OS 보안 저장소를 쓸 수 없습니다. 환경 변수 {envVar}로 키를 지정한 뒤 앱을 다시 시작하세요.
          </p>
        )}
        <p className="muted small">키는 Windows 자격 증명 관리자(macOS 키체인)에만 저장되고, 프로젝트 파일·로그에는 남지 않습니다.</p>
        {!claude && (
          <details className="small">
            <summary>무료로 쓰려면 / 요금</summary>
            <ul>
              <li>
                요금은 모델이 아니라 <b>키를 만든 Google 프로젝트</b>에 따라 정해집니다. 결제(Billing)가 연결되지 않은 프로젝트의 키는 무료
                등급으로 동작하고, 분당·하루 요청 수가 제한됩니다.
              </li>
              <li>결제가 연결된 프로젝트의 키는 쓴 만큼 과금됩니다. 선불 크레딧으로 설정했다면 크레딧이 바닥날 때 요청이 거부됩니다.</li>
              <li>
                Google 약관상 무료 등급에서는 보낸 내용이 Google 서비스 개선에 쓰일 수 있습니다. 회사 도면·BOM·요구사항을 보낸다면 사내 규정을 먼저
                확인하세요.
              </li>
              <li>AI 회로 설계는 한 번에 3~5회 요청합니다. 무료 등급 한도에 자주 걸리면 「설계 후 AI 검토」를 끄세요.</li>
            </ul>
          </details>
        )}
      </div>

      <div className="card">
        <h3>모델</h3>
        <div className="toolbar">
          <input aria-label="AI 모델" value={model} onChange={(e) => setModel(e.target.value)} list="ai-models" style={{ minWidth: 260 }} />
          <datalist id="ai-models">
            {models.map((m) => (
              <option key={m.name} value={m.name}>
                {m.display_name ?? m.name}
              </option>
            ))}
          </datalist>
          <button onClick={saveModel} disabled={!model.trim() || model.trim() === status.settings.model}>
            저장
          </button>
          <button className="ghost" onClick={loadModels} disabled={!k.configured}>
            모델 목록 불러오기
          </button>
        </div>
        <p className="muted small">
          {claude
            ? "기본값 claude-opus-5-5는 Anthropic의 가장 성능 좋은 모델입니다. 비용을 줄이려면 목록에서 claude-sonnet-5-5를 골라 보세요."
            : "기본값 gemini-flash-latest는 Google이 최신 Flash 모델로 이어 주는 이름입니다. 회로가 복잡해 PDF 변환이 부정확하면 목록에서 Pro 계열을 골라 보세요."}
        </p>
        {models.length > 0 && <p className="small">사용 가능한 모델 {models.length}개를 불러왔습니다. 입력란에서 고르세요.</p>}
        {message && (
          <p className="small" role="status">
            {message}
          </p>
        )}
      </div>

      {system && (
        <div className="card">
          <h3>환경</h3>
          <dl className="facts">
            <dt>앱 버전</dt>
            <dd>{system.app_version}</dd>
            <dt>워크스페이스</dt>
            <dd>{system.workspace}</dd>
            <dt>KiCad</dt>
            <dd>{system.kicad.available ? `${system.kicad.version} (${system.kicad.runner})` : "없음"}</dd>
            <dt>심볼 라이브러리</dt>
            <dd>{system.symbol_dirs.join(", ") || "없음"}</dd>
          </dl>
        </div>
      )}
    </section>
  );
}
