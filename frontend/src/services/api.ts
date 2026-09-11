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
  ReviewData,
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

// 请求拦截器：自动携带登录 token（可选登录）
client.interceptors.request.use((config) => {
  const token = localStorage.getItem('ww-token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

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

  /** 查询当前等待该人类玩家提交的操作提示（WS 断线重连 / 轮询兜底恢复用） */
  async getPendingAction(gameId: string, playerToken: string): Promise<{ action: any | null }> {
    const res = await client.get(`/games/${gameId}/pending_action`, { headers: { 'X-Player-Token': playerToken } })
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

  /** 获取可选模型列表 + 各座位模型覆盖 */
  async getSeatModels(gameId: string): Promise<{ available: string[]; player_count: number; seat_models: Record<string, string> }> {
    const res = await client.get(`/games/${gameId}/models`)
    return unwrap(res)
  },

  /** 房主设置各座位对应模型（仅 waiting） */
  async setSeatModels(gameId: string, seatModels: Record<string, string>, ownerToken: string) {
    const res = await client.put(`/games/${gameId}/models`, { seat_models: seatModels }, { headers: { 'X-Owner-Token': ownerToken } })
    return unwrap(res)
  },

  /** 玩家为自己座位设模型（仅 waiting） */
  async setMyModel(gameId: string, model: string, playerToken: string) {
    const res = await client.put(`/games/${gameId}/my_model`, { model }, { headers: { 'X-Player-Token': playerToken } })
    return unwrap(res)
  },

  /** 自定义模型池 */
  async listModelPool(): Promise<{ items: { name: string; base_url: string; temperature: number | null; api_key_masked: string }[] }> {
    const res = await client.get('/models')
    return unwrap(res)
  },
  async upsertModelPool(model: { name: string; api_key: string; base_url: string; temperature?: number | null }) {
    const res = await client.post('/models', model)
    return unwrap(res)
  },
  async deleteModelPool(name: string) {
    const res = await client.delete(`/models/${encodeURIComponent(name)}`)
    return unwrap(res)
  },

  /** 账号（可选登录） */
  async register(req: { username: string; password: string; nickname?: string; email?: string }): Promise<{ token: string; user: any }> {
    const res = await client.post('/auth/register', req)
    return unwrap(res)
  },
  async login(req: { username: string; password: string }): Promise<{ token: string; user: any }> {
    const res = await client.post('/auth/login', req)
    return unwrap(res)
  },
  async me(): Promise<{ user: any }> {
    const res = await client.get('/auth/me')
    return unwrap(res)
  },
  async updateMe(req: { nickname?: string; avatar?: string; bio?: string; email?: string }): Promise<{ user: any }> {
    const res = await client.put('/auth/me', req)
    return unwrap(res)
  },
  async myGames(): Promise<{ items: any[] }> {
    const res = await client.get('/auth/me/games')
    return unwrap(res)
  },
  async myStats(): Promise<any> {
    const res = await client.get('/auth/me/stats')
    return unwrap(res)
  },

  /** 获取回放数据 */
  async getReplay(gameId: string): Promise<ReplayData> {
    const res = await client.get(`/games/${gameId}/replay`)
    return unwrap(res)
  },

  /** 获取复盘数据（胜率曲线实时计算 + 已缓存 AI 点评） */
  async getReview(gameId: string): Promise<ReviewData> {
    const res = await client.get(`/games/${gameId}/review`)
    return unwrap(res)
  },

  /** 生成 / 重新生成 AI 复盘点评 */
  async generateReview(gameId: string): Promise<ReviewData> {
    const res = await client.post(`/games/${gameId}/review/generate`)
    return unwrap(res)
  },
}

// 导出 axios 实例（供测试 mock）
export { client as axiosClient }
