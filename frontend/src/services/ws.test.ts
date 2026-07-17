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

  it('caps_network_reconnects_even_when_each_transport_opens_before_dropping', () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', MockWebSocket)

    wsService.connect('game-1', 'player-token')
    for (let attempt = 0; attempt < 6; attempt++) {
      const socket = MockWebSocket.instances[MockWebSocket.instances.length - 1]
      socket.onopen?.()
      socket.onclose?.({ code: 1006 } as CloseEvent)
      vi.runAllTimers()
    }

    expect(MockWebSocket.instances).toHaveLength(6)
  })
})
