import { useEffect, useState } from "react";

import { api, errorText } from "../api";
import type { DatasheetSearchKeys } from "../types";

const SOURCE_TEXT: Record<string, string> = { keyring: "보안 저장소", env: "환경 변수", mixed: "보안 저장소·환경 변수" };

/** Keys for the distributor searches that find datasheet links by part number (Mouser, DigiKey). */
export function DatasheetSearchSettings() {
  const [keys, setKeys] = useState<DatasheetSearchKeys | null>(null);
  const [mouser, setMouser] = useState("");
  const [dkId, setDkId] = useState("");
  const [dkSecret, setDkSecret] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api.datasheetSearchKeys().then(setKeys).catch((e) => setMessage(errorText(e)));
  }, []);

  const act = async (fn: () => Promise<DatasheetSearchKeys>, done: string) => {
    setMessage(null);
    try {
      setKeys(await fn());
      setMessage(done);
    } catch (e) {
      setMessage(errorText(e));
    }
  };

  const state = (k: { configured: boolean; source: string | null } | undefined) =>
    k?.configured ? <span className="ok-text">설정됨 ({SOURCE_TEXT[k.source ?? ""] ?? k.source})</span> : <span className="muted">없음</span>;

  return (
    <div className="card">
      <h3>데이터시트 검색 (Mouser · DigiKey)</h3>
      <p className="small">
        유통사 검색 API로 부품 품번의 데이터시트 링크를 찾습니다. 키가 없어도 KiCad 심볼 링크와 제조사 주소 규칙(TI, ST, onsemi, Diodes, Nexperia)으로 받을 수
        있습니다. 검색할 때 보내는 것은 품번뿐입니다. 키는 각 사이트에서 무료로 발급받습니다: Mouser는 API Hub의 Search API 키, DigiKey는 API Portal에서 만든
        앱(Product Information v4)의 Client ID·Secret.
      </p>
      <h4>Mouser {state(keys?.mouser)}</h4>
      <form
        className="toolbar"
        onSubmit={(e) => {
          e.preventDefault();
          void act(() => api.putDatasheetSearchKey("mouser", { api_key: mouser.trim() }), "Mouser 키를 저장했습니다.").then(() => setMouser(""));
        }}
      >
        <input type="password" aria-label="Mouser API 키" placeholder="Mouser Search API 키" value={mouser} autoComplete="off"
          onChange={(e) => setMouser(e.target.value)} style={{ minWidth: 280 }} />
        <button type="submit" disabled={!mouser.trim() || keys?.keyring_available === false}>저장</button>
        {keys?.mouser.configured && keys.mouser.source !== "env" && (
          <button type="button" className="ghost" onClick={() => act(() => api.deleteDatasheetSearchKey("mouser"), "Mouser 키를 지웠습니다.")}>
            지우기
          </button>
        )}
      </form>
      <h4>DigiKey {state(keys?.digikey)}</h4>
      <form
        className="toolbar"
        onSubmit={(e) => {
          e.preventDefault();
          void act(
            () => api.putDatasheetSearchKey("digikey", { client_id: dkId.trim(), client_secret: dkSecret.trim() }),
            "DigiKey 키를 저장했습니다.",
          ).then(() => {
            setDkId("");
            setDkSecret("");
          });
        }}
      >
        <input aria-label="DigiKey Client ID" placeholder="Client ID" value={dkId} autoComplete="off" onChange={(e) => setDkId(e.target.value)} />
        <input type="password" aria-label="DigiKey Client Secret" placeholder="Client Secret" value={dkSecret} autoComplete="off"
          onChange={(e) => setDkSecret(e.target.value)} />
        <button type="submit" disabled={!dkId.trim() || !dkSecret.trim() || keys?.keyring_available === false}>저장</button>
        {keys?.digikey.configured && keys.digikey.source !== "env" && (
          <button type="button" className="ghost" onClick={() => act(() => api.deleteDatasheetSearchKey("digikey"), "DigiKey 키를 지웠습니다.")}>
            지우기
          </button>
        )}
      </form>
      <p className="muted small">
        키는 Windows 자격 증명 관리자(macOS 키체인)에만 저장되고, 프로젝트 파일·로그에는 남지 않습니다.
        {keys?.keyring_available === false && " 이 컴퓨터에서는 보안 저장소를 쓸 수 없습니다. 환경 변수 MOUSER_API_KEY 또는 DIGIKEY_CLIENT_ID·DIGIKEY_CLIENT_SECRET을 지정하고 앱을 다시 시작하세요."}
      </p>
      {message && (
        <p className="small" role="status">
          {message}
        </p>
      )}
    </div>
  );
}
