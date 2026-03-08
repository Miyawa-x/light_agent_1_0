import json
import uuid
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# --- 标准化配置 ---
# 全局知识库文件夹（用于存放全局唯一的json）
GLOBAL_DIR_NAME = ""
# 全局知识库文件名
GLOBAL_FILE_NAME = "knowledge_base.json"
# 局部知识库文件名 (直接位于当前工作目录下的隐藏文件)
LOCAL_FILE_NAME = ".knowledge_base.json"


def _utc_now_str() -> str:
    return datetime.utcnow().isoformat()


@dataclass
class KnowledgeItem:
    id: str
    topic: str
    content: str
    tags: List[str]
    created_at: str
    updated_at: str
    source: str = "user"


class KnowledgeBase:
    """
    通用知识库文件处理器。
    它只认准一个具体的文件路径 (file_path)，不关心它是全局还是局部的逻辑。
    """

    def __init__(self, file_path: Path, allow_create: bool = False) -> None:
        self.file_path = file_path
        self.allow_create = allow_create
        self._data = {"updated_at": "", "items": []}
        self._loaded = False

    def exists(self) -> bool:
        return self.file_path.exists() and self.file_path.is_file()

    def _ensure_parent_dir(self) -> None:
        """确保父目录存在 (主要用于全局库)"""
        if not self.file_path.parent.exists():
            os.makedirs(self.file_path.parent, exist_ok=True)

    def _persist(self) -> None:
        self._ensure_parent_dir()
        self._data["updated_at"] = _utc_now_str()
        self.file_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def _load(self) -> None:
        if self._loaded:
            return

        if not self.exists():
            if self.allow_create:
                # 只有被允许自动创建时（如全局库），才会在加载时初始化
                self._persist()
            else:
                # 否则保持内存为空，等待显式初始化
                return

        try:
            text = self.file_path.read_text(encoding="utf-8")
            self._data = json.loads(text)
        except (json.JSONDecodeError, FileNotFoundError):
            self._data = {"updated_at": "", "items": []}

        self._loaded = True

    def init_storage(self) -> bool:
        """
        显式初始化文件（用于局部库的手动创建）。
        """
        if self.exists():
            return False
        self._persist()
        return True

    def add_item(self, topic: str, content: str, tags: List[str], source: str = "agent") -> str:
        self._load()
        # 如果是 Local 且未初始化，save 时会自动创建文件
        # 但我们在 Manager 层会做校验，这里只负责写入

        item = KnowledgeItem(
            id=str(uuid.uuid4())[:8],
            topic=topic,
            content=content,
            tags=tags,
            created_at=_utc_now_str(),
            updated_at=_utc_now_str(),
            source=source
        )
        self._data["items"].append(asdict(item))
        self._persist()
        return item.id

    def search(self, query: str) -> List[Dict[str, Any]]:
        self._load()
        if not self.exists() and not self.allow_create:
            return []

        items = self._data.get("items", [])
        if not query:
            return items

        query = query.lower()
        results = []
        for item in items:
            score = 0
            # 简单的加权搜索
            if query in item["topic"].lower(): score += 3
            if query in str(item["tags"]).lower(): score += 2
            if query in item["content"].lower(): score += 1

            if score > 0:
                item_copy = item.copy()
                item_copy["score"] = score
                results.append(item_copy)

        results.sort(key=lambda x: x["score"], reverse=True)
        return results


class KnowledgeManager:
    """
    协调器：负责管理 Global (标准化路径) 和 Local (动态路径)。
    """

    def __init__(self, local_cwd: str) -> None:
        # 1. 配置全局库路径： script_dir/knowledge/knowledge_base.json
        base_dir = Path(__file__).resolve().parent
        global_path = base_dir / GLOBAL_DIR_NAME / GLOBAL_FILE_NAME

        # 全局库永远允许自动创建
        self.global_kb = KnowledgeBase(global_path, allow_create=True)

        # 2. 配置局部库路径
        self.local_cwd = Path(local_cwd)
        self.local_kb = self._init_local_kb_instance()

    def _init_local_kb_instance(self) -> KnowledgeBase:
        # 局部库路径： current_cwd/.knowledge_base.json
        local_path = self.local_cwd / LOCAL_FILE_NAME
        # 局部库默认不允许自动创建，必须用户指令触发
        return KnowledgeBase(local_path, allow_create=False)

    def update_cwd(self, new_cwd: str) -> None:
        """当用户 cd 切换目录时调用"""
        self.local_cwd = Path(new_cwd)
        # 重新指向新的 .knowledge_base.json 文件
        self.local_kb = self._init_local_kb_instance()

    def create_local_kb(self) -> str:
        """显式创建"""
        if self.local_kb.exists():
            return f"Local KB already exists: {LOCAL_FILE_NAME}"
        self.local_kb.init_storage()
        return f"Created local knowledge base: {self.local_cwd / LOCAL_FILE_NAME}"

    def add_knowledge(self, scope: str, topic: str, content: str, tags: List[str] = None) -> str:
        tags = tags or []
        if scope == "local":
            if not self.local_kb.exists():
                return f"[Error] Local KB ({LOCAL_FILE_NAME}) not found. Use 'create_local' first."
            try:
                item_id = self.local_kb.add_item(topic, content, tags)
                return f"Added to LOCAL: {topic}"
            except PermissionError:
                return "[Error] Permission denied."
        else:
            item_id = self.global_kb.add_item(topic, content, tags)
            return f"Added to GLOBAL: {topic}"

    def search_knowledge(self, query: str) -> str:
        g_results = self.global_kb.search(query)
        l_results = self.local_kb.search(query)

        lines = []
        if g_results:
            lines.append("--- Global KB ---")
            for item in g_results[:3]:
                lines.append(f"* {item['topic']}: {item['content'][:80]}...")

        if l_results:
            lines.append(f"--- Local KB ({LOCAL_FILE_NAME}) ---")
            for item in l_results[:3]:
                lines.append(f"* {item['topic']}: {item['content'][:80]}...")

        if not lines:
            return "No matching knowledge found."
        return "\n".join(lines)