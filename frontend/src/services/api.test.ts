/**
 * API Service 单元测试
 *
 * Mock axiosClient 的 get/post 方法
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { apiService, axiosClient } from './api'

describe('apiService', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('createGame', () => {
    it('createGame_pureAi_shouldReturnGameId', async () => {
      // Arrange
      vi.spyOn(axiosClient, 'post').mockResolvedValue({
        data: {
          code: 0,
          message: 'ok',
          data: { game_id: 'test-001', mode: 'pure_ai', status: 'waiting' },
        },
      })

      // Act
      const result = await apiService.createGame({ mode: 'pure_ai' })

      // Assert
      expect(result.game_id).toBe('test-001')
      expect(result.mode).toBe('pure_ai')
    })

    it('createGame_mixed_shouldIncludePlayerName', async () => {
      // Arrange
      vi.spyOn(axiosClient, 'post').mockResolvedValue({
        data: {
          code: 0,
          message: 'ok',
          data: { game_id: 'test-002', mode: 'mixed', status: 'waiting' },
        },
      })

      // Act
      const result = await apiService.createGame({ mode: 'mixed', player_name: '张三' })

      // Assert
      expect(result.game_id).toBe('test-002')
    })

    it('createGame_shouldReturnOwnerTokenAndRosterSnapshot', async () => {
      vi.spyOn(axiosClient, 'post').mockResolvedValue({
        data: {
          code: 0,
          message: 'ok',
          data: {
            game_id: 'twelve-player-game',
            mode: 'pure_ai',
            status: 'waiting',
            owner_token: 'one-time-token',
            player_count: 12,
            roster_type: 'official',
            roster: { werewolf: 4, villager: 4, seer: 1, witch: 1, hunter: 1, guard: 1 },
            validation: { valid: true, errors: [] },
          },
        },
      })

      const result = await apiService.createGame({
        mode: 'pure_ai',
        player_count: 12,
        roster_type: 'official',
        roster: { werewolf: 4, villager: 4, seer: 1, witch: 1, hunter: 1, guard: 1 },
      })

      expect(result.owner_token).toBe('one-time-token')
      expect(result.player_count).toBe(12)
      expect(result.roster.hunter).toBe(1)
    })

    it('updateRoster_shouldSendOwnerTokenAndWholeRosterSnapshot', async () => {
      vi.spyOn(axiosClient, 'patch').mockResolvedValue({
        data: {
          code: 0,
          message: 'ok',
          data: { validation: { valid: true, errors: [] } },
        },
      })
      const roster = { werewolf: 2, villager: 1, seer: 1, witch: 1, hunter: 1, guard: 0 }

      await apiService.updateRoster('game-1', 'owner-token', 6, 'custom', roster)

      expect(axiosClient.patch).toHaveBeenCalledWith(
        '/games/game-1/roster',
        { player_count: 6, roster_type: 'custom', roster },
        { headers: { 'X-Owner-Token': 'owner-token' } },
      )
    })
  })

  describe('listGames', () => {
    it('listGames_shouldReturnTotalAndItems', async () => {
      // Arrange
      vi.spyOn(axiosClient, 'get').mockResolvedValue({
        data: {
          code: 0,
          message: 'ok',
          data: {
            total: 2,
            items: [
              { game_id: 'g1', mode: 'pure_ai', status: 'finished', winner: 'werewolf', total_rounds: 3, created_at: '2026-07-13T10:00:00', finished_at: '2026-07-13T10:05:00' },
              { game_id: 'g2', mode: 'mixed', status: 'playing', winner: null, total_rounds: 1, created_at: '2026-07-13T10:10:00', finished_at: null },
            ],
          },
        },
      })

      // Act
      const result = await apiService.listGames()

      // Assert
      expect(result.total).toBe(2)
      expect(result.items).toHaveLength(2)
      expect(result.items[0].game_id).toBe('g1')
    })
  })

  describe('getGame', () => {
    it('getGame_shouldReturnDetail', async () => {
      // Arrange
      vi.spyOn(axiosClient, 'get').mockResolvedValue({
        data: {
          code: 0,
          message: 'ok',
          data: {
            game_id: 'test-001',
            mode: 'pure_ai',
            status: 'playing',
            current_round: 2,
            winner: null,
            end_reason: null,
            players: [
              { seat_number: 1, player_name: 'AI-1', player_type: 'ai', role: null, is_alive: true },
            ],
            rounds: [],
          },
        },
      })

      // Act
      const result = await apiService.getGame('test-001')

      // Assert
      expect(result.game_id).toBe('test-001')
      expect(result.players).toHaveLength(1)
    })
  })

  describe('startGame', () => {
    it('startGame_shouldReturnPlayingStatus', async () => {
      // Arrange
      vi.spyOn(axiosClient, 'post').mockResolvedValue({
        data: {
          code: 0,
          message: 'ok',
          data: { game_id: 'test-001', status: 'playing' },
        },
      })

      // Act
      const result = await apiService.startGame('test-001')

      // Assert
      expect(result.status).toBe('playing')
    })
  })

  describe('submitSpeech', () => {
    it('submitSpeech_lastWords_shouldDeclareThePromptActionContract', async () => {
      vi.spyOn(axiosClient, 'post').mockResolvedValue({
        data: { code: 0, message: 'ok', data: { accepted: true } },
      })

      await apiService.submitSpeech('game-1', '请好人继续找狼', false, 'player-token', 'last_words')

      expect(axiosClient.post).toHaveBeenCalledWith(
        '/games/game-1/actions/speech',
        { content: '请好人继续找狼', is_pk: false, action_type: 'last_words' },
        { headers: { 'X-Player-Token': 'player-token' } },
      )
    })
  })

  describe('getReplay', () => {
    it('getReplay_shouldReturnSteps', async () => {
      // Arrange
      vi.spyOn(axiosClient, 'get').mockResolvedValue({
        data: {
          code: 0,
          message: 'ok',
          data: {
            game_id: 'test-001',
            total_steps: 3,
            steps: [
              { step_index: 0, phase: 'system', event_type: 'role_assign', description: '角色分配', event_data: null },
              { step_index: 1, phase: 'night', event_type: 'night_kill', description: '狼人击杀', event_data: null },
              { step_index: 2, phase: 'day', event_type: 'speech', description: '发言', event_data: null },
            ],
            role_mapping: { '1': 'werewolf', '2': 'villager' },
          },
        },
      })

      // Act
      const result = await apiService.getReplay('test-001')

      // Assert
      expect(result.total_steps).toBe(3)
      expect(result.steps).toHaveLength(3)
      expect(result.role_mapping['1']).toBe('werewolf')
    })
  })
})
