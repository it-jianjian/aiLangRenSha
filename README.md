# AI 狼人杀（ai-werewolf）

**让一群大模型 Agent 坐下来打一局狼人杀的 Web 平台。** 你可以看着 6 个 AI 用自然语言互相试探、跳身份、结盟、拉票、投票，也可以自己坐进 1 号位和 AI 同场对打——每一次发言、投票和夜间行动都通过 WebSocket 实时呈现，整局结束后还能逐事件回放。和「一个 Prompt 让模型扮演角色」的玩具不同，它用 **LangGraph 把整局拆成有状态的游戏流程**，每个 AI 走 **ReAct 循环按需查证据再决策**，并有严格的 **角色信息隔离**：狼人偷看不到女巫的用药，村民也看不到预言家的查验结果。

## 为什么值得看

- **两种对战模式。** `pure_ai`（纯 AI 自动对战）和 `mixed`（人类 + AI 混合）。默认 6 人官方板子：2 狼 + 2 村民 + 1 预言家 + 1 女巫；阵容与人数可配置，座位支持 1–12，引擎还内置守卫、猎人等角色。
- **ReAct 决策，不是单次问答。** LangGraph 子图跑 `Thought → Action → Observation` 循环，3 个只读工具（查票型 / 查发言 / 查死亡），最多 3 轮强制收敛，完整决策轨迹落库、可回放审计。
- **严格信息隔离。** 按角色可见性矩阵过滤上下文，连工具返回值都走同一套隔离过滤——这是博弈公平的安全红线。
- **夜晚与投票并行。** 狼人 / 预言家 / 守卫 `asyncio.gather` 并行、投票全员并行，模块级 `Semaphore(5)` 限流，行动延迟可配置（快速模式可设 0）。
- **发言流式输出。** `speech` / `last_words` 走 `llm.astream()` 逐字推送 `speech_chunk`，前端打字机效果，中途异常自动兜底不中断对局。
- **模型路由与降级。** 按决策类型和座位路由不同模型，分级超时（简单决策 15s、发言 45s），实例缓存 + 失败重试；**未配置 API Key 时自动降级为内置 Mock LLM**，零成本就能跑通整局逻辑。
- **检查点续跑。** `AsyncSqliteSaver` 持久化状态，`thread_id = game_id`，服务重启后自动从断点恢复进行中的对局。
- **可观测、可回放。** `AgentLog` 记录 token 用量 / 模型名 / 延迟 / fallback 标记，`agent_steps` 存完整 ReAct 轨迹，节点级 `[Perf]` 计时，配套离线分析脚本；整局事件流可回放。
- **工程化到位。** 后端 22 个测试文件 / 184 个测试函数、前端 5 个文件 / 28 个用例；GitHub Actions CI 跑 ruff + pytest（覆盖率门槛 ≥ 60%）+ vitest + tsc + vite build。

> **实验特性（默认关闭）**：狼队协商（`wolf_deliberation`）、发言批评-修订（`speech_critique`）——均可通过环境变量开启。

## 目录

- [快速上手（30 秒看懂）](#快速上手30-秒看懂)
- [本地启动](#本地启动)
- [系统架构](#系统架构)
- [核心能力](#核心能力)
- [游戏模式与角色](#游戏模式与角色)
- [API 与 WebSocket](#api-与-websocket)
- [配置项](#配置项)
- [技术栈](#技术栈)
- [测试与 CI](#测试与-ci)
- [目录结构](#目录结构)
- [Docker 部署](#docker-部署)

---

## 快速上手（30 秒看懂）

启动后端后（见下一节），两条命令即可开一局纯 AI 对战：

```bash
# 1) 创建一局纯 AI 对战（没配 API Key 也能跑，会自动用内置 Mock LLM）
curl -X POST http://localhost:8000/api/v1/games \
  -H "Content-Type: application/json" \
  -d '{"mode": "pure_ai"}'
# => {"code":0,"message":"ok","data":{"game_id":"<uuid>","mode":"pure_ai","status":"waiting","player_count":6,"roster":{...}}}

# 2) 开局，AI 自动进入「夜晚 → 白天发言 → 投票」循环
curl -X POST http://localhost:8000/api/v1/games/<game_id>/start
```

前端连上 `ws://localhost:8000/ws/game/<game_id>` 后，会实时收到诸如逐字发言的事件：

```jsonc
{ "type": "speech_chunk", "data": { "seat": 3, "round": 2, "delta": "…" } }
```

---

## 本地启动

推荐用本地开发模式启动（已验证可从干净环境跑通）。

### 1. 后端（FastAPI，端口 8000）

```bash
cd backend
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt langgraph-checkpoint-sqlite
cp .env.example .env        # 编辑 .env，填入 LLM_API_KEY（留空则用内置 Mock LLM）
uvicorn app.main:app --reload --port 8000
```

> **说明**
> - `langgraph-checkpoint-sqlite` 是检查点续跑所需（`game_flow.py` 顶层导入），但未列入 `requirements.txt`，需一并安装。
> - 数据库表在**启动时自动创建**（`lifespan` 里 `create_all` + Alembic 升级），无需手动执行迁移。
> - Python 版本要求 **3.11+**（见 `pyproject.toml` 与后端 Dockerfile）。

### 2. 前端（Vite + React，端口 5173）

```bash
cd frontend
npm install
npm run dev
```

打开 **http://localhost:5173**。Vite 已在 `vite.config.ts` 中把 `/api` 与 `/ws` 代理到 `http://localhost:8000`，无需额外配置跨域。

---

## 系统架构

```
┌──────────┐     WebSocket / HTTP      ┌────────────────┐
│ Frontend │ ◄───────────────────────► │    Backend     │
│  React   │                           │    FastAPI     │
│ Zustand  │                           │                │
└──────────┘                           └───────┬────────┘
                                               │
             ┌─────────────────────────────────┼─────────────────────────────────┐
             │                                 │                                 │
      ┌──────▼───────┐                 ┌───────▼────────┐               ┌────────▼────────┐
      │  GameFlow    │                 │  Agent Graph   │               │  HumanBridge    │
      │  LangGraph   │                 │  ReAct 子图     │               │  asyncio.Event  │
      │  StateGraph  │                 │  + LLM 路由     │               │  多槽位等待      │
      └──────┬───────┘                 └───────┬────────┘               └────────┬────────┘
             │                                 │                                 │
             └─────────────────────────────────┼─────────────────────────────────┘
                                               │
                                     ┌─────────▼──────────┐
                                     │  SQLite (aiosqlite) │
                                     │  + AsyncSqliteSaver │
                                     │    werewolf.db      │
                                     └─────────────────────┘
```

游戏主流程由 LangGraph `StateGraph` 编排：`night_start → 夜晚并行行动 → night_witch → night_settle → hunter_revenge → victory_check → day_start → day_last_words → day_speech → day_vote → day_vote_result →（淘汰 / PK / 平安日）→ …` 循环，节点间用条件边路由，`AsyncSqliteSaver` 负责检查点持久化。

---

## 核心能力

### ReAct Agent 子图（`agent/react_agent.py`）

打破「ContextFilter → PromptBuild → LLMCall → DecisionParse」的单向流水线，让 AI 能**主动取证**：

- LLM 每轮先产出 `Thought`，再决定是否调用只读工具；`Observation` 以 `ToolMessage` 回填后继续推理。
- 3 个只读工具：`query_vote_history`、`query_speech`、`query_death_history`（`agent/react_tools.py`）。
- **终止双保险**：LLM 自主输出最终决策为主出口，`max_iterations = 3` 强制出图并走全量兜底。
- 每一步（`thought / tool_call / observation / final`）写入 `agent_steps` 表，决策过程逐行可审计、可回放。

### 信息隔离（`agent/context_filter.py`）

按角色构建可见性矩阵，是保证博弈公平的安全红线：

| 角色 | 额外可见 |
|------|----------|
| 狼人 | 同伴座位 |
| 预言家 | 历史查验结果 |
| 女巫 | 药水状态 + 当夜被杀目标 |
| 村民 | 仅公开信息 |

> 任何新增数据通道（滚动摘要、工具返回、流式事件）都必须过同一套隔离过滤。

### 并行化（`graphs/nodes/night_phase.py`、`vote_phase.py`）

- **夜晚**：狼人 / 预言家 / 守卫 `asyncio.gather` 并行，女巫因依赖刀口在 gather 后串行；端到端耗时约等于「最慢的一方 + 女巫」，而非四者之和。
- **投票**：所有 AI 投票与人类 `wait_for_action` 一起并行；PK 重投同样并行。
- 模块级 `asyncio.Semaphore(5)` 限流，防止聚合平台 QPS 触顶；行动延迟可按阶段配置，快速模式设 0。

### 发言流式输出（`graphs/nodes/day_phase.py`）

`speech` / `last_words` 走 `llm.astream()`，每个 chunk 推送 `speech_chunk` 事件，前端 `CurrentSpeechPanel` 逐字渲染、`speech_end` 落定；流式中途异常时已累积文本照常驻库并标注「（发言中断）」，回放格式不变。

### 模型路由、缓存与降级（`agent/llm.py`）

`create_llm` 的查找优先级：**显式模型名 → 决策类型映射 → 座位配置 → 默认模型 → Mock 降级**。

- 按决策类型路由：简单决策（kill/verify/save/poison/vote/guard/hunter_shoot）可走小模型，发言走大模型。
- 按座位路由：座位 1–12 各自可绑定独立模型与温度。
- 分级超时：简单决策 15s、发言 45s；`ChatOpenAI` `max_retries=1` 重试后再降级。
- **未配置 API Key 时自动降级为 `MockWerewolfLLM`**，输出合法结构化决策，本地零成本跑通全流程与测试。

### 检查点持久化与对局恢复（`graphs/game_flow.py`、`main.py`）

编译图时注入 `AsyncSqliteSaver`（与业务库同一个 SQLite）。服务启动时扫描 `status = playing` 的对局：有检查点则 `resume` 续跑，无检查点则标记 `interrupted_by_restart` 终止，避免僵尸对局。

### 可观测性（`models/game.py`、`scripts/`）

- `AgentLog`：完整 Prompt、LLM 输出、解析决策、`prompt_tokens` / `completion_tokens`、`model_name`、`is_fallback`、`latency_ms`。
- 节点级 `[Perf] {节点名} 耗时 {ms}ms` 计时。
- 离线脚本：`analyze_agent_logs.py`（按 action_type / round / game 多维聚合）、`batch_games.py`（批量跑 N 局，支持 `--mock` 做逻辑回归）。

---

## 游戏模式与角色

| 模式 | 说明 |
|------|------|
| `pure_ai` | 纯 AI 自动对战，创建后 `start` 即可观战 |
| `mixed` | 人类 + AI 混合，创建时需提供 `player_name`，人类座位通过 `HumanActionBridge` 桥接 |

- 默认 6 人官方板子：**2 狼 + 2 村民 + 1 预言家 + 1 女巫**。
- 阵容/人数可通过 roster 接口调整，座位号 1–12；引擎另支持守卫、猎人等角色（含 `hunter_revenge` 节点与 `guard` / `hunter_shoot` 行动）。
- 人类操作有默认超时与幂等防重（同一窗口重复提交返回 409），避免关掉页面导致整局悬挂。

---

## API 与 WebSocket

REST 前缀 `/api/v1/games`，统一响应 `{code, message, data}`（`code=0` 为成功）。

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/api/v1/games` | 创建对局 |
| GET | `/api/v1/games` | 对局列表（分页 / 按状态过滤） |
| GET | `/api/v1/games/{id}` | 对局详情 |
| GET | `/api/v1/games/{id}/events` | 事件列表 |
| GET | `/api/v1/games/{id}/pending_action` | 查询等待中的人类操作（断线恢复用） |
| POST | `/api/v1/games/{id}/roster/reset-official` | 重置为官方阵容 |
| POST | `/api/v1/games/{id}/start` | 开始对局 |
| POST | `/api/v1/games/{id}/actions/night` | 人类夜晚行动（kill/verify/save/poison/skip） |
| POST | `/api/v1/games/{id}/actions/speech` | 人类提交发言 |
| POST | `/api/v1/games/{id}/actions/vote` | 人类提交投票 |
| GET | `/api/v1/games/{id}/replay` | 获取整局回放数据 |
| GET | `/health` | 健康检查 |
| WS | `/ws/game/{game_id}` | 实时事件推送（首帧认证后绑定座位） |

WebSocket 采用**首帧认证制**：连接后第一条消息必须是认证凭据，验证通过才绑定人类座位并投递私密身份；令牌绝不出现在 URL、日志或错误消息中。应用层心跳（约 25s）配合前端心跳看门狗，顶住反向代理空闲超时并及时重连。

---

## 配置项

复制 `backend/.env.example` 为 `.env` 后按需修改（`pydantic-settings` 加载，环境变量 > `.env` > 默认值）。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/werewolf.db` | 数据库连接（生产可切 MySQL） |
| `LLM_API_KEY` | 空 | 任意 OpenAI 兼容 API Key；留空则用 Mock LLM |
| `LLM_BASE_URL` | `https://api.siliconflow.cn/v1` | OpenAI 兼容端点（硅基流动 / DeepSeek / Qwen 等） |
| `LLM_MODEL_NAME` | `Qwen/Qwen3.5-Instruct` | 默认模型 |
| `LLM_TEMPERATURE` | `0.7` | 采样温度 |
| `LLM_ACTION_SIMPLE_MODEL` | 空 | 简单决策专用模型（空=不启用路由） |
| `LLM_ACTION_SPEECH_MODEL` | 空 | 发言专用模型（空=不启用路由） |
| `LLM_INST_{1..12}_MODEL` / `_TEMPERATURE` | 空 / `0.7` | 按座位绑定独立模型 |
| `PROMPT_HISTORY_BUDGET` | `3000` | 历史字符预算，超阈值触发滚动摘要 |
| `AI_ACTION_DELAY_NIGHT/DAY/VOTE` | `1.5 / 1.0 / 0.8` | 各阶段行动间隔（快速模式可设 0） |
| `WOLF_DELIBERATION_ENABLED` | `false` | 实验：狼队协商开关 |
| `SPEECH_CRITIQUE_ENABLED` | `false` | 实验：发言批评-修订开关 |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` | 允许的跨域来源 |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | 服务监听地址 |

---

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | React 18 · TypeScript 5.6 · Vite 5 · Zustand 4 · Ant Design 5 · axios · react-router-dom 6 · Vitest 2 |
| 后端 | Python 3.11+ · FastAPI · uvicorn · pydantic v2 · pydantic-settings · websockets |
| AI 框架 | LangChain 0.3 · LangGraph 0.2（StateGraph 编排 + ReAct 子图 + Checkpointer） |
| 数据库 | SQLAlchemy 2.0（async）· aiosqlite · Alembic（6 个迁移）· 8 张表；生产可切 MySQL（aiomysql / pymysql） |
| 部署 | Docker Compose（backend `python:3.11-slim`、frontend `node:20` 构建 → `nginx`） |

数据库表：`games`、`game_players`、`game_rounds`、`game_events`、`chat_messages`、`votes`、`agent_logs`、`agent_steps`。

---

## 测试与 CI

```bash
# 后端（覆盖率门槛 ≥ 60%）
cd backend && pytest tests/ --cov=app --cov-report=term-missing
ruff check .

# 前端
cd frontend && npm test          # vitest run
npx tsc --noEmit                 # 类型检查
```

GitHub Actions（`.github/workflows/ci.yml`）在 push / PR 到 `main` 时自动运行：后端 `ruff check` + `pytest --cov-fail-under=60`，前端 `npm test` + `tsc --noEmit` + `vite build`。

- 后端：22 个测试文件、184 个测试函数。
- 前端：5 个测试文件、28 个测试用例。

---

## 目录结构

```
aiLangRenSha/
├── backend/
│   ├── app/
│   │   ├── agent/        # ReAct 子图、LLM 路由、信息隔离、Prompt、发言批评
│   │   ├── graphs/       # LangGraph 游戏流程编排
│   │   │   └── nodes/    # 夜晚 / 白天 / 投票 / 胜负 / Agent 节点
│   │   ├── services/     # GameService、HumanActionBridge、公共事件、规则
│   │   ├── models/       # SQLAlchemy ORM（8 张表）
│   │   └── api/          # FastAPI 路由 + WebSocket + Schemas
│   ├── alembic/          # 数据库迁移
│   ├── scripts/          # 批量对局 + 离线分析
│   └── tests/            # pytest（184 个测试函数）
├── frontend/
│   └── src/
│       ├── components/   # 身份面板、发言面板、事件日志、玩家表
│       ├── pages/        # Lobby / Game / Replay
│       ├── stores/       # Zustand 状态
│       └── services/     # REST / WebSocket / 事件流
└── .github/workflows/    # CI
```

---

## Docker 部署

仓库提供 `docker-compose.yml` 与前后端 `Dockerfile`，用于容器化部署：

```bash
docker-compose up -d --build
# backend → :8000，frontend → :80
```

> **注意**：前端镜像的 `Dockerfile.frontend` 会 `COPY frontend/nginx.conf`，但当前仓库**未提供该文件**；直接构建容器化前端可能失败或缺少把 `/api`、`/ws` 反向代理到后端的配置。若要使用 Docker 路径，请先补齐 `frontend/nginx.conf`。日常开发与验证建议优先使用上面的[本地启动](#本地启动)方式。

---

<sub>本项目为个人独立开发的多 Agent 狼人杀对战平台，基于 LangChain + LangGraph 构建，持续迭代中。</sub>
