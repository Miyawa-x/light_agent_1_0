# 🚀 DeepSeek CLI Agent: Context-Aware AI Terminal

A highly dynamic, DeepSeek-powered command-line interface (CLI) assistant. Unlike standard AI wrappers, this agent features a multi-tiered memory system, directory-aware context routing, and a proactive conversation engine that remembers your past projects, habits, and goals. 🧠✨

## ✨ Core Features

* **📂 Context-Aware Memory Routing:** The agent dynamically loads different memory contexts based on your Current Working Directory (CWD). If you `cd` into a past project folder, the AI instantly recalls the relevant context without needing a manual prompt.
* **💬 Proactive Engagement:** The AI isn't just reactive. It randomly pulls from your long-term facts (skills, ongoing goals, project plans) to start relevant conversations or check in on your progress when navigating directories.
* **🗂️ Tri-Tier Memory System:**
  * **⏳ State Memory:** Tracks temporary states and daily resets.
  * **🗜️ Dialogue Memory:** Automatically compresses and summarizes long conversations to optimize token usage while retaining key context.
  * **💾 Long-Term Memory:** Extracts durable facts, user preferences, and skills directly from natural conversation via `$mem$` commands.
* **📚 Knowledge Base Management:** Supports both global and local (directory-specific) knowledge bases. The AI can dynamically search and append documentation or code snippets using `$know$` commands.
* **🛡️ Safe Command Execution:** Executes system commands directly from the terminal via `$ctrl$` syntax, automatically returning stdout/stderr back to the LLM for self-correction.

## 🏗️ Architecture & Commands

The agent communicates with the underlying system using specific JSON-based command prefixes:

* ⚡ `$ctrl$ [command]`: Executes local terminal commands.
* 🧠 `$mem$ {"action": "add_fact", "type": "skill", ...}`: Manages the Long-Term and State memory stores.
* 🔍 `$know$ {"action": "search", "query": "..."}`: Interfaces with the global and local Knowledge Managers.

## ⚙️ Installation & Configuration

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/Miyawa-x/light_agent_1_0.git](https://github.com/Miyawa-x/light_agent_1_0.git)
   cd light_agent_1_0



2. Ensure you have **Python 3.10+** installed. 🐍 *(No other dependencies required!)*
3. **Configure your API Key:** 🔑
* Navigate to `conversation/deepseek_client.py`.
* Locate the API configuration line and replace `"YOUR_API"` with your actual DeepSeek API key.


4. *Optional:* Because the architecture is modular, you can easily modify this client file to use a different provider's API (such as OpenAI, Anthropic, or a local open-source model) by updating the base URL and authentication headers. 🔌

## 🚀 Usage

Run the main script to start the interactive session:

```bash
python chat_cli.py

```

* Type your queries normally. ⌨️
* Navigate directories using standard `cd` commands—the agent will update its context automatically. 🧭
* Type `exit` or `quit` to end the session. 👋

## 👨‍💻 Author

**Xie Runxuan** & **Huang Sichen**
