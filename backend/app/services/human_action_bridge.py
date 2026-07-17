"""AI 狼人杀 — 人类操作桥接器

职责：在游戏流程（后台异步任务）和 API 端点之间桥接人类玩家的操作
机制：asyncio.Event 实现阻塞等待 + 共享字典传递操作数据

调用链：
  游戏节点 → human_bridge.wait_for_action() → 阻塞等待
  API 端点 → human_bridge.submit_action() → 唤醒并传递数据
  游戏节点 → 读取数据继续执行

设计说明：
- 同一 FastAPI 事件循环内，后台任务和 API 处理器共享 asyncio.Event
- 每个 game_id 同时只有一个等待中的操作（游戏流程是串行的）
- wait_for_action 会通过 WebSocket 通知前端"轮到你了"
"""

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

TARGETED_ACTIONS = {"kill", "verify", "poison", "guard", "hunter_shoot", "vote"}


class HumanActionBridge:
    """人类操作桥接器

    使用 asyncio.Event 实现"游戏暂停 → 等待人类操作 → 恢复"机制

    使用方式：
        # 游戏节点中（阻塞等待）：
        action = await human_bridge.wait_for_action(game_id, "vote", {"seat": 1})
        target = action.get("target_seat")

        # API 端点中（唤醒）：
        human_bridge.submit_action(game_id, authenticated_seat, {"action_type": "vote", "target_seat": 3})
    """

    def __init__(self):
        # game_id → asyncio.Event（等待人类操作时设置，API 提交后 set）
        self._events: dict[str, asyncio.Event] = {}
        # game_id → 操作数据（API 提交时写入，游戏节点读取后清除）
        self._actions: dict[str, dict[str, Any]] = {}
        # game_id → 服务端声明的当前行动上下文；客户端无权覆盖。
        self._contexts: dict[str, dict[str, Any]] = {}

    async def wait_for_action(
        self,
        game_id: str,
        action_type: str,
        player_info: dict[str, Any],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """在游戏流程中阻塞等待人类玩家提交操作

        参数:
            game_id: 对局 ID
            action_type: 操作类型（kill/verify/save/poison/speech/vote）
            player_info: 服务端玩家上下文（座位、角色、阶段、合法目标和允许空目标动作）

        返回:
            人类提交的操作数据字典

        副作用:
            通过 WebSocket 向已认证绑定座位定向发送 human_action_prompt
        """
        event = asyncio.Event()
        self._events[game_id] = event
        self._contexts[game_id] = {
            "action_type": action_type,
            "seat_number": player_info["seat_number"],
            "role": player_info.get("role"),
            "phase": player_info.get("phase"),
            "allowed_target_seats": set(player_info.get("allowed_target_seats", [])),
            "empty_target_actions": set(player_info.get("empty_target_actions", [])),
        }

        # 通过 WebSocket 通知前端：轮到你了
        try:
            from app.api.ws_handler import ws_manager
            await ws_manager.send_to_seat(game_id, player_info["seat_number"], {
                "type": "human_action_prompt",
                "data": {
                    "action_type": action_type,
                    "seat": player_info.get("seat_number"),
                    "player_name": player_info.get("player_name"),
                    "role": player_info.get("role"),
                    "allowed_target_seats": list(player_info.get("allowed_target_seats", [])),
                    "last_target": player_info.get("last_target"),
                    "can_skip": action_type in player_info.get("empty_target_actions", []),
                },
            })
        except Exception as e:
            logger.debug(f"[HumanBridge] WebSocket 通知失败: {e}")

        logger.info(
            f"[HumanBridge] 等待人类操作: game={game_id} "
            f"seat={player_info.get('seat_number')} type={action_type}"
        )

        # 阻塞等待，直到 API 提交；可选超时用于死亡后的可选动作，避免整局永久悬挂。
        try:
            if timeout_seconds is None:
                await event.wait()
            else:
                await asyncio.wait_for(event.wait(), timeout=timeout_seconds)
        except TimeoutError:
            logger.info(
                f"[HumanBridge] 人类操作超时，按跳过继续: game={game_id} "
                f"seat={player_info.get('seat_number')} type={action_type}"
            )
        finally:
            action = self._actions.pop(game_id, {})
            self._events.pop(game_id, None)
            self._contexts.pop(game_id, None)

        logger.info(
            f"[HumanBridge] 收到人类操作: game={game_id} "
            f"action={action}"
        )

        return action

    def submit_action(self, game_id: str, actor_seat: int, action_data: dict[str, Any]):
        """API 端点提交人类操作，唤醒等待中的游戏流程

        参数:
            game_id: 对局 ID
            action_data: 操作数据（如 {"target_seat": 3} 或 {"content": "发言文本"}）
        """
        event = self._events.get(game_id)
        context = self._contexts.get(game_id)
        if not event or not context:
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail="当前没有等待中的人类操作")
        if actor_seat != context["seat_number"]:
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="当前等待操作不属于该座位")

        expected_action = context["action_type"]
        submitted_action = action_data.get("action_type")
        allowed_actions = {expected_action}
        if expected_action == "save":
            allowed_actions.update({"poison", "skip"})
        if submitted_action not in allowed_actions:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="动作类型与当前等待操作不匹配")

        target = action_data.get("target_seat")
        if submitted_action in TARGETED_ACTIONS:
            if target is None and submitted_action not in context["empty_target_actions"]:
                from fastapi import HTTPException
                raise HTTPException(status_code=400, detail="当前动作目标不能为空")
            allowed_targets = context["allowed_target_seats"]
            if target is not None and (not allowed_targets or target not in allowed_targets):
                from fastapi import HTTPException
                raise HTTPException(status_code=400, detail="目标不是当前行动的合法目标")

        self._actions[game_id] = action_data
        event.set()  # 唤醒等待中的 wait_for_action

    def is_waiting(self, game_id: str) -> bool:
        """检查指定对局是否正在等待人类操作"""
        return game_id in self._events

    def cancel_wait(self, game_id: str):
        """取消等待（如玩家超时或退出）"""
        self._actions.pop(game_id, None)
        self._contexts.pop(game_id, None)
        event = self._events.pop(game_id, None)
        if event:
            event.set()  # 唤醒但数据为空，调用方需处理空数据


# ─── 全局单例 ─────────────────────────────────────────────
human_bridge = HumanActionBridge()
