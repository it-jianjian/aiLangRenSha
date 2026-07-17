import { act, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import GamePage from './GamePage'
import { useGameStore } from '../stores/gameStore'
import { wsService } from '../services/ws'

const apiMocks = vi.hoisted(() => ({ getGame: vi.fn(), getPublicEvents: vi.fn() }))

vi.mock('../services/api', () => ({
  apiService: apiMocks,
}))

vi.mock('../services/ws', () => ({
  wsService: { connect: vi.fn(), disconnect: vi.fn(), onMessage: vi.fn(() => () => {}) },
}))

describe('GamePage', () => {
  beforeEach(() => {
    useGameStore.getState().reset()
    apiMocks.getGame.mockResolvedValue({
      game_id: 'mixed-game', mode: 'mixed', status: 'playing', current_round: 0,
      players: [{ seat_number: 1, player_name: '玩家', player_type: 'human', role: null, is_alive: true }],
    })
    apiMocks.getPublicEvents.mockResolvedValue({ events: [] })
  })

  it('shows_identity_syncing_for_mixed_game_until_private_identity_is_restored', async () => {
    render(<MemoryRouter initialEntries={['/game/mixed-game']}><Routes><Route path="/game/:gameId" element={<GamePage />} /></Routes></MemoryRouter>)

    expect(await screen.findByText('正在同步你的私密身份信息…')).toBeInTheDocument()
    expect(screen.queryByText('纯AI模式，游戏自动运行')).not.toBeInTheDocument()
  })

  it('restores_the_human_role_when_identity_sync_arrives_after_first_frame_authentication', async () => {
    render(<MemoryRouter initialEntries={['/game/mixed-game']}><Routes><Route path="/game/:gameId" element={<GamePage />} /></Routes></MemoryRouter>)
    await screen.findByText('正在同步你的私密身份信息…')

    const handlers = vi.mocked(wsService.onMessage).mock.calls
    const handler = handlers[handlers.length - 1][0]
    act(() => handler({
      type: 'identity_sync',
      data: { seat: 1, role: 'werewolf', werewolf_companions: [4] },
      timestamp: '2026-07-16T12:00:01',
    }))

    expect(await screen.findByText('你的身份: 狼人 (1号)')).toBeInTheDocument()
    expect(screen.getByText('🐺 你的狼人同伴: 4号')).toBeInTheDocument()
  })

  it('keeps_a_generic_public_night_phase_notice_without_rendering_the_private_action_type', async () => {
    render(<MemoryRouter initialEntries={['/game/mixed-game']}><Routes><Route path="/game/:gameId" element={<GamePage />} /></Routes></MemoryRouter>)
    await screen.findByText('正在同步你的私密身份信息…')

    const handlers = vi.mocked(wsService.onMessage).mock.calls
    const handler = handlers[handlers.length - 1][0]
    act(() => handler({
      event_id: 'event-night',
      event_order: '2026-07-16T12:00:04:event-night',
      type: 'night_phase',
      data: { round: 3 },
      timestamp: '2026-07-16T12:00:04',
    }))

    expect(await screen.findByText('🌙 夜晚正在进行…')).toBeInTheDocument()
    expect(screen.queryByText(/狼人正在行动|预言家正在查验|女巫正在思考/)).not.toBeInTheDocument()
  })

  it('marks_the_eliminated_seat_dead_when_the_atomic_eliminate_event_arrives', async () => {
    render(<MemoryRouter initialEntries={['/game/mixed-game']}><Routes><Route path="/game/:gameId" element={<GamePage />} /></Routes></MemoryRouter>)
    await screen.findByText('正在同步你的私密身份信息…')

    const handlers = vi.mocked(wsService.onMessage).mock.calls
    const handler = handlers[handlers.length - 1][0]
    act(() => handler({
      event_id: 'event-eliminate',
      event_order: '2026-07-16T12:00:05:event-eliminate',
      type: 'eliminate',
      data: { seat: 1 },
      timestamp: '2026-07-16T12:00:05',
    }))

    expect(await screen.findByText('💀淘汰')).toBeInTheDocument()
    expect(screen.queryByText('存活')).not.toBeInTheDocument()
  })

  it('renders_only_server_allowed_targets_for_guard_prompt_and_shows_guard_role_in_chinese', async () => {
    apiMocks.getGame.mockResolvedValue({
      game_id: 'mixed-game', mode: 'mixed', status: 'playing', current_round: 1,
      players: [
        { seat_number: 1, player_name: '玩家', player_type: 'human', role: 'guard', is_alive: true },
        { seat_number: 2, player_name: 'AI-2', player_type: 'ai', role: null, is_alive: true },
        { seat_number: 3, player_name: 'AI-3', player_type: 'ai', role: null, is_alive: true },
        { seat_number: 4, player_name: 'AI-4', player_type: 'ai', role: null, is_alive: true },
      ],
    })
    render(<MemoryRouter initialEntries={['/game/mixed-game']}><Routes><Route path="/game/:gameId" element={<GamePage />} /></Routes></MemoryRouter>)
    await screen.findByText('守卫')

    const handlers = vi.mocked(wsService.onMessage).mock.calls
    const handler = handlers[handlers.length - 1][0]
    act(() => handler({
      type: 'identity_sync',
      data: { seat: 1, role: 'guard', werewolf_companions: [] },
      timestamp: '2026-07-16T12:00:01',
    }))
    act(() => handler({
      type: 'human_action_prompt',
      data: { action_type: 'guard', seat: 1, role: 'guard', allowed_target_seats: [2, 4], last_target: 3 },
      timestamp: '2026-07-16T12:00:02',
    }))

    expect(await screen.findByText(/可选目标: 2号、4号/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '2号' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '4号' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '3号' })).not.toBeInTheDocument()
  })
})
