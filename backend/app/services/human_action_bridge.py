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
import time
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

TARGETED_ACTIONS = {"kill", "verify", "poison", "guard", "hunter_shoot", "vote"}

# S1: 认证失败限流 — 按 game_id 记录失败次数，超限封禁 60s
_auth_failures: dict[str, list[float]] = defaultdict(list)
_AUTH_BAN_WINDOW = 60  # 封禁时长（秒）
_AUTH_MAX_FAILURES = 5  # 每分钟最大失败次数


def _check_auth_rate_limit(game_id: str) -> None:
    """S1: 检查认证限流，超限抛出 HTTPException"""
    now = time.monotonic()
    window = now - _AUTH_BAN_WINDOW
    _auth_failures[game_id] = [t for t in _auth_failures[game_id] if t > window]
    if len(_auth_failures[game_id]) >= _AUTH_MAX_FAILURES:
        from fastapi import HTTPException
        raise HTTPException(status_code=429, detail="认证失败次数过多，请稍后重试")


def _record_auth_failure(game_id: str) -> None:
    """S1: 记录一次认证失败"""
    _auth_failures[game_id].append(time.monotonic())

# B1: 各行动类型默认超时（秒），超时按跳过继续，防止整局永久悬挂
DEFAULT_TIMEOUTS = {
    "kill": 120,
    "verify": 120,
    "save": 120,
    "poison": 120,
    "guard": 120,
    "vote": 120,
    "speech": 180,
    "last_words": 120,
    "hunter_shoot": 60,
    "skip": 60,
}


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
        # (game_id, seat) → asyncio.Event（等待人类操作时设置，API 提交后 set）
        # B2: 从单 game_id 槽位扩展为 (game_id, seat) 多槽位，支持同局多人类并发
        self._events: dict[tuple[str, int], asyncio.Event] = {}
        # (game_id, seat) → 操作数据
        self._actions: dict[tuple[str, int], dict[str, Any]] = {}
        # (game_id, seat) → 服务端声明的当前行动上下文
        self._contexts: dict[tuple[str, int], dict[str, Any]] = {}
        # S2: 已提交待消费的键集合，防止同一窗口重复提交覆盖
        self._submitted: set[tuple[str, int]] = set()

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
        key = (game_id, player_info["seat_number"])
        self._events[key] = event
        self._contexts[key] = {
            "action_type": action_type,
            "seat_number": player_info["seat_number"],
            "player_name": player_info.get("player_name"),
            "role": player_info.get("role"),
            "phase": player_info.get("phase"),
            "last_target": player_info.get("last_target"),
            "allowed_target_seats": set(player_info.get("allowed_target_seats", [])),
            "empty_target_actions": set(player_info.get("empty_target_actions", [])),
            # 角色专属附加信息（如女巫的被杀目标/药水状态），用于断线恢复时完整还原操作面板
            "extra": player_info.get("extra") or {},
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
                    "extra": player_info.get("extra") or {},
                },
            })
        except Exception as e:
            logger.debug(f"[HumanBridge] WebSocket 通知失败: {e}")

        logger.info(
            f"[HumanBridge] 等待人类操作: game={game_id} "
            f"seat={player_info.get('seat_number')} type={action_type}"
        )

        # B1: 未显式指定超时时使用行动类型默认超时，防止整局永久悬挂
        if timeout_seconds is None:
            timeout_seconds = DEFAULT_TIMEOUTS.get(action_type, 120)

        # 阻塞等待，直到 API 提交；超时按跳过继续
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            logger.info(
                f"[HumanBridge] 人类操作超时，按跳过继续: game={game_id} "
                f"seat={player_info.get('seat_number')} type={action_type}"
            )
        finally:
            action = self._actions.pop(key, {})
            self._events.pop(key, None)
            self._contexts.pop(key, None)
            self._submitted.discard(key)  # S2: 消费后清除提交标记

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
        key = (game_id, actor_seat)
        # S2: 已提交过则拒绝重复提交
        if key in self._submitted:
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail="该操作已提交，请勿重复提交")

        event = self._events.get(key)
        context = self._contexts.get(key)
        if not event or not context:
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail="当前没有等待中的人类操作")

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

        self._actions[key] = action_data
        self._submitted.add(key)  # S2: 标记已提交
        event.set()  # 唤醒等待中的 wait_for_action

    def get_pending_action(self, game_id: str, seat_number: int) -> dict[str, Any] | None:
        """查询指定座位当前等待中的操作提示（WS 断线重连 / HTTP 轮询兜底用）

        返回与 WebSocket human_action_prompt 推送一致的数据载荷，
        让重连后的客户端能恢复"轮到你了"的操作面板，避免对局永久等待。

        返回:
            None — 当前无等待操作，或等待中的操作不属于该座位
            dict — {action_type, seat, player_name, role, allowed_target_seats,
                    last_target, can_skip, extra}
        """
        context = self._contexts.get((game_id, seat_number))
        if not context:
            return None
        return {
            "action_type": context["action_type"],
            "seat": context["seat_number"],
            "player_name": context.get("player_name"),
            "role": context.get("role"),
            "allowed_target_seats": sorted(context["allowed_target_seats"]),
            "last_target": context.get("last_target"),
            "can_skip": context["action_type"] in context["empty_target_actions"],
            "extra": context.get("extra") or {},
        }

    def is_waiting(self, game_id: str, seat_number: int | None = None) -> bool:
        """检查指定对局（及可选座位）是否正在等待人类操作"""
        if seat_number is not None:
            return (game_id, seat_number) in self._events
        return any(gid == game_id for gid, _ in self._events)

    def cancel_wait(self, game_id: str, seat_number: int | None = None):
        """取消等待（如玩家超时或退出）"""
        if seat_number is not None:
            key = (game_id, seat_number)
            self._actions.pop(key, None)
            self._contexts.pop(key, None)
            event = self._events.pop(key, None)
            if event:
                event.set()
        else:
            # 取消该对局所有座位的等待
            keys_to_remove = [k for k in self._events if k[0] == game_id]
            for key in keys_to_remove:
                self._actions.pop(key, None)
                self._contexts.pop(key, None)
                event = self._events.pop(key, None)
                if event:
                    event.set()


# ─── 全局单例 ─────────────────────────────────────────────
human_bridge = HumanActionBridge()
