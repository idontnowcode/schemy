import { useCallback, useEffect, useState } from "react";

import { api, errorText } from "../api";

interface Props {
  projectId: string;
  onSaved(): Promise<unknown>;
}

export function RequirementsTab({ projectId, onSaved }: Props) {
  const [text, setText] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api
      .requirements(projectId)
      .then((r) => {
        setText(r.text);
        setSaved(r.text);
      })
      .catch((e) => setMessage(errorText(e)));
  }, [projectId]);

  const dirty = saved !== null && text !== saved;

  const save = useCallback(async () => {
    try {
      await api.saveRequirements(projectId, text);
      setSaved(text);
      setMessage("저장했습니다.");
      await onSaved();
    } catch (e) {
      setMessage(errorText(e));
    }
  }, [projectId, text, onSaved]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") {
      e.preventDefault();
      if (dirty) void save();
    }
  };

  return (
    <div className="panel">
      <div className="toolbar">
        <button onClick={save} disabled={!dirty}>
          저장
        </button>
        <span className="muted small">{dirty ? "저장되지 않은 변경이 있습니다 (Ctrl+S)" : message ?? "requirements.md"}</span>
      </div>
      <p className="muted small">
        자유 형식의 Markdown입니다. 필드형 사양과 AI 구조화는 Phase 2에서 추가됩니다.
      </p>
      <textarea
        className="editor"
        aria-label="요구사항"
        spellCheck={false}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
      />
    </div>
  );
}
