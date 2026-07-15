// TypeScript 类型定义 — 匹配后端 API schemas

// ─── 通用响应 ────────────────────────────────────────────
export interface ApiResponse<T = any> {
  code: number
  message: string
  data: T
}

// ─── 对局相关 ────────────────────────────────────────────
export interface GameConfig {
  model_name: string
  temperature: number
}

export interface CreateGameRequest {
  mode: 'pure_ai' | 'mixed'
  player_name?: string
  config?: GameConfig
}

export interface PlayerInfo {
  seat_number: number
  player_name: string
  player_type: 'human' | 'ai'
  role?: string | null
  is_alive: boolean
}

export interface GameSummary {
  game_id: string
  mode: string
  status: 'waiting' | 'playing' | 'finished'
  winner?: string | null
  total_rounds: number
  created_at: string
  finished_at?: string | null
}

export interface GameDetail {
  game_id: string
  mode: string
  status: string
  current_round: number
  current_phase?: string | null
  winner?: string | null
  end_reason?: string | null
  players: PlayerInfo[]
  rounds: any[]
}

// ─── 操作相关 ────────────────────────────────────────────
export interface NightActionRequest {
  action_type: 'kill' | 'verify' | 'save' | 'poison' | 'skip'
  target_seat?: number | null
}

export interface SpeechRequest {
  content: string
  is_pk?: boolean
}

export interface VoteRequest {
  target_seat?: number | null
  is_pk_vote?: boolean
}

// ─── 回放相关 ────────────────────────────────────────────
export interface ReplayStep {
  step_index: number
  phase: string
  event_type: string
  description: string
  event_data?: any
}

export interface ReplayData {
  game_id: string
  total_steps: number
  steps: ReplayStep[]
  role_mapping: Record<string, string>
}

// ─── WebSocket 消息 ───────────────────────────────────────
export interface WSMessage {
  type: string
  data: any
  timestamp: string
}

// ─── WebSocket 消息类型枚举 ──────────────────────────────
export type WSMessageType =
  | 'game_started'
  | 'phase_change'
  | 'night_action_prompt'
  | 'night_result'
  | 'death_announce'
  | 'last_words'
  | 'speech'
  | 'speech_prompt'
  | 'vote_prompt'
  | 'vote_result'
  | 'pk_announce'
  | 'eliminate'
  | 'victory_check'
  | 'ai_thinking'
  | 'ai_reasoning'
  | 'error'
  | 'timeout_warning'
