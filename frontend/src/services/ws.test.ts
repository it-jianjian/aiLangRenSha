import { afterEach, describe, expect, it, vi } from 'vitest'
import { wsService } from './ws'

class MockWebSocket {
  static OPEN = 1
  static instances: MockWebSocket[] = []
  readonly url: string
  readyState = MockWebSocket.OPEN
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  send = vi.fn()
  close = vi.fn()

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }
}

describe('WebSocketService', () => {
  afterEach(() => {
    wsService.disconnect()
    MockWebSocket.instances = []
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('sends_player_token_only_in_the_authentication_first_frame', () => {
    vi.stubGlobal('WebSocket', MockWebSocket)

    wsService.connect('game-1', 'sensitive-player-token')
    const socket = MockWebSocket.instances[0]
    socket.onopen?.()

    expect(socket.url).toContain('/ws/game/game-1')
    expect(socket.url).not.toContain('?')
    expect(socket.url).not.toContain('sensitive-player-token')
    expect(socket.send).toHaveBeenCalledWith(JSON.stringify({
      type: 'authenticate', data: { player_token: 'sensitive-player-token' },
    }))
  })

  it.each([1000, 1008])('does_not_reconnect_after_normal_or_policy_close_%s', (code) => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', MockWebSocket)

    wsService.connect('game-1', 'invalid-token')
    const socket = MockWebSocket.instances[0]
    socket.onopen?.()
    socket.onclose?.({ code } as CloseEvent)
    vi.runAllTimers()

    expect(MockWebSocket.instances).toHaveLength(1)
  })

  it('keeps_reconnecting_indefinitely_on_network_drops', () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', MockWebSocket)

    wsService.connect('game-1', 'player-token')
    // 网络异常（1006）不再设重连次数上限，避免对局中途永久失联
    for (let attempt = 0; attempt < 7; attempt++) {
      const socket = MockWebSocket.instances[MockWebSocket.instances.length - 1]
      socket.onclose?.({ code: 1006 } as CloseEvent)
      vi.advanceTimersByTime(40_000) // 覆盖最大退避 30s + 抖动
    }

    expect(MockWebSocket.instances).toHaveLength(8)
  })

  it('filters_server_heartbeat_ping_from_business_handlers', () => {
    vi.stubGlobal('WebSocket', MockWebSocket)
    const handler = vi.fn()
    wsService.onMessage(handler)

    wsService.connect('game-1', 'player-token')
    const socket = MockWebSocket.instances[0]
    socket.onopen?.()

    socket.onmessage?.({ data: JSON.stringify({ type: 'ping', timestamp: 't' }) } as MessageEvent)
    expect(handler).not.toHaveBeenCalled()

    socket.onmessage?.({ data: JSON.stringify({ type: 'speech', data: {} }) } as MessageEvent)
    expect(handler).toHaveBeenCalledTimes(1)
  })

  it('forces_reconnect_when_heartbeat_times_out', () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', MockWebSocket)

    wsService.connect('game-1', 'player-token')
    const socket = MockWebSocket.instances[0]
    socket.onopen?.()

    // 超过 45s 未收到任何消息（含 ping）：判定半开连接，主动关闭并重连
    vi.advanceTimersByTime(50_000)
    expect(socket.close).toHaveBeenCalled()

    vi.advanceTimersByTime(2_000) // 首次重连退避 ≤1.25s
    expect(MockWebSocket.instances).toHaveLength(2)
  })

  it('stays_connected_when_messages_keep_arriving', () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', MockWebSocket)

    wsService.connect('game-1', 'player-token')
    const socket = MockWebSocket.instances[0]
    socket.onopen?.()

    // 每 20s 收到一次服务端 ping：看门狗不应触发强制重连
    for (let i = 0; i < 5; i++) {
      vi.advanceTimersByTime(20_000)
      socket.onmessage?.({ data: JSON.stringify({ type: 'ping', timestamp: 't' }) } as MessageEvent)
    }

    expect(socket.close).not.toHaveBeenCalled()
    expect(MockWebSocket.instances).toHaveLength(1)
  })

  it('notifies_reconnect_handlers_only_after_a_successful_reconnect', () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', MockWebSocket)
    const onReconnect = vi.fn()
    wsService.onReconnect(onReconnect)

    wsService.connect('game-1', 'player-token')
    MockWebSocket.instances[0].onopen?.()
    expect(onReconnect).not.toHaveBeenCalled() // 首次连接触发不回调

    MockWebSocket.instances[0].onclose?.({ code: 1006 } as CloseEvent)
    vi.advanceTimersByTime(2_000)
    MockWebSocket.instances[1].onopen?.()
    expect(onReconnect).toHaveBeenCalledTimes(1)
  })
})
