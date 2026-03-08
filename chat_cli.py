import json
import os
import re
import sys
import random
from pathlib import Path

from conversation.deepseek_client import DeepSeekClient

from safe_cmd.safe_cmd import run_safe_cmd  # noqa: E402

from knowledge.knowledge_store import KnowledgeManager

from memory.memory_store import DialogueMemory, LongTermMemory, StateMemory  # noqa: E402
#上述Path代码在实践中证明无效，故替代以消除报错



CTRL_PREFIX = "$ctrl$"
MEM_PREFIX = "$mem$"
STATE_FILE = Path(__file__).resolve().parent / ".session_cwd.txt"
MIN_COMPRESS_DAYS = 3
MIN_COMPRESS_TURNS = 30
PRUNE_AFTER_DAYS = 5
MAX_DIALOGUE_TURNS = 1000


def extract_ctrl_commands(text: str) -> list[str]:
    commands: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(CTRL_PREFIX):
            continue
        cmd = stripped[len(CTRL_PREFIX) :].strip()
        if cmd:
            commands.append(cmd)
    return commands


def extract_mem_commands(text: str) -> list[str]:
    commands: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(MEM_PREFIX):
            continue
        payload = stripped[len(MEM_PREFIX) :].strip()
        if payload:
            commands.append(payload)
    return commands


KNOW_PREFIX = "$know$"
def extract_know_commands(text: str) -> list[str]:
    commands: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(KNOW_PREFIX):
            continue
        payload = stripped[len(KNOW_PREFIX):].strip()
        if payload:
            commands.append(payload)
    return commands


def apply_knowledge_commands(commands: list[str], km: KnowledgeManager) -> list[str]:
    results: list[str] = []
    for raw in commands:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            results.append(f"invalid_json: {raw}")
            continue

        action = data.get("action")

        if action == "create_local":
            msg = km.create_local_kb()
            results.append(msg)

        elif action == "add":
            scope = data.get("scope", "global")  # 默认为 global
            topic = data.get("topic")
            content = data.get("content")
            tags = data.get("tags", [])

            if not topic or not content:
                results.append("add_knowledge: missing topic or content")
                continue

            msg = km.add_knowledge(scope, topic, content, tags)
            results.append(msg)

        elif action == "search":
            query = data.get("query", "")
            results.append(km.search_knowledge(query))

        else:
            results.append(f"unknown_knowledge_action: {action}")
    return results


def format_command_feedback(results: list[tuple[str, int, str, str]]) -> str:
    lines = ["Command execution results:"]
    for cmd, code, stdout, stderr in results:
        lines.append(f"- cmd: {cmd}")
        lines.append(f"  exit_code: {code}")
        if stdout:
            lines.append("  stdout:")
            lines.append(stdout.rstrip())
        if stderr:
            lines.append("  stderr:")
            lines.append(stderr.rstrip())
    return "\n".join(lines)


def format_memory_feedback(results: list[str]) -> str:
    if not results:
        return ""
    return "Memory update results:\n" + "\n".join(f"- {line}" for line in results)


def load_session_cwd() -> str:
    if STATE_FILE.exists():
        stored = STATE_FILE.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    return os.getcwd()


def save_session_cwd(path: str) -> None:
    STATE_FILE.write_text(path, encoding="utf-8")


_CD_RE = re.compile(r"^cd(?:\s+/d)?\s+(.+)$", re.IGNORECASE)


def handle_cd_command(command: str, current_dir: str) -> tuple[bool, str, str]:
    match = _CD_RE.match(command.strip())
    if not match:
        return False, current_dir, ""
    raw_path = match.group(1).strip().strip('"')
    if not raw_path:
        return True, current_dir, f"Current directory: {current_dir}"
    new_path = raw_path
    if not os.path.isabs(new_path):
        new_path = os.path.normpath(os.path.join(current_dir, new_path))
    if not os.path.isdir(new_path):
        return True, current_dir, f"[error] directory not found: {new_path}"
    return True, new_path, f"Changed directory to: {new_path}"


def strip_special_lines(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(CTRL_PREFIX) or stripped.startswith(MEM_PREFIX):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def build_memory_prompt(long_term: LongTermMemory) -> str:
    facts = sorted(long_term.list_facts(), key=lambda x: x.get("importance", 0), reverse=True)
    summaries = sorted(long_term.list_summaries(), key=lambda x: x.get("importance", 0), reverse=True)
    facts = facts[:5]
    summaries = summaries[:3]
    if not facts and not summaries:
        return ""
    lines = ["Known user memory:"]
    for item in facts:
        lines.append(
            f"- fact(type={item.get('type','general')}, imp={item.get('importance', 0)}): "
            f"{item.get('content', '')}"
        )
    for item in summaries:
        lines.append(f"- summary(imp={item.get('importance', 0)}): {item.get('summary', '')}")
    return "\n".join(lines)


def compress_dialogue_if_needed(
    client: DeepSeekClient,
    dialogue: DialogueMemory,
    long_term: LongTermMemory,
) -> bool:
    if not dialogue.should_compress(min_days=MIN_COMPRESS_DAYS, min_turns=MIN_COMPRESS_TURNS):
        return False
    turns = dialogue.get_all()
    if not turns:
        return False
    transcript = "\n".join([f"{t['role']}: {t['content']}" for t in turns])
    messages = [
        {
            "role": "system",
            "content": (
                "You are a memory organizer. Extract durable user facts and a concise summary. "
                "Only output strict JSON with keys: "
                "facts (list of {type, content, importance, confidence}), "
                "summary (string), summary_importance (1-10). "
                "Use type from: profile, preference, skill, constraint, project, habit, general."
            ),
        },
        {"role": "user", "content": transcript},
    ]
    try:
        response = client.chat(messages, temperature=0.1, max_tokens=512)
    except Exception:
        return False
    choice = response.get("choices", [{}])[0]
    content = choice.get("message", {}).get("content", "")
    if not content:
        return False
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return False
    for fact in data.get("facts", []):
        text = fact.get("content", "").strip()
        if not text:
            continue
        fact_type = fact.get("type", "general")
        long_term.add_fact(
            text,
            importance=int(fact.get("importance", 5)),
            fact_type=fact_type,
            source="summary",
            confidence=float(fact.get("confidence", 0.7)),
        )
    summary = data.get("summary", "").strip()
    if summary:
        long_term.add_summary(summary, importance=int(data.get("summary_importance", 3)))
    dialogue.mark_compressed()
    dialogue.prune_older_than(days=PRUNE_AFTER_DAYS)
    return True


def apply_memory_commands(
    commands: list[str],
    state: StateMemory,
    long_term: LongTermMemory,
) -> list[str]:
    results: list[str] = []
    for raw in commands:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            results.append(f"invalid_json: {raw}")
            continue
        action = data.get("action")
        if action == "add_fact":
            content = str(data.get("content", "")).strip()
            if not content:
                results.append("add_fact: missing content")
                continue
            long_term.add_fact(
                content,
                importance=int(data.get("importance", 5)),
                fact_type=str(data.get("type", "general")),
                source=str(data.get("source", "assistant")),
                confidence=float(data.get("confidence", 0.7)),
            )
            results.append(f"add_fact: {content}")
        elif action == "add_summary":
            summary = str(data.get("summary", "")).strip()
            if not summary:
                results.append("add_summary: missing summary")
                continue
            long_term.add_summary(summary, importance=int(data.get("importance", 3)))
            results.append("add_summary: ok")
        elif action == "set_state":
            key = str(data.get("key", "")).strip()
            status = str(data.get("status", "")).strip()
            if not key or not status:
                results.append("set_state: missing key/status")
                continue
            meta = data.get("meta") or {}
            state.set_item(key, status, meta=meta)
            results.append(f"set_state: {key} -> {status}")
        elif action == "update_status":
            key = str(data.get("key", "")).strip()
            status = str(data.get("status", "")).strip()
            if not key or not status:
                results.append("update_status: missing key/status")
                continue
            state.update_status(key, status)
            results.append(f"update_status: {key} -> {status}")
        elif action == "increment_done":
            key = str(data.get("key", "")).strip()
            if not key:
                results.append("increment_done: missing key")
                continue
            state.increment_done(key)
            results.append(f"increment_done: {key}")
        else:
            results.append(f"unknown_action: {action}")
    return results

def decide_memory_context(
        client: DeepSeekClient,
        user_input: str,
        overview: list[dict],
        current_cwd: str
) -> str:
    """
    [新增] Router 逻辑：分析用户意图和记忆概览，决定加载哪个路径的记忆。
    """
    # 策略：如果没有历史记录，或者只有当前目录的记录，直接返回当前目录，节省 API 调用
    if not overview:
        return current_cwd
    if len(overview) == 1 and overview[0]['cwd'] == current_cwd:
        return current_cwd

    # 构建轻量级 Prompt 供模型判断
    # 仅提供索引（路径 + 预览），不提供完整内容
    overview_json = json.dumps(overview, ensure_ascii=False, indent=2)
    messages = [
        {
            "role": "system",
            "content": (
                "You are a memory router. Your goal is to identify which memory context (cwd) "
                "is relevant to the user's input.\n"
                f"Current Working Directory: {current_cwd}\n"
                f"Available Memory Contexts (Index):\n{overview_json}\n\n"
                "Rules:\n"
                "1. If the user refers to a past project or path in the Index, return that cwd.\n"
                "2. If the user input relates to the current task/directory, return the Current CWD.\n"
                "3. Output ONLY a JSON object: {\"target_cwd\": \"...\"}"
            )
        },
        {"role": "user", "content": user_input}
    ]

    try:
        # 使用 temperature=0.0 确保判断准确且格式稳定
        response = client.chat(messages, temperature=0.0, max_tokens=128)
        choice = response.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "")

        # 提取 JSON
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
            return data.get("target_cwd", current_cwd)
    except Exception:
        # 如果路由出错，安全回退到当前目录
        pass

    return current_cwd


def trigger_proactive_memory(
        client: DeepSeekClient,
        long_term_memory: LongTermMemory,
        current_cwd: str,
        force: bool = False
) -> str:
    """
    [新增] 主动回忆机制：AI "突然" 想起了什么。
    :param force: 是否强制触发（用于启动时或特定事件）
    """
    # 1. 概率控制：如果是被动触发，只有 20% 的概率开口，避免太烦人
    if not force and random.random() > 0.2:
        return ""

    # 2. 获取素材：提取长期记忆中的 Facts
    # 我们优先找 'plan', 'goal', 'project' 类型的记忆
    facts = long_term_memory.list_facts()
    if not facts:
        return ""

    # 筛选一些有趣的记忆（比如 unfinished tasks, ongoing learning）
    interesting_facts = [
        f for f in facts
        if f.get('type') in ['goal', 'skill', 'project', 'habit']
    ]
    # 如果没有特定的，就用全部
    candidates = interesting_facts if interesting_facts else facts

    # 随机选 3 个作为灵感
    selected = random.sample(candidates, k=min(3, len(candidates)))
    memory_text = "\n".join([f"- [{f.get('type')}] {f.get('content')}" for f in selected])

    # 3. 生成一句简短的“寒暄”
    messages = [
        {
            "role": "system",
            "content": (
                "You are an AI assistant attempting to start a casual conversation based on past memory. "
                "Look at the user's memory fragments below and the current directory.\n"
                f"Current Directory: {current_cwd}\n"
                f"Memory Fragments:\n{memory_text}\n\n"
                "Task: Generate a very short (1 sentence), casual, friendly remark to remind the user "
                "of a past goal, ask about progress, or comment on the current project directory.\n"
                "Examples:\n"
                "- 'By the way, how is the German learning going?'\n"
                "- 'Back in the coding folder I see. Ready to fix that bug?'\n"
                "- 'Did you ever finish reading that book you mentioned?'\n"
                "Constraint: If nothing is relevant or interesting, output MAGIC_SKIP.\n"
                "Output: ONLY the sentence or MAGIC_SKIP."
            )
        }
    ]

    try:
        response = client.chat(messages, temperature=0.7, max_tokens=60)
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

        if "MAGIC_SKIP" in content or not content:
            return ""

        return content
    except Exception:
        return ""

def main() -> int:
    client = DeepSeekClient()
    session_cwd = load_session_cwd()

    # --- 初始化各存储模块 ---
    state_memory = StateMemory()
    dialogue_memory = DialogueMemory()
    long_term_memory = LongTermMemory()
    # [新增] 初始化知识库管理器
    knowledge_manager = KnowledgeManager(local_cwd=session_cwd)

    state_memory.reset_daily_if_needed()
    memory_context = build_memory_prompt(long_term_memory)

    base_system_prompt = (
        "You are a helpful assistant connected to a local command controller. "
        "When you need to run a Windows 11 CMD command, output it on its own line "
        "starting with $ctrl$. Example:\n"
        "$ctrl$ dir\n"
        "$ctrl$ start \"\" \"C:\\\\Program Files\\\\App\\\\app.exe\"\n"
        "Only $ctrl$-prefixed lines will be executed as commands. Use Windows CMD syntax. "
        "Do not claim you cannot execute commands; you can via $ctrl$ and will receive results.\n\n"

        "For memory updates (User Profile/Habits), output a single-line JSON after $mem$:\n"
        "$mem$ {\"action\":\"add_fact\",\"type\":\"skill\",\"content\":\"User is learning MindSpore\"}\n"
        "Rule: when the user states profile, preference, long-term goal, skill, constraint, or habit, "
        "you must emit a $mem$ add_fact line to store it.\n\n"

        "For Knowledge Base (Documentation/Code Snippets), output a single-line JSON after $know$:\n"
        "$know$ {\"action\": \"search\", \"query\": \"deployment script\"}\n"
        "$know$ {\"action\": \"add\", \"scope\": \"global\", \"topic\": \"Python Style\", \"content\": \"Use Snake Case\", \"tags\": [\"coding\"]}\n"
        "$know$ {\"action\": \"add\", \"scope\": \"local\", \"topic\": \"Project Config\", \"content\": \"Port is 8080\"} (Requires local KB)\n"
        "$know$ {\"action\": \"create_local\"} (Only if user explicitly asks to init local KB for this folder).\n"
    )

    messages = [
        {
            "role": "system",
            "content": base_system_prompt + (f"\n\n{memory_context}" if memory_context else ""),
        }
    ]

    print("DeepSeek CLI. Type 'exit' or 'quit' to stop.")

    welcome_msg = trigger_proactive_memory(client, long_term_memory, session_cwd, force=True)
    if welcome_msg:
        print(f"\nassistant (memory)> {welcome_msg}\n")
        # 可选：把它加入历史，这样你回答的时候它知道自己在问啥
        messages.append({"role": "assistant", "content": welcome_msg})

    while True:
        try:
            user_input = input("you> ").strip()
        except EOFError:
            print()
            break

        if not user_input:
            # [进阶] 如果用户只是按回车，触发一次“无聊时的闲聊”
            idle_msg = trigger_proactive_memory(client, long_term_memory, session_cwd, force=True)
            if idle_msg:
                print(f"assistant> {idle_msg}")
                messages.append({"role": "assistant", "content": idle_msg})
            continue
        if user_input.lower() in {"exit", "quit"}:
            break

        # --- Scheme B: 动态记忆加载逻辑 (Memory Router) ---
        memory_overview = dialogue_memory.get_memory_overview()
        target_cwd = decide_memory_context(client, user_input, memory_overview, session_cwd)

        context_turns = []
        if target_cwd:
            context_turns = dialogue_memory.get_by_meta("cwd", target_cwd, limit=10)
            if target_cwd != session_cwd and context_turns:
                print(f"[System] 正在加载相关上下文: {target_cwd}")

        current_messages = list(messages)
        if context_turns:
            context_text = "\n".join([f"[{t['ts']}] {t['role']}: {t['content']}" for t in context_turns])
            injection_msg = {
                "role": "system",
                "content": f"Relevant Memory (from {target_cwd}):\n{context_text}"
            }
            current_messages.insert(1, injection_msg)

        current_messages.append({"role": "user", "content": user_input})

        # 记录用户输入 (带 CWD 标签)
        dialogue_memory.append_turn(
            "user",
            user_input,
            meta={"cwd": session_cwd},
            max_turns=MAX_DIALOGUE_TURNS
        )
        messages.append({"role": "user", "content": user_input})

        try:
            response = client.chat(current_messages)
        except Exception as exc:
            print(f"[error] {exc}")
            messages.pop()
            continue

        choice = response.get("choices", [{}])[0]
        assistant_msg = choice.get("message", {}).get("content", "")
        if not assistant_msg:
            print("[error] empty response")
            continue

        print(f"assistant> {assistant_msg}")
        messages.append({"role": "assistant", "content": assistant_msg})

        cleaned_assistant = strip_special_lines(assistant_msg)
        if cleaned_assistant:
            dialogue_memory.append_turn(
                "assistant",
                cleaned_assistant,
                meta={"cwd": session_cwd},
                max_turns=MAX_DIALOGUE_TURNS
            )

        # --- 指令提取 ---
        ctrl_commands = extract_ctrl_commands(assistant_msg)
        mem_commands = extract_mem_commands(assistant_msg)
        know_commands = extract_know_commands(assistant_msg)  # [新增]

        # --- 处理 Memory 和 Knowledge 指令 ---
        mem_results = apply_memory_commands(mem_commands, state_memory, long_term_memory)
        mem_feedback = format_memory_feedback(mem_results)

        know_results = apply_knowledge_commands(know_commands, knowledge_manager)  # [新增]
        know_feedback = ""
        if know_results:
            know_feedback = "Knowledge Ops:\n" + "\n".join(f"- {r}" for r in know_results)

        # 统一处理反馈
        if mem_feedback:
            print(mem_feedback)
            messages.append({"role": "user", "content": mem_feedback})
        if know_feedback:
            print(know_feedback)
            messages.append({"role": "user", "content": know_feedback})

        # 如果没有任何操作，进入下一轮
        if not ctrl_commands and not mem_feedback and not know_feedback:
            updated = compress_dialogue_if_needed(client, dialogue_memory, long_term_memory)
            if updated:
                memory_context = build_memory_prompt(long_term_memory)
                messages[0]["content"] = base_system_prompt + (
                    f"\n\n{memory_context}" if memory_context else ""
                )
            continue

        # --- 优化的执行与反馈逻辑 (Silent Execution) ---
        results: list[tuple[str, int, str, str]] = []
        has_significant_output = False

        for cmd in ctrl_commands:
            handled, new_cwd, note = handle_cd_command(cmd, session_cwd)
            if handled:
                session_cwd = new_cwd
                save_session_cwd(session_cwd)
                knowledge_manager.update_cwd(session_cwd)
                results.append((cmd, 0 if not note.startswith("[error]") else 1, note, ""))

                # [插入点 B] 切换目录后的主动回忆
                # 这里不强制 (force=False)，让它随机触发，或者你可以写逻辑判断如果是新目录则强制触发
                reflection = trigger_proactive_memory(client, long_term_memory, session_cwd, force=False)
                if reflection:
                    # 我们把它作为一种特殊的系统输出打印出来
                    print(f"\nassistant (reflection)> {reflection}")
                    # 同时也加入到 messages 里，作为上下文
                    messages.append({"role": "assistant", "content": reflection})

                continue
            try:
                result = run_safe_cmd(cmd, cwd=session_cwd)
                stdout = result.stdout.strip()
                stderr = result.stderr.strip()

                if stdout or stderr or result.returncode != 0:
                    has_significant_output = True

                results.append((cmd, result.returncode, stdout, stderr))
            except Exception as exc:
                has_significant_output = True
                results.append((cmd, 1, "", f"[blocked/error] {exc}"))

        all_success = all(r[1] == 0 for r in results)
        is_trivial = all_success and not has_significant_output

        feedback = format_command_feedback(results)

        if is_trivial:
            print("[System] Commands executed successfully.")
            messages.append({"role": "user", "content": feedback})
        else:
            print(feedback)
            messages.append({"role": "user", "content": feedback})

            # Follow-up: 仅当有实质性输出时，让 LLM 进行解释
            try:
                follow_up = client.chat(messages)
                follow_choice = follow_up.get("choices", [{}])[0]
                follow_msg = follow_choice.get("message", {}).get("content", "")

                if follow_msg:
                    print(f"assistant> {follow_msg}")
                    messages.append({"role": "assistant", "content": follow_msg})
                    cleaned_follow = strip_special_lines(follow_msg)
                    if cleaned_follow:
                        dialogue_memory.append_turn(
                            "assistant",
                            cleaned_follow,
                            meta={"cwd": session_cwd},
                            max_turns=MAX_DIALOGUE_TURNS
                        )
            except Exception as exc:
                print(f"[error] {exc}")

        # 压缩检查
        updated = compress_dialogue_if_needed(client, dialogue_memory, long_term_memory)
        if updated:
            memory_context = build_memory_prompt(long_term_memory)
            messages[0]["content"] = base_system_prompt + (
                f"\n\n{memory_context}" if memory_context else ""
            )

    return 0

if __name__ == "__main__":
    sys.exit(main())
