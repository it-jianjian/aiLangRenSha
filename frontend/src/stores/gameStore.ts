/**
 * 游戏状态 Store（Zustand）
 *
 * 职责：管理对局运行时的全部前端状态
 * 调用链：WebSocket → store action → React 组件重渲染
 */

import { create } from 'zustand'
import type { WSMessage, PlayerInfo } from '../types'

export interface Speech {
  seat: number
  content: string
  isPk?: boolean
}

interface GameStoreState {
  // ─── 基础信息 ───
  gameId: string | null
  gameMode: string | null
  gameStatus: 'idle' | 'waiting' | 'playing' | 'finished'
  modelName: string

  // ─── 游戏数据 ───
  players: PlayerInfo[]
  speeches: Speech[]
  votes: Record<number, number | null>
  currentRound: number
  currentPhase: string | null
  winner: string | null

  // ─── 人类玩家身份（混合模式） ───
  myRole: string | null
  mySeat: number | null
  myCompanions: number[]

  // ─── 人类操作提示（轮到人类时由 WebSocket 推送） ───
  actionPrompt: {
    actionType: string
    seat: number
    role: string
    allowedTargetSeats?: number[]
    lastTarget?: number | null
    canSkip?: boolean
  } | null

  // ─── 阶段 2 流式发言 ───
  streamingSpeech: Record<number, string>  // seat → 累积文本
  streamingActive: boolean                 // 是否正在流式输出
  _streamingPending: Record<number, string>  // F1: chunk 缓冲区
  _streamingFlushScheduled: boolean          // F1: flush 是否已安排

  // ─── WebSocket 消息流 ───
  messages: WSMessage[]

  // ─── Actions ───
  setGame: (gameId: string, mode: string, modelName?: string) => void
  setPlayers: (players: PlayerInfo[]) => void
  markPlayerDead: (seat: number) => void
  setMyRole: (seat: number, role: string, companions?: number[]) => void
  addSpeech: (speech: Speech) => void
  appendStreamingSpeech: (seat: number, delta: string) => void
  endStreamingSpeech: (seat: number) => void
  clearStreamingSpeech: () => void
  setVotes: (votes: Record<number, number | null>) => void
  setPhase: (round: number, phase: string) => void
  setWinner: (winner: string) => void
  setActionPrompt: (prompt: any) => void
  clearActionPrompt: () => void
  addMessage: (message: WSMessage) => void
  reset: () => void
}

const initialState = {
  gameId: null as string | null,
  gameMode: null as string | null,
  gameStatus: 'idle' as const,
  modelName: '',
  players: [] as PlayerInfo[],
  speeches: [] as Speech[],
  votes: {} as Record<number, number | null>,
  currentRound: 0,
  currentPhase: null as string | null,
  winner: null as string | null,
  myRole: null as string | null,
  mySeat: null as number | null,
  myCompanions: [] as number[],
  actionPrompt: null as any,
  streamingSpeech: {} as Record<number, string>,
  streamingActive: false,
  _streamingPending: {} as Record<number, string>,
  _streamingFlushScheduled: false,
  messages: [] as WSMessage[],
}

export const useGameStore = create<GameStoreState>((set) => ({
  ...initialState,

  setGame: (gameId, mode, modelName) =>
    set({ gameId, gameMode: mode, gameStatus: 'waiting', modelName: modelName || '' }),

  setPlayers: (players) => set({ players }),
  markPlayerDead: (seat) => set((state) => ({
    players: state.players.map((player) => (
      player.seat_number === seat ? { ...player, is_alive: false } : player
    )),
  })),

  setMyRole: (seat, role, companions) => set({ mySeat: seat, myRole: role, myCompanions: companions || [] }),

  addSpeech: (speech) =>
    set((state) => ({ speeches: [...state.speeches, speech] })),

  // F1: chunk 缓冲合批 — 50ms 窗口内多个 chunk 合并为一次 setState
  appendStreamingSpeech: (seat, delta) =>
    set((state) => {
      const pending = state._streamingPending[seat] || ''
      state._streamingPending[seat] = pending + delta
      // 首次收到 chunk 时安排 flush
      if (!state._streamingFlushScheduled) {
        state._streamingFlushScheduled = true
        setTimeout(() => {
          set((s) => {
            const updates = { ...s.streamingSpeech }
            for (const [s2, text] of Object.entries(s._streamingPending)) {
              updates[Number(s2)] = (updates[Number(s2)] || '') + text
            }
            return {
              streamingSpeech: updates,
              streamingActive: true,
              _streamingPending: {},
              _streamingFlushScheduled: false,
            }
          })
        }, 50)
      }
      return { streamingActive: true }
    }),

  endStreamingSpeech: (seat) =>
    set((state) => ({ streamingActive: false })),

  clearStreamingSpeech: () =>
    set({ streamingSpeech: {}, streamingActive: false }),

  setVotes: (votes) => set({ votes }),

  setPhase: (round, phase) =>
    set({ currentRound: round, currentPhase: phase }),

  setWinner: (winner) =>
    set({ winner, gameStatus: 'finished' }),

  setActionPrompt: (prompt) => set({ actionPrompt: prompt }),
  clearActionPrompt: () => set({ actionPrompt: null }),

  addMessage: (message) =>
    set((state) => ({ messages: [...state.messages, message] })),

  reset: () => set({ ...initialState, streamingSpeech: {}, streamingActive: false, _streamingPending: {}, _streamingFlushScheduled: false }),
}))
