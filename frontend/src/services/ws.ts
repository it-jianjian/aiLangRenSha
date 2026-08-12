/**
 * WebSocket 客户端
 *
 * 职责：管理 WebSocket 连接，接收后端推送的游戏事件
 * 调用链：后端 ws_handler → WebSocket → wsService → Zustand store → React 组件
 *
 * 连接地址: ws://host/ws/game/{game_id}
 *
 * 云环境可用性设计：
 * - 心跳看门狗：超过 HEARTBEAT_TIMEOUT_MS 未收到任何消息（含服务端 ping），
 *   判定为半开连接，主动断开并重连（TCP 层面无法发现对端已死）
 * - 无限重连：指数退避封顶 30s + 随机抖动，不因连续失败而放弃对局
 * - 重连回调：重连成功后通知页面对账断线窗口内错过的事件与操作提示
 */

import type { WSMessage } from '../types'

type MessageHandler = (message: WSMessage) => void
type ReconnectHandler = () => void

/** 看门狗检查周期 */
const WATCHDOG_INTERVAL_MS = 10_000
/** 心跳超时阈值：需大于服务端心跳间隔（25s），小于代理空闲超时的重连代价可接受范围 */
const HEARTBEAT_TIMEOUT_MS = 45_000
/** 重连退避上限 */
const MAX_RECONNECT_DELAY_MS = 30_000

class WebSocketService {
  private ws: WebSocket | null = null
  private handlers: Set<MessageHandler> = new Set()
  private reconnectHandlers: Set<ReconnectHandler> = new Set()
  private reconnectAttempts = 0
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private watchdogTimer: ReturnType<typeof setInterval> | null = null
  private lastMessageAt = 0
  private hasConnectedBefore = false
  private manuallyClosed = false

  /** 连接到指定对局的 WebSocket */
  connect(gameId: string, playerToken?: string): void {
    this.closeTransport()
    this.reconnectAttempts = 0
    this.manuallyClosed = false
    this.hasConnectedBefore = false
    this.openTransport(gameId, playerToken)
  }

  private openTransport(gameId: string, playerToken?: string): void {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    const url = `${protocol}//${host}/ws/game/${gameId}`

    this.ws = new WebSocket(url)
    const socket = this.ws

    socket.onopen = () => {
      console.log('[WS] Connected to game:', gameId)
      this.reconnectAttempts = 0
      this.lastMessageAt = Date.now()
      this.startWatchdog(gameId, playerToken)
      if (playerToken) {
        this.send({ type: 'authenticate', data: { player_token: playerToken } })
      }
      // 重连成功（非首次连接）：通知上层对账断线窗口内错过的事件与操作提示
      if (this.hasConnectedBefore) {
        this.reconnectHandlers.forEach((handler) => handler())
      }
      this.hasConnectedBefore = true
    }

    socket.onmessage = (event) => {
      // 任何消息（含应用层 ping）都证明链路存活
      this.lastMessageAt = Date.now()
      try {
        const message: WSMessage = JSON.parse(event.data)
        // 应用层心跳仅用于保活与死连接探测，不进入业务消息流
        if (message.type === 'ping') return
        this.handlers.forEach((handler) => handler(message))
      } catch (e) {
        console.error('[WS] Parse error:', e)
      }
    }

    socket.onclose = (event) => {
      if (this.ws !== socket) return
      this.ws = null
      this.stopWatchdog()
      console.log('[WS] Disconnected:', event.code)
      const shouldReconnect = !this.manuallyClosed && event.code !== 1000 && event.code !== 1008
      if (shouldReconnect) {
        this.scheduleReconnect(gameId, playerToken)
      }
    }

    socket.onerror = (error) => {
      console.error('[WS] Error:', error)
    }
  }

  /** 无限重连：指数退避封顶 30s，叠加 ±25% 抖动避免多客户端同时重连风暴 */
  private scheduleReconnect(gameId: string, playerToken?: string): void {
    this.reconnectAttempts++
    const base = Math.min(1000 * 2 ** (this.reconnectAttempts - 1), MAX_RECONNECT_DELAY_MS)
    const delay = Math.round(base * (0.75 + Math.random() * 0.5))
    console.log(`[WS] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`)
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.openTransport(gameId, playerToken)
    }, delay)
  }

  /** 心跳看门狗：周期性检查链路活性，发现半开连接立即强制重连 */
  private startWatchdog(gameId: string, playerToken?: string): void {
    this.stopWatchdog()
    this.watchdogTimer = setInterval(() => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return
      if (Date.now() - this.lastMessageAt <= HEARTBEAT_TIMEOUT_MS) return
      console.warn('[WS] Heartbeat timeout, forcing reconnect')
      // 摘掉发僵连接的所有回调，避免其 onclose 与本次强制重连重复触发
      const stale = this.ws
      this.ws = null
      stale.onopen = null
      stale.onmessage = null
      stale.onclose = null
      stale.onerror = null
      try { stale.close() } catch { /* 半开连接关闭异常可忽略 */ }
      this.stopWatchdog()
      this.scheduleReconnect(gameId, playerToken)
    }, WATCHDOG_INTERVAL_MS)
  }

  private stopWatchdog(): void {
    if (this.watchdogTimer) {
      clearInterval(this.watchdogTimer)
      this.watchdogTimer = null
    }
  }

  private closeTransport(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.stopWatchdog()
    if (this.ws) {
      this.ws.onclose = null
      this.ws.close(1000, 'client disconnect')
      this.ws = null
    }
  }

  /** 断开连接 */
  disconnect(): void {
    this.manuallyClosed = true
    this.closeTransport()
    this.handlers.clear()
    this.reconnectHandlers.clear()
    this.reconnectAttempts = 0
  }

  /** 注册消息处理函数 */
  onMessage(handler: MessageHandler): () => void {
    this.handlers.add(handler)
    return () => this.handlers.delete(handler)
  }

  /** 注册重连成功回调（用于断线后重新对账公共事件与等待中的操作提示） */
  onReconnect(handler: ReconnectHandler): () => void {
    this.reconnectHandlers.add(handler)
    return () => this.reconnectHandlers.delete(handler)
  }

  /** 发送消息到后端 */
  send(data: any): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data))
    }
  }

  /** 是否已连接 */
  isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN
  }
}

export const wsService = new WebSocketService()
