/**
 * REST API 客户端
 *
 * 封装所有后端 HTTP 接口调用
 * 调用链：前端组件 → apiService → axios → 后端 REST API
 */

import axios from 'axios'
import type {
  ApiResponse,
  CreateGameRequest,
  GameSummary,
  GameDetail,
  ReplayData,
  GameCreated,
  Roster,
  RosterValidation,
} from '../types'

// 创建 axios 实例
const client = axios.create({
  baseURL: '/api/v1',
  headers: { 'Content-Type': 'application/json' },
})

// 响应拦截器：提取 data 字段
client.interceptors.response.use(
  (response) => response,
  (error) => {
    console.error('[API Error]', error)
    return Promise.reject(error)
  },
)

/**
 * 从 ApiResponse 中提取 data 字段
 * 后端统一返回 { code, message, data } 格式
 */
function unwrap<T>(response: { data: ApiResponse<T> }): T {
  const { code, message, data } = response.data
  if (code !== 0) {
    throw new Error(`API Error ${code}: ${message}`)
  }
  return data
}

// ─── 对局相关 API ────────────────────────────────────────

export const apiService = {
  /** 创建对局 */
  async createGame(req: CreateGameRequest): Promise<GameCreated> {
    const res = await client.post('/games', req)
    return unwrap(res)
  },

  async updateRoster(gameId: string, ownerToken: string, playerCount: 6 | 12, rosterType: 'official' | 'custom', roster: Roster): Promise<{ validation: RosterValidation }> {
    const res = await client.patch(`/games/${gameId}/roster`, { player_count: playerCount, roster_type: rosterType, roster }, { headers: { 'X-Owner-Token': ownerToken } })
    return unwrap(res)
  },

  async resetOfficialRoster(gameId: string, ownerToken: string): Promise<{ player_count: number; roster: Roster; validation: RosterValidation }> {
    const res = await client.post(`/games/${gameId}/roster/reset-official`, {}, { headers: { 'X-Owner-Token': ownerToken } })
    return unwrap(res)
  },

  /** 对局列表 */
  async listGames(page = 1, pageSize = 10): Promise<{ total: number; items: GameSummary[] }> {
    const res = await client.get('/games', { params: { page, page_size: pageSize } })
    return unwrap(res)
  },

  /** 对局详情 */
  async getGame(gameId: string): Promise<GameDetail> {
    const res = await client.get(`/games/${gameId}`)
    return unwrap(res)
  },

  async getPublicEvents(gameId: string): Promise<{ events: import('../types').WSMessage[] }> {
    const res = await client.get(`/games/${gameId}/events`)
    return unwrap(res)
  },

  /** 开始对局 */
  async startGame(gameId: string, ownerToken?: string): Promise<{ game_id: string; status: string }> {
    const res = await client.post(`/games/${gameId}/start`, {}, ownerToken ? { headers: { 'X-Owner-Token': ownerToken } } : undefined)
    return unwrap(res)
  },

  /** 提交夜晚行动 */
  async submitNightAction(gameId: string, actionType: string, targetSeat: number | null, playerToken: string) {
    const res = await client.post(`/games/${gameId}/actions/night`, {
      action_type: actionType,
      target_seat: targetSeat,
    }, { headers: { 'X-Player-Token': playerToken } })
    return unwrap(res)
  },

  /** 提交发言 */
  async submitSpeech(gameId: string, content: string, isPk: boolean, playerToken: string, actionType: 'speech' | 'last_words' = 'speech') {
    const res = await client.post(`/games/${gameId}/actions/speech`, {
      content,
      is_pk: isPk,
      action_type: actionType,
    }, { headers: { 'X-Player-Token': playerToken } })
    return unwrap(res)
  },

  /** 提交投票 */
  async submitVote(gameId: string, targetSeat: number | null, isPkVote: boolean, playerToken: string) {
    const res = await client.post(`/games/${gameId}/actions/vote`, {
      target_seat: targetSeat,
      is_pk_vote: isPkVote,
    }, { headers: { 'X-Player-Token': playerToken } })
    return unwrap(res)
  },

  /** 获取回放数据 */
  async getReplay(gameId: string): Promise<ReplayData> {
    const res = await client.get(`/games/${gameId}/replay`)
    return unwrap(res)
  },
}

// 导出 axios 实例（供测试 mock）
export { client as axiosClient }
