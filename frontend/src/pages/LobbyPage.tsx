/**
 * 游戏大厅页面
 *
 * 职责：模式选择 + 创建对局 + 历史对局列表
 * 路由：/
 */

import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Table, Space, message, Tag, InputNumber } from 'antd'
import { ThunderboltOutlined } from '@ant-design/icons'
import { apiService } from '../services/api'
import type { GameSummary, Roster } from '../types'
import './LobbyPage.css'

const roleCN: Record<string, string> = {
  werewolf: '狼人', villager: '村民', seer: '预言家', witch: '女巫', hunter: '猎人', guard: '守卫',
}

export default function LobbyPage() {
  const navigate = useNavigate()
  const [selectedMode, setSelectedMode] = useState<'pure_ai' | 'mixed' | null>(null)
  const [playerCount, setPlayerCount] = useState<6 | 12>(6)
  const [rosterType, setRosterType] = useState<'official' | 'custom'>('official')
  const officialRoster = (count: 6 | 12): Roster => ({ werewolf: count === 6 ? 2 : 4, villager: count === 6 ? 2 : 4, seer: 1, witch: 1, hunter: count === 12 ? 1 : 0, guard: count === 12 ? 1 : 0 })
  const [roster, setRoster] = useState<Roster>(officialRoster(6))
  const updateCount = (count: 6 | 12) => { setPlayerCount(count); setRosterType('official'); setRoster(officialRoster(count)) }
  const validationErrors = [
    Object.values(roster).reduce((sum, value) => sum + value, 0) !== playerCount ? `当前 ${Object.values(roster).reduce((sum, value) => sum + value, 0)} / ${playerCount} 人` : '',
    roster.werewolf !== (playerCount === 6 ? 2 : 4) ? `${playerCount} 人局狼人必须为 ${playerCount === 6 ? 2 : 4} 名` : '',
    ...(['seer', 'witch', 'hunter', 'guard'] as const).filter(role => roster[role] > 1).map(role => `${role} 最多 1 名`),
  ].filter(Boolean)
  const [creating, setCreating] = useState(false)
  const [games, setGames] = useState<GameSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [playerName, setPlayerName] = useState('')

  useEffect(() => { loadGames() }, [])

  const loadGames = async () => {
    try {
      setLoading(true)
      const { items } = await apiService.listGames()
      setGames(items)
    } catch {
      // 忽略
    } finally {
      setLoading(false)
    }
  }

  const handleCreate = async () => {
    if (!selectedMode) { message.warning('请先选择游戏模式'); return }
    if (selectedMode === 'mixed' && !playerName) { message.warning('请输入玩家昵称'); return }
    try {
      setCreating(true)
      const result = await apiService.createGame({
        mode: selectedMode,
        player_name: selectedMode === 'mixed' ? playerName : undefined,
        player_count: playerCount,
        roster_type: rosterType,
        roster,
      })
      sessionStorage.setItem(`game-owner-token:${result.game_id}`, result.owner_token)
      if (result.player_token) sessionStorage.setItem(`game-player-token:${result.game_id}`, result.player_token)
      message.success('对局创建成功！')
      navigate(`/game/${result.game_id}`)
    } catch {
      message.error('创建对局失败')
    } finally {
      setCreating(false)
    }
  }

  const columns = [
    { title: '对局ID', dataIndex: 'game_id', key: 'game_id', ellipsis: true, width: 220 },
    { title: '模式', dataIndex: 'mode', key: 'mode', width: 80, render: (v: string) => (
      v === 'pure_ai' ? <Tag color="blue">纯AI</Tag> : <Tag color="green">混合</Tag>
    )},
    { title: '状态', dataIndex: 'status', key: 'status', width: 80, render: (v: string) => {
      const map: Record<string, [string, string]> = { waiting: ['default', '等待'], playing: ['processing', '进行中'], finished: ['success', '已结束'] }
      const [color, label] = map[v] || ['default', v]
      return <Tag color={color}>{label}</Tag>
    }},
    { title: '胜方', dataIndex: 'winner', key: 'winner', width: 70, render: (v: string | null) => (v === 'werewolf' ? '狼人' : v === 'goods' ? '好人' : '-')},
    { title: '轮数', dataIndex: 'total_rounds', key: 'total_rounds', width: 60 },
    { title: '操作', key: 'action', width: 80, render: (_: any, record: GameSummary) => (
      record.status === 'finished'
        ? <Button size="small" onClick={() => navigate(`/replay/${record.game_id}`)}>回放</Button>
        : <Button size="small" type="primary" onClick={() => navigate(`/game/${record.game_id}`)}>{record.status === 'waiting' ? '开始' : '进入'}</Button>
    )},
  ]

  return (
    <div className="lobby-page">
      <div className="lobby-page__inner">
        {/* ─── 标题 ─── */}
        <div className="lobby-header">
          <h1 className="lobby-header__title">AI 狼人杀</h1>
          <p className="lobby-header__subtitle">基于多 Agent 推理的狼人杀对战平台</p>
        </div>

        {/* ─── 模式选择 ─── */}
        <div className="mode-cards">
          <div
            className={`mode-card mode-card--pure-ai ${selectedMode === 'pure_ai' ? 'mode-card--selected pure-ai' : ''}`}
            onClick={() => setSelectedMode('pure_ai')}
          >
            <div className="mode-card__icon">🤖</div>
            <h3 className="mode-card__title">纯 AI 对战</h3>
            <p className="mode-card__desc">{playerCount} 个 AI Agent 自动完成一局<br/>可旁观观察推理过程</p>
          </div>
          <div
            className={`mode-card mode-card--mixed ${selectedMode === 'mixed' ? 'mode-card--selected mixed' : ''}`}
            onClick={() => setSelectedMode('mixed')}
          >
            <div className="mode-card__icon">👤</div>
            <h3 className="mode-card__title">人类 + AI 混合</h3>
            <p className="mode-card__desc">1 名人类玩家 + {playerCount - 1} 个 AI<br/>亲身体验推理与博弈</p>
          </div>
        </div>

        {/* ─── 昵称输入 ─── */}
        {selectedMode === 'mixed' && (
          <div className="lobby-nickname">
            <input
              placeholder="输入你的昵称"
              value={playerName}
              onChange={(e) => setPlayerName(e.target.value)}
            />
          </div>
        )}

        {/* ─── 人数选择 ─── */}
        <div className="lobby-player-count">
          <span className="lobby-player-count__label">对局人数：</span>
          <div className="lobby-radio-group">
            <input type="radio" name="pcount" id="p6" value={6} checked={playerCount === 6} onChange={() => updateCount(6)} />
            <label htmlFor="p6">6 人经典</label>
            <input type="radio" name="pcount" id="p12" value={12} checked={playerCount === 12} onChange={() => updateCount(12)} />
            <label htmlFor="p12">12 人进阶</label>
          </div>
        </div>

        {/* ─── 阵容配置 ─── */}
        <div className="lobby-roster">
          <div className="lobby-roster__header">
            <span className="lobby-roster__title">阵容配置</span>
            <div className="lobby-roster__type-toggle">
              <input type="radio" name="rtype" id="rofficial" checked={rosterType === 'official'} onChange={() => { setRosterType('official'); setRoster(officialRoster(playerCount)) }} />
              <label htmlFor="rofficial">官方默认</label>
              <input type="radio" name="rtype" id="rcustom" checked={rosterType === 'custom'} onChange={() => setRosterType('custom')} />
              <label htmlFor="rcustom">自定义</label>
            </div>
          </div>
          <div className="lobby-roster__roles">
            {(Object.keys(roster) as (keyof Roster)[]).map(role => (
              <span key={role} className="lobby-roster__role">
                {roleCN[role]}：
                <InputNumber
                  size="small"
                  min={0}
                  max={role === 'werewolf' ? (playerCount === 6 ? 2 : 4) : role === 'villager' ? playerCount : 1}
                  disabled={rosterType === 'official' || role === 'werewolf'}
                  value={roster[role]}
                  onChange={(v) => setRoster({ ...roster, [role]: Number(v || 0) })}
                  style={{ width: 56 }}
                />
              </span>
            ))}
          </div>
          {validationErrors.length > 0 && (
            <div className="lobby-roster__errors">
              {validationErrors.map(err => <div key={err} className="lobby-roster__error">{err}</div>)}
            </div>
          )}
        </div>

        {/* ─── 开始按钮 ─── */}
        <div className="lobby-start-btn">
          <Button
            type="primary"
            size="large"
            icon={<ThunderboltOutlined />}
            loading={creating}
            disabled={!selectedMode || validationErrors.length > 0}
            onClick={handleCreate}
          >
            开始对局
          </Button>
        </div>

        {/* ─── 历史对局 ─── */}
        <h3 className="lobby-history-title">历史对局</h3>
        <div className="lobby-history-table">
          <Table
            dataSource={games}
            columns={columns}
            rowKey="game_id"
            loading={loading}
            size="small"
            pagination={{ pageSize: 10, size: 'small' }}
            locale={{ emptyText: '暂无对局记录' }}
          />
        </div>
      </div>
    </div>
  )
}
