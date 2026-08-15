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
  player_count?: 6 | 12
  roster_type?: 'official' | 'custom'
  roster?: Roster
  config?: GameConfig
}

export type PlayerRole = 'werewolf' | 'villager' | 'seer' | 'witch' | 'hunter' | 'guard'
export type Roster = Record<PlayerRole, number>
export interface RosterValidation { valid: boolean; errors: string[] }
export interface GameCreated {
  game_id: string; mode: string; status: string; owner_token: string; player_token?: string | null
  player_count: number; roster_type: 'official' | 'custom'; roster: Roster; validation: RosterValidation
}

export interface PlayerInfo {
  seat_number: number
  player_name: string
  player_type: 'human' | 'ai'
  role?: string | null
  is_alive: boolean
  llm_model_name?: string  // 该 AI 玩家使用的模型名称
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
  player_count: number
  roster_type: 'official' | 'custom'
  roster: Roster
  roster_locked: boolean
  model_name: string
}

// ─── 操作相关 ────────────────────────────────────────────
export interface NightActionRequest {
  action_type: 'kill' | 'verify' | 'save' | 'poison' | 'guard' | 'hunter_shoot' | 'skip'
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
  round?: number
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
  player_count: number
  roster: Roster
  winner: string
  end_reason: string
}

// ─── WebSocket 消息 ───────────────────────────────────────
export interface WSMessage {
  event_id?: string
  event_order?: string
  type: string
  data: any
  timestamp: string
}

// ─── WebSocket 消息类型枚举 ──────────────────────────────
export type WSMessageType =
  | 'game_started'
  | 'identity_sync'
  | 'phase_change'
  | 'night_action_prompt'
  | 'night_result'
  | 'death_announce'
  | 'last_words'
  | 'speech'
  | 'speech_prompt'
  | 'speech_chunk'
  | 'speech_end'
  | 'vote_prompt'
  | 'vote_result'
  | 'pk_announce'
  | 'eliminate'
  | 'victory_check'
  | 'game_over'
  | 'ai_thinking'
  | 'ai_reasoning'
  | 'error'
  | 'timeout_warning'

// ─── 流式发言事件 ────────────────────────────────────────
export interface SpeechChunkMessage {
  type: 'speech_chunk'
  data: { seat: number; round: number; delta: string }
  timestamp: string
}

export interface SpeechEndMessage {
  type: 'speech_end'
  data: { seat: number; round: number }
  timestamp: string
}
