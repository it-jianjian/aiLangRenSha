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
  } | null

  // ─── WebSocket 消息流 ───
  messages: WSMessage[]

  // ─── Actions ───
  setGame: (gameId: string, mode: string) => void
  setPlayers: (players: PlayerInfo[]) => void
  setMyRole: (seat: number, role: string, companions?: number[]) => void
  addSpeech: (speech: Speech) => void
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
  messages: [] as WSMessage[],
}

export const useGameStore = create<GameStoreState>((set) => ({
  ...initialState,

  setGame: (gameId, mode) =>
    set({ gameId, gameMode: mode, gameStatus: 'waiting' }),

  setPlayers: (players) => set({ players }),

  setMyRole: (seat, role, companions) => set({ mySeat: seat, myRole: role, myCompanions: companions || [] }),

  addSpeech: (speech) =>
    set((state) => ({ speeches: [...state.speeches, speech] })),

  setVotes: (votes) => set({ votes }),

  setPhase: (round, phase) =>
    set({ currentRound: round, currentPhase: phase }),

  setWinner: (winner) =>
    set({ winner, gameStatus: 'finished' }),

  setActionPrompt: (prompt) => set({ actionPrompt: prompt }),
  clearActionPrompt: () => set({ actionPrompt: null }),

  addMessage: (message) =>
    set((state) => ({ messages: [...state.messages, message] })),

  reset: () => set({ ...initialState }),
}))
