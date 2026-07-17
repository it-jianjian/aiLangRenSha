/**
 * 游戏大厅页面
 *
 * 职责：模式选择 + 创建对局 + 历史对局列表
 * 路由：/
 */

import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, Button, Table, Space, message, Tag, Typography, Radio, InputNumber } from 'antd'
import { RobotOutlined, UserOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { apiService } from '../services/api'
import type { GameSummary, Roster } from '../types'

const { Title, Text } = Typography

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

  // 加载历史对局列表
  useEffect(() => {
    loadGames()
  }, [])

  const loadGames = async () => {
    try {
      setLoading(true)
      const { items } = await apiService.listGames()
      setGames(items)
    } catch (e) {
      // 忽略错误，空列表即可
    } finally {
      setLoading(false)
    }
  }

  const handleCreate = async () => {
    if (!selectedMode) {
      message.warning('请先选择游戏模式')
      return
    }

    if (selectedMode === 'mixed' && !playerName) {
      message.warning('混合模式需要输入玩家名称')
      return
    }

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
    } catch (e) {
      message.error('创建对局失败')
    } finally {
      setCreating(false)
    }
  }

  const [playerName, setPlayerName] = useState('')

  const columns = [
    { title: '对局ID', dataIndex: 'game_id', key: 'game_id', ellipsis: true },
    { title: '模式', dataIndex: 'mode', key: 'mode', render: (v: string) => (
      v === 'pure_ai' ? <Tag color="blue">纯AI</Tag> : <Tag color="green">混合</Tag>
    )},
    { title: '状态', dataIndex: 'status', key: 'status', render: (v: string) => {
      const colors: Record<string, string> = { waiting: 'default', playing: 'processing', finished: 'success' }
      return <Tag color={colors[v] || 'default'}>{v}</Tag>
    }},
    { title: '胜方', dataIndex: 'winner', key: 'winner', render: (v: string | null) => v || '-'},
    { title: '轮数', dataIndex: 'total_rounds', key: 'total_rounds' },
    { title: '操作', key: 'action', render: (_: any, record: GameSummary) => (
      record.status === 'finished' ? (
        <Button size="small" onClick={() => navigate(`/replay/${record.game_id}`)}>回放</Button>
      ) : record.status === 'playing' ? (
        <Button size="small" type="primary" onClick={() => navigate(`/game/${record.game_id}`)}>进入</Button>
      ) : (
        <Button size="small" type="primary" onClick={() => navigate(`/game/${record.game_id}`)}>开始</Button>
      )
    )},
  ]

  return (
    <div style={{ maxWidth: 800, margin: '0 auto', padding: '40px 20px' }}>
      <Title level={2} style={{ textAlign: 'center' }}>AI 狼人杀</Title>
      <Text type="secondary" style={{ display: 'block', textAlign: 'center', marginBottom: 32 }}>
        基于 LangChain + LangGraph 的多 Agent 狼人杀对战平台
      </Text>

      <Space style={{ display: 'flex', justifyContent: 'center', marginBottom: 32 }} size="large">
        <Card
          hoverable
          style={{ width: 280, borderColor: selectedMode === 'pure_ai' ? '#1677ff' : undefined }}
          onClick={() => setSelectedMode('pure_ai')}
        >
          <RobotOutlined style={{ fontSize: 32, color: '#1677ff' }} />
          <Title level={4} style={{ marginTop: 12 }}>纯 AI 对战</Title>
          <Text type="secondary">{playerCount} 个 AI Agent 自动完成一局，可旁观观察</Text>
        </Card>

        <Card
          hoverable
          style={{ width: 280, borderColor: selectedMode === 'mixed' ? '#1677ff' : undefined }}
          onClick={() => setSelectedMode('mixed')}
        >
          <UserOutlined style={{ fontSize: 32, color: '#52c41a' }} />
          <Title level={4} style={{ marginTop: 12 }}>人类 + AI 混合</Title>
          <Text type="secondary">1 名人类玩家 + {playerCount - 1} 个 AI，亲身体验</Text>
        </Card>
      </Space>

      {selectedMode === 'mixed' && (
        <div style={{ textAlign: 'center', marginBottom: 16 }}>
          <input
            placeholder="输入你的昵称"
            value={playerName}
            onChange={(e) => setPlayerName(e.target.value)}
            style={{ padding: '8px 16px', borderRadius: 6, border: '1px solid #d9d9d9', width: 240 }}
          />
        </div>
      )}

      <div style={{ textAlign: 'center', marginBottom: 16 }}>
        <Text style={{ marginRight: 12 }}>对局人数：</Text>
        <Radio.Group value={playerCount} onChange={(event) => updateCount(event.target.value)}>
          <Radio.Button value={6}>6 人（经典）</Radio.Button>
          <Radio.Button value={12}>12 人（含猎人、守卫）</Radio.Button>
        </Radio.Group>
      </div>
      <Card size="small" title="阵容配置" style={{ marginBottom: 16 }}>
        <Radio.Group value={rosterType} onChange={(event) => { setRosterType(event.target.value); if (event.target.value === 'official') setRoster(officialRoster(playerCount)) }}>
          <Radio value="official">官方默认</Radio><Radio value="custom">自定义阵容</Radio>
        </Radio.Group>
        <Space wrap style={{ marginTop: 12 }}>
          {(Object.keys(roster) as (keyof Roster)[]).map(role => <span key={role}>{role}: <InputNumber min={0} max={role === 'werewolf' ? (playerCount === 6 ? 2 : 4) : role === 'villager' ? playerCount : 1} disabled={rosterType === 'official' || role === 'werewolf'} value={roster[role]} onChange={(value) => setRoster({ ...roster, [role]: Number(value || 0) })} /></span>)}
        </Space>
        {validationErrors.map(error => <Text key={error} type="danger" style={{ display: 'block', marginTop: 6 }}>{error}</Text>)}
      </Card>

      <div style={{ textAlign: 'center', marginBottom: 32 }}>
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

      <Title level={4}>历史对局</Title>
      <Table
        dataSource={games}
        columns={columns}
        rowKey="game_id"
        loading={loading}
        size="small"
        pagination={{ pageSize: 10 }}
        locale={{ emptyText: '暂无对局记录' }}
      />
    </div>
  )
}
