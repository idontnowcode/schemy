import { useEffect, useState } from "react";

import { api, errorText } from "../api";
import type { AiStatus, SystemInfo } from "../types";

export function SettingsPage({ system, onError }: { system: SystemInfo | null; onError(message: string | null): void }) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [key, setKey] = useState("");
  const [model, setModel] = useState("");
  const [models, setModels] = useState<{ name: string; display_name: string | null }[]>([]);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api
      .aiStatus()
      .then((s) => {
        setStatus(s);
        setModel(s.settings.model);
      })
      .catch((e) => onError(errorText(e)));
  }, [onError]);

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
          켜면 「PDF 가져오기 (AI)」와 「AI 대체 추천」을 쓸 수 있습니다. 기본 AI는 Google Gemini API입니다. 끈 상태에서도 나머지 기능은 모두
          동작하며, 코파일럿으로 만든 회로도 Markdown은 AI 없이 가져올 수 있습니다.
        </p>
        <label className="switch">
          <input type="checkbox" checked={status.settings.enabled} onChange={toggle} aria-label="AI 기능 사용" /> AI 기능 사용
        </label>
        <p className="muted small">
          AI를 부를 때마다 무엇을 보내는지 먼저 보여주고 확인을 받습니다. 호출 기록(시각, 모델, 파일 크기·해시, 사용 토큰)은 보드 폴더의
          ai_runs/에 남고, API 키는 기록하지 않습니다.
        </p>
      </div>

      <div className="card">
        <h3>Gemini API 키</h3>
        <p className="small">
          상태:{" "}
          {k.configured ? (
            <span className="ok-text">설정됨 ({k.source === "env" ? "환경 변수 GEMINI_API_KEY" : "OS 보안 저장소"})</span>
          ) : (
            <span className="warn-text">없음</span>
          )}
        </p>
        {k.keyring_available ? (
          <div className="toolbar">
            <input
              type="password"
              aria-label="Gemini API 키"
              placeholder="Google AI Studio에서 발급한 키"
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
            이 컴퓨터에서는 OS 보안 저장소를 쓸 수 없습니다. 환경 변수 GEMINI_API_KEY로 키를 지정한 뒤 앱을 다시 시작하세요.
          </p>
        )}
        <p className="muted small">키는 Windows 자격 증명 관리자(macOS 키체인)에만 저장되고, 프로젝트 파일·로그에는 남지 않습니다.</p>
      </div>

      <div className="card">
        <h3>모델</h3>
        <div className="toolbar">
          <input aria-label="Gemini 모델" value={model} onChange={(e) => setModel(e.target.value)} list="gemini-models" style={{ minWidth: 260 }} />
          <datalist id="gemini-models">
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
          기본값 gemini-flash-latest는 Google이 최신 Flash 모델로 이어 주는 이름입니다. 회로가 복잡해 PDF 변환이 부정확하면 목록에서 Pro 계열을
          골라 보세요.
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
