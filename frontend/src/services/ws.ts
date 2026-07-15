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

  /** 连接到指定对局的 WebSocket */
  connect(gameId: string): void {
    if (this.ws) {
      this.disconnect()
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    const url = `${protocol}//${host}/ws/game/${gameId}`

    this.ws = new WebSocket(url)

    this.ws.onopen = () => {
      this.reconnectAttempts = 0
      console.log('[WS] Connected to game:', gameId)
    }

    this.ws.onmessage = (event) => {
      try {
        const message: WSMessage = JSON.parse(event.data)
        this.handlers.forEach((handler) => handler(message))
      } catch (e) {
        console.error('[WS] Parse error:', e)
      }
    }

    this.ws.onclose = () => {
      console.log('[WS] Disconnected')
      if (this.reconnectAttempts < this.maxReconnectAttempts) {
        this.reconnectAttempts++
        const delay = 1000 * this.reconnectAttempts
        console.log(`[WS] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`)
        setTimeout(() => this.connect(gameId), delay)
      }
    }

    this.ws.onerror = (error) => {
      console.error('[WS] Error:', error)
    }
  }

  /** 断开连接 */
  disconnect(): void {
    if (this.ws) {
      this.ws.onclose = null // 防止自动重连
      this.ws.close()
      this.ws = null
    }
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
