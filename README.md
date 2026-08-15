# AI 狼人杀

基于 LangChain + LangGraph 的多 Agent 狼人杀对战平台。6 人局（2狼+2村民+1预言家+1女巫），支持纯 AI 自动对战和人类+AI 混合模式。

## 架构图

```
┌──────────┐     WebSocket      ┌───────────┐
│ Frontend │ ◄────────────────► │  Backend  │
│  React   │                    │ FastAPI   │
│  Ant Design                  │           │
└──────────┘                    └─────┬─────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    │                 │                 │
              ┌─────▼─────┐   ┌──────▼──────┐   ┌─────▼──────┐
              │ GameFlow  │   │ Agent Graph │   │ HumanBridge│
              │ LangGraph │   │ ReAct/LLM   │   │ asyncio    │
              └─────┬─────┘   └──────┬──────┘   └─────┬──────┘
                    │                 │                 │
                    └─────────────────┼─────────────────┘
                                      │
                                ┌─────▼─────┐
                                │  SQLite   │
                                │  (werewolf│
                                │   .db)    │
                                └───────────┘
```

## 技术栈

| 层 | 技术 |
|---|------|
| 前端 | React + TypeScript + Vite + Zustand + Ant Design |
| 后端 | Python 3.10+ + FastAPI + LangChain + LangGraph |
| 数据库 | SQLite (异步 aiosqlite) + Alembic 迁移 |
| AI | OpenAI-compatible API（硅基流动/Qwen/DeepSeek 等） |
| 部署 | Docker Compose / uvicorn |

## 改造亮点

- **并行化**：夜晚狼/预言/守卫 asyncio.gather 并行，投票并行，延迟从 O(n) 降到 O(1)
- **流式输出**：发言打字机效果，首字 < 1 秒呈现
- **ReAct Agent**：LangGraph 子图实现 Thought→Action→Observation 循环，按需查询工具
- **模型路由**：简单决策走小模型、发言走大模型，降低 token 成本
- **Prompt 治理**：滚动摘要，第 6+ 轮 Prompt 稳定在阈值内
- **Checkpointer**：服务重启后对局自动续跑，不丢状态
- **可观测性**：AgentLog 完整轨迹 + token 消耗 + 节点级计时

## 快速启动

```bash
# 后端
cd backend
cp .env.example .env
# 编辑 .env 填入 LLM_API_KEY
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload

# 前端
cd frontend
npm install
npm run dev
```

## Docker 一键启动

```bash
docker-compose up -d
```

## 目录结构

```
aiLangRenSha/
├── backend/
│   ├── app/
│   │   ├── agent/         # Agent 推理（ReAct 子图、LLM 路由、Prompt）
│   │   ├── graphs/        # LangGraph 游戏流程编排
│   │   │   └── nodes/     # 各阶段节点函数
│   │   ├── services/      # 业务服务层
│   │   ├── models/        # SQLAlchemy ORM
│   │   └── api/           # FastAPI 路由 + WS
│   ├── scripts/           # 批量对局 + 分析脚本
│   └── tests/             # 168 个单元测试
├── frontend/
│   └── src/
│       ├── components/    # React 组件
│       ├── pages/         # 页面
│       ├── stores/        # Zustand 状态管理
│       └── services/      # API/WS 服务
└── .github/workflows/     # CI
```
