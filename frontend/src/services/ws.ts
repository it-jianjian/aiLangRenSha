/**
 * WebSocket 客户端
 *
 * 职责：管理 WebSocket 连接，接收后端推送的游戏事件
 * 调用链：后端 ws_handler → WebSocket → wsService → Zustand store → React 组件
 *
 * 连接地址: ws://host/ws/game/{game_id}
 */

import type { WSMessage } from '../types'

type MessageHandler = (message: WSMessage) => void

class WebSocketService {
  private ws: WebSocket | null = null
  private handlers: Set<MessageHandler> = new Set()
  private reconnectAttempts = 0
  private maxReconnectAttempts = 5
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private manuallyClosed = false

  /** 连接到指定对局的 WebSocket */
  connect(gameId: string, playerToken?: string): void {
    this.closeTransport()
    this.reconnectAttempts = 0
    this.manuallyClosed = false
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
      if (playerToken) {
        this.send({ type: 'authenticate', data: { player_token: playerToken } })
      }
    }

    socket.onmessage = (event) => {
      try {
        const message: WSMessage = JSON.parse(event.data)
        this.handlers.forEach((handler) => handler(message))
      } catch (e) {
        console.error('[WS] Parse error:', e)
      }
    }

    socket.onclose = (event) => {
      if (this.ws === socket) this.ws = null
      console.log('[WS] Disconnected:', event.code)
      const shouldReconnect = !this.manuallyClosed && event.code !== 1000 && event.code !== 1008
      if (shouldReconnect && this.reconnectAttempts < this.maxReconnectAttempts) {
        this.reconnectAttempts++
        const delay = Math.min(1000 * 2 ** (this.reconnectAttempts - 1), 30000)
        console.log(`[WS] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`)
        this.reconnectTimer = setTimeout(() => {
          this.reconnectTimer = null
          this.openTransport(gameId, playerToken)
        }, delay)
      }
    }

    socket.onerror = (error) => {
      console.error('[WS] Error:', error)
    }
  }

  private closeTransport(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
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
    this.reconnectAttempts = 0
  }

  /** 注册消息处理函数 */
  onMessage(handler: MessageHandler): () => void {
    this.handlers.add(handler)
    return () => this.handlers.delete(handler)
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
