/**
 * Zustand store 测试
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { useGameStore } from './gameStore'

describe('useGameStore', () => {
  beforeEach(() => {
    // 重置 store
    useGameStore.setState({
      gameId: null,
      gameMode: null,
      gameStatus: 'idle',
      players: [],
      speeches: [],
      votes: {},
      currentRound: 0,
      currentPhase: null,
      winner: null,
      actionPrompt: null,
      messages: [],
    })
  })

  it('initialState_shouldBeIdle', () => {
    const state = useGameStore.getState()
    expect(state.gameStatus).toBe('idle')
    expect(state.gameId).toBeNull()
  })

  it('setGame_shouldUpdateGameIdAndStatus', () => {
    useGameStore.getState().setGame('test-001', 'pure_ai')
    const state = useGameStore.getState()
    expect(state.gameId).toBe('test-001')
    expect(state.gameMode).toBe('pure_ai')
  })

  it('addSpeech_shouldAppendToSpeeches', () => {
    useGameStore.getState().addSpeech({ seat: 1, content: 'hello' })
    useGameStore.getState().addSpeech({ seat: 2, content: 'world' })
    const state = useGameStore.getState()
    expect(state.speeches).toHaveLength(2)
    expect(state.speeches[0].seat).toBe(1)
  })

  it('addMessage_shouldAppendToMessages', () => {
    useGameStore.getState().addMessage({ type: 'phase_change', data: { round: 1 }, timestamp: '' })
    const state = useGameStore.getState()
    expect(state.messages).toHaveLength(1)
    expect(state.messages[0].type).toBe('phase_change')
  })

  it('reset_shouldClearAllState', () => {
    useGameStore.getState().setGame('test-001', 'pure_ai')
    useGameStore.getState().addSpeech({ seat: 1, content: 'hello' })
    useGameStore.getState().reset()
    const state = useGameStore.getState()
    expect(state.gameId).toBeNull()
    expect(state.speeches).toHaveLength(0)
  })
})
