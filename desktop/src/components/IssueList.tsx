import type { Issue, SchemaError } from "../types";

interface Props {
  issues?: Issue[];
  schemaErrors?: SchemaError[];
  syntaxError?: string | null;
}

export function IssueList({ issues = [], schemaErrors = [], syntaxError }: Props) {
  if (!syntaxError && issues.length === 0 && schemaErrors.length === 0) return null;
  return (
    <ul className="issues">
      {syntaxError && (
        <li className="issue issue-error">
          <span className="issue-code">JSON_SYNTAX</span> {syntaxError}
        </li>
      )}
      {schemaErrors.map((e, i) => (
        <li key={`s${i}`} className="issue issue-error">
          <span className="issue-code">SCHEMA</span> <code>{e.loc.join(".")}</code>: {e.message}
        </li>
      ))}
      {issues.map((issue, i) => (
        <li key={`i${i}`} className={`issue issue-${issue.severity}`}>
          <span className="issue-code">{issue.error_code}</span>
          {issue.level === "library" && <span className="issue-tag">라이브러리</span>} {issue.message}
          {issue.affected_component_ids.length > 0 && (
            <span className="muted"> ({issue.affected_component_ids.join(", ")})</span>
          )}
          {issue.recovery_hint && <div className="issue-hint">→ {issue.recovery_hint}</div>}
        </li>
      ))}
    </ul>
  );
}
