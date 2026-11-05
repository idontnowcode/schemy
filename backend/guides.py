"""Workspace design guide (``<workspace>/guides/``): the entries and the full history of how they changed.

    guides/
      guides.json      the entries (built-in baseline + what people and reviews added)
      history.jsonl    one line per event: created, edited, adopted, retired, candidate, deleted, cited, issue
      ai_runs/         one record per AI call that drafted a guide (no keys)

Like the datasheet library, the guide belongs to the workspace, not to a project: what is learned on one
board applies to the next. Text that came from the AI (a review finding, a drafted guide) is always saved as
a *candidate*; only a person adopts it. The history is append-only, so it doubles as a record of feedback
and lessons over time.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from circuit_core.guides import SEED, SEED_VERSION, GuideDraft, GuideEntry, GuideOrigin, GuideStatus

from .errors import ApiError
from .store import atomic_write, now_iso

AI_TEXT_ORIGINS = {"ai_review"}
"""Origins whose text the AI wrote: they can only be saved as candidates."""


class GuideLibrary:
    def __init__(self, workspace: Path):
        self.dir = Path(workspace) / "guides"
        self.path = self.dir / "guides.json"
        self.history_path = self.dir / "history.jsonl"
        self._lock = threading.RLock()

    # --- storage -------------------------------------------------------------------------------

    def _load_raw(self) -> dict:
        if not self.path.is_file():
            return {"schema_version": 1, "seed_version": 0, "entries": []}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            return {"schema_version": 1, "seed_version": 0, "entries": []}

    def _save(self, raw: dict) -> None:
        atomic_write(self.path, json.dumps(raw, ensure_ascii=False, indent=2) + "\n")

    def _log(self, event: str, entry_id: str | None, by: str = "", **data) -> None:
        line = {"at": now_iso(), "event": event, "id": entry_id, "by": by, **data}
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    def _with_seed(self, raw: dict) -> dict:
        """Add built-in entries this workspace does not have yet (never overwrites a person's edits)."""
        if raw.get("seed_version", 0) >= SEED_VERSION:
            return raw
        have = {e["id"] for e in raw["entries"]}
        now = now_iso()
        added, updated = [], []
        # Built-in entries nobody has edited or decided on take the new app version's text.
        seeds = {s["id"]: s for s in SEED}
        for i, e in enumerate(raw["entries"]):
            s = seeds.get(e["id"])
            if s and e.get("origin") == "seed" and e.get("version", 1) == 1 and e.get("decided_by") == "앱 기본":
                fresh = GuideEntry.model_validate({**e, **s, "seed_version": SEED_VERSION}).model_dump(mode="json")
                if any(fresh[k] != e.get(k) for k in s):
                    updated.append(e["id"])  # the text changed, not just the version stamp
                raw["entries"][i] = fresh
        for s in SEED:
            if s["id"] in have:
                continue
            entry = GuideEntry(**s, status="adopted", origin="seed", created_at=now, created_by="앱 기본",
                               updated_at=now, decided_by="앱 기본", decided_at=now, seed_version=SEED_VERSION)
            raw["entries"].append(entry.model_dump(mode="json"))
            added.append(s["id"])
        raw["seed_version"] = SEED_VERSION
        self._save(raw)
        if added:
            self._log("seeded", None, "앱 기본", ids=added, seed_version=SEED_VERSION)
        if updated:
            self._log("seed_updated", None, "앱 기본", ids=updated, seed_version=SEED_VERSION)
        return raw

    def entries(self) -> list[GuideEntry]:
        with self._lock:
            raw = self._with_seed(self._load_raw())
            return [GuideEntry.model_validate(e) for e in raw["entries"]]

    def get(self, entry_id: str) -> GuideEntry:
        for e in self.entries():
            if e.id == entry_id:
                return e
        raise ApiError(404, "GUIDE_NOT_FOUND", f"지침 '{entry_id}'가 없습니다.")

    def _replace(self, entry: GuideEntry) -> None:
        raw = self._with_seed(self._load_raw())
        raw["entries"] = [entry.model_dump(mode="json") if e["id"] == entry.id else e for e in raw["entries"]]
        self._save(raw)

    def _next_id(self, raw: dict) -> str:
        nums = [int(m.group(1)) for e in raw["entries"] if (m := re.fullmatch(r"G-(\d{4})", e["id"]))]
        return f"G-{(max(nums) + 1) if nums else 1:04d}"

    # --- changes -------------------------------------------------------------------------------

    def create(self, draft: GuideDraft, *, origin: GuideOrigin, by: str, adopt: bool = False,
               ai_text: bool = False) -> GuideEntry:
        """Add an entry. It is adopted at once only when a person wrote it and asked to adopt it."""
        if origin == "seed":
            raise ApiError(422, "INVALID_ORIGIN", "앱 기본 지침은 직접 만들 수 없습니다.")
        candidate_only = ai_text or origin in AI_TEXT_ORIGINS
        status: GuideStatus = "adopted" if adopt and not candidate_only else "candidate"
        with self._lock:
            raw = self._with_seed(self._load_raw())
            now = now_iso()
            adopted = status == "adopted"
            entry = GuideEntry(**draft.model_dump(), id=self._next_id(raw), status=status, origin=origin,
                               created_at=now, created_by=by, updated_at=now, decided_by=by if adopted else None,
                               decided_at=now if adopted else None)
            raw["entries"].append(entry.model_dump(mode="json"))
            self._save(raw)
            self._log("created", entry.id, by, origin=origin, status=status, ai_text=candidate_only,
                      entry=entry.model_dump(mode="json"))
            return entry

    def update(self, entry_id: str, draft: GuideDraft, *, by: str) -> GuideEntry:
        with self._lock:
            old = self.get(entry_id)
            new = GuideEntry.model_validate({**old.model_dump(), **draft.model_dump(), "version": old.version + 1,
                                             "updated_at": now_iso()})
            self._replace(new)
            before, after = old.model_dump(mode="json"), new.model_dump(mode="json")
            changed = {k: {"before": before[k], "after": after[k]} for k in GuideDraft.model_fields
                       if before[k] != after[k]}
            self._log("edited", entry_id, by, version=new.version, changes=changed)
            return new

    def decide(self, entry_id: str, status: GuideStatus, *, by: str, note: str | None = None) -> GuideEntry:
        with self._lock:
            old = self.get(entry_id)
            new = old.model_copy(update={"status": status, "decided_by": by, "decided_at": now_iso(), "note": note,
                                         "updated_at": now_iso()})
            self._replace(new)
            event = {"adopted": "adopted", "retired": "retired", "candidate": "reopened"}[status]
            self._log(event, entry_id, by, note=note, version=new.version)
            return new

    def delete(self, entry_id: str, *, by: str) -> None:
        with self._lock:
            entry = self.get(entry_id)
            if entry.status != "candidate" or entry.origin == "seed":
                raise ApiError(409, "GUIDE_NOT_DELETABLE", "채택된 지침과 앱 기본 지침은 지우지 않고 「폐기」합니다.",
                               recovery_hint="폐기하면 쓰이지 않지만 이력은 남습니다.")
            raw = self._load_raw()
            raw["entries"] = [e for e in raw["entries"] if e["id"] != entry_id]
            self._save(raw)
            self._log("deleted", entry_id, by, entry=entry.model_dump(mode="json"))

    def record(self, event: str, entry_ids: list[str], *, by: str = "", **data) -> None:
        """Usage events: 'cited' (the AI said it followed these), 'flagged' (an AI review said one was not
        followed), 'issue' (an engineer's review recorded one not followed)."""
        known = {e.id for e in self.entries()}
        for entry_id in entry_ids:
            if entry_id in known:
                self._log(event, entry_id, by, **data)

    # --- history -------------------------------------------------------------------------------

    def history(self, entry_id: str | None = None, limit: int = 500) -> list[dict]:
        if not self.history_path.is_file():
            return []
        out = []
        for line in self.history_path.read_text(encoding="utf-8").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if entry_id is None or ev.get("id") == entry_id or entry_id in (ev.get("ids") or []):
                out.append(ev)
        return out[-limit:]

    def stats(self) -> dict[str, dict[str, int]]:
        """Per entry: how often the AI said it followed it (cited), how often an AI review said a circuit did not
        (flagged), and how often an engineer's review recorded an issue with it (issue)."""
        out: dict[str, dict[str, int]] = {}
        for ev in self.history():
            if ev.get("event") in ("cited", "flagged", "issue") and ev.get("id"):
                s = out.setdefault(ev["id"], {"cited": 0, "flagged": 0, "issue": 0})
                s[ev["event"]] += 1
        return out

    def save_ai_run(self, record: dict) -> Path:
        path = self.dir / "ai_runs" / f"{record['id']}.json"
        atomic_write(path, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        return path
