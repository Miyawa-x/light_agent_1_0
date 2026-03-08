import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "state_memory.json"
SHORT_DIALOGUE_FILE = BASE_DIR / "dialogue_memory.json"
LONG_TERM_FILE = BASE_DIR / "long_term_memory.json"


def _utc_today_str() -> str:
    return date.today().isoformat()


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")


@dataclass
class StateItem:
    key: str
    status: str
    meta: Dict[str, Any] = field(default_factory=dict)


class StateMemory:
    """
    Status-like memory. Meant to be updated frequently by the agent.
    Supports daily-reset items via meta: {"reset": "daily"}.
    Optional quota fields: meta.quota_per_day, item.done_count.
    """

    def __init__(self) -> None:
        self._data = _load_json(STATE_FILE, {"updated_at": "", "items": {}, "last_reset_date": ""})

    def _persist(self) -> None:
        self._data["updated_at"] = datetime.utcnow().isoformat()
        _save_json(STATE_FILE, self._data)

    def reset_daily_if_needed(self) -> bool:
        today = _utc_today_str()
        if self._data.get("last_reset_date") == today:
            return False
        items = self._data.get("items", {})
        changed = False
        for item in items.values():
            meta = item.get("meta", {})
            if meta.get("reset") == "daily":
                if item.get("status") != "pending":
                    item["status"] = "pending"
                    changed = True
                if "done_count" in item:
                    item["done_count"] = 0
                    changed = True
        self._data["last_reset_date"] = today
        if changed:
            self._persist()
        return changed

    def set_item(self, key: str, status: str, meta: Optional[Dict[str, Any]] = None) -> None:
        items = self._data.setdefault("items", {})
        items[key] = {"status": status, "meta": meta or {}}
        self._persist()

    def update_status(self, key: str, status: str) -> None:
        items = self._data.setdefault("items", {})
        item = items.get(key)
        if not item:
            items[key] = {"status": status, "meta": {}}
        else:
            item["status"] = status
        self._persist()

    def increment_done(self, key: str) -> None:
        items = self._data.setdefault("items", {})
        item = items.get(key)
        if not item:
            items[key] = {"status": "pending", "meta": {}, "done_count": 1}
            self._persist()
            return
        item["done_count"] = int(item.get("done_count", 0)) + 1
        quota = item.get("meta", {}).get("quota_per_day")
        if quota and item["done_count"] >= int(quota):
            item["status"] = "completed"
        self._persist()

    def get_item(self, key: str) -> Optional[StateItem]:
        item = self._data.get("items", {}).get(key)
        if not item:
            return None
        return StateItem(key=key, status=item.get("status", ""), meta=item.get("meta", {}))

    def list_items(self) -> List[StateItem]:
        items = []
        for key, item in self._data.get("items", {}).items():
            items.append(StateItem(key=key, status=item.get("status", ""), meta=item.get("meta", {})))
        return items


class DialogueMemory:
    """
    Short-term dialogue memory. Stores recent turns and can be compressed later.
    """

    def __init__(self) -> None:
        self._data = _load_json(
            SHORT_DIALOGUE_FILE,
            {"updated_at": "", "turns": [], "last_compress_at": ""},
        )

    # 务必确保这个方法存在，且缩进在 DialogueMemory 类里面
    def _persist(self) -> None:
        self._data["updated_at"] = datetime.utcnow().isoformat()
        _save_json(SHORT_DIALOGUE_FILE, self._data)

    def append_turn(
            self,
            role: str,
            content: str,
            meta: Optional[Dict[str, Any]] = None,
            max_turns: int = 1000
    ) -> None:
        turn_data = {
            "role": role,
            "content": content,
            "ts": datetime.utcnow().isoformat(),
        }
        if meta:
            turn_data["meta"] = meta

        self._data.setdefault("turns", []).append(turn_data)

        turns = self._data.get("turns", [])
        if len(turns) > max_turns:
            self._data["turns"] = turns[-max_turns:]
        self._persist()  # 报错就是因为这一行找不到上面的 _persist

    def get_recent(self, limit: int = 20) -> List[Dict[str, str]]:
        turns = self._data.get("turns", [])
        return turns[-limit:]

    def get_all(self) -> List[Dict[str, str]]:
        return list(self._data.get("turns", []))

    # 新增的按 meta 筛选方法
    def get_by_meta(self, key: str, value: Any, limit: int = 20) -> List[Dict[str, str]]:
        """
        检索符合特定标签的对话记录
        """
        turns = self._data.get("turns", [])
        matched = []
        for turn in reversed(turns):
            turn_meta = turn.get("meta", {})
            if turn_meta and turn_meta.get(key) == value:
                matched.insert(0, turn)
            if len(matched) >= limit:
                break
        return matched


    def get_memory_overview(self) -> List[Dict[str, Any]]:
        """
        [新增] 扫描所有记忆，按 'cwd' 标签聚合，生成概览索引。
        返回格式: [{'cwd': '/path/a', 'count': 10, 'last_ts': '...', 'preview': '...'}]
        """
        overview = {}
        turns = self._data.get("turns", [])
        for turn in turns:
            meta = turn.get("meta", {})
            cwd = meta.get("cwd")
            if not cwd:
                continue

            if cwd not in overview:
                overview[cwd] = {"cwd": cwd, "count": 0, "last_ts": "", "preview": ""}

            stats = overview[cwd]
            stats["count"] += 1
            ts = turn.get("ts", "")
            if ts > stats["last_ts"]:
                stats["last_ts"] = ts
                # 截取该路径下最新的一条内容作为预览，帮助Router判断
                content = turn.get("content", "")
                if content:
                    stats["preview"] = (content[:50] + "...") if len(content) > 50 else content

        # 将结果转换为列表并按时间倒序排列（最近的排前面）
        result = list(overview.values())
        result.sort(key=lambda x: x["last_ts"], reverse=True)
        return result

    def should_compress(self, min_days: int = 3, min_turns: int = 30) -> bool:
        turns = self._data.get("turns", [])
        if len(turns) < min_turns:
            return False
        last = self._data.get("last_compress_at")
        now = datetime.utcnow()
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
            except ValueError:
                last_dt = None
            if last_dt and now - last_dt >= timedelta(days=min_days):
                return True
            return False
        try:
            oldest_dt = datetime.fromisoformat(turns[0].get("ts", ""))
        except ValueError:
            return False
        return now - oldest_dt >= timedelta(days=min_days)

    def mark_compressed(self) -> None:
        self._data["last_compress_at"] = datetime.utcnow().isoformat()
        self._persist()

    def prune_older_than(self, days: int = 5) -> int:
        turns = self._data.get("turns", [])
        if not turns:
            return 0
        cutoff = datetime.utcnow() - timedelta(days=days)
        kept = []
        removed = 0
        for turn in turns:
            try:
                ts = datetime.fromisoformat(turn.get("ts", ""))
            except ValueError:
                ts = None
            if ts and ts < cutoff:
                removed += 1
                continue
            kept.append(turn)
        if removed:
            self._data["turns"] = kept
            self._persist()
        return removed


class LongTermMemory:
    """
    Long-term memory: user profile + compressed summaries.
    """

    def __init__(self) -> None:
        self._data = _load_json(LONG_TERM_FILE, {"updated_at": "", "facts": [], "summaries": []})

    def _persist(self) -> None:
        self._data["updated_at"] = datetime.utcnow().isoformat()
        _save_json(LONG_TERM_FILE, self._data)

    def add_fact(
        self,
        content: str,
        importance: int = 5,
        fact_type: str = "general",
        source: str = "summary",
        confidence: float = 0.7,
    ) -> None:
        facts = self._data.setdefault("facts", [])
        for item in facts:
            if item.get("type") == fact_type and item.get("content") == content:
                item["importance"] = max(int(item.get("importance", 5)), int(importance))
                item["last_seen"] = datetime.utcnow().isoformat()
                item["confidence"] = max(float(item.get("confidence", 0.7)), float(confidence))
                self._persist()
                return
        facts.append(
            {
                "id": str(uuid.uuid4()),
                "type": fact_type,
                "content": content,
                "importance": int(importance),
                "source": source,
                "last_seen": datetime.utcnow().isoformat(),
                "confidence": float(confidence),
            }
        )
        self._persist()

    def add_summary(self, summary: str, importance: int = 3) -> None:
        self._data.setdefault("summaries", []).append(
            {
                "summary": summary,
                "importance": importance,
                "ts": datetime.utcnow().isoformat(),
            }
        )
        self._persist()

    def list_facts(self) -> List[Dict[str, Any]]:
        return list(self._data.get("facts", []))

    def list_summaries(self) -> List[Dict[str, Any]]:
        return list(self._data.get("summaries", []))
