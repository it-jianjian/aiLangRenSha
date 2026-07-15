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
  async createGame(req: CreateGameRequest): Promise<{ game_id: string; mode: string; status: string }> {
    const res = await client.post('/games', req)
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

  /** 开始对局 */
  async startGame(gameId: string): Promise<{ game_id: string; status: string }> {
    const res = await client.post(`/games/${gameId}/start`)
    return unwrap(res)
  },

  /** 提交夜晚行动 */
  async submitNightAction(gameId: string, actionType: string, targetSeat: number | null, playerSeat: number) {
    const res = await client.post(`/games/${gameId}/actions/night`, {
      action_type: actionType,
      target_seat: targetSeat,
    }, { headers: { 'X-Player-Seat': playerSeat } })
    return unwrap(res)
  },

  /** 提交发言 */
  async submitSpeech(gameId: string, content: string, isPk: boolean, playerSeat: number) {
    const res = await client.post(`/games/${gameId}/actions/speech`, {
      content,
      is_pk: isPk,
    }, { headers: { 'X-Player-Seat': playerSeat } })
    return unwrap(res)
  },

  /** 提交投票 */
  async submitVote(gameId: string, targetSeat: number | null, isPkVote: boolean, playerSeat: number) {
    const res = await client.post(`/games/${gameId}/actions/vote`, {
      target_seat: targetSeat,
      is_pk_vote: isPkVote,
    }, { headers: { 'X-Player-Seat': playerSeat } })
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
