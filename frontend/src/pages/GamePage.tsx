/**
 * 对局界面
 *
 * 职责：展示游戏进程 + 人类玩家操作面板
 * 路由：/game/:gameId
 *
 * 展示内容：
 * - 夜晚/白天阶段指示器（暗色/亮色主题）
 * - 6 个玩家座位卡（存活/淘汰状态、角色揭示）
 * - 事件日志流（发言、死亡、投票、淘汰等所有事件）
 * - 操作面板（人类玩家发言/投票）
 * - 游戏结束结算面板
 */

import { useEffect, useState, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Card, Row, Col, Button, Input, Space, Tag, Typography, Spin, message, Divider, Badge, Radio, InputNumber } from 'antd'
import { wsService } from '../services/ws'
import { apiService } from '../services/api'
import { mergePublicEvents } from '../services/eventStream'
import { useGameStore } from '../stores/gameStore'
import type { WSMessage, GameDetail, Roster } from '../types'

const { Title, Text } = Typography

// 事件日志条目
interface LogEntry {
  event_id?: string
  event_order?: string
  timestamp: string
  time: string
  type: string
  text: string
  phase: string
}

export default function GamePage() {
  const { gameId } = useParams<{ gameId: string }>()
  const navigate = useNavigate()
  const [loading, setLoading] = useState(true)
  const [started, setStarted] = useState(false)
  const [speechText, setSpeechText] = useState('')
  const [eventLog, setEventLog] = useState<LogEntry[]>([])
  const logEndRef = useRef<HTMLDivElement>(null)
  const [witchInfo, setWitchInfo] = useState<{ night_kill_target: number | null; save_available: boolean; poison_available: boolean } | null>(null)
  const [seerResults, setSeerResults] = useState<{ target: number; result: string; round: number }[]>([])

  // ─── 等待态阵容编辑状态 ───
  const [rosterConfig, setRosterConfig] = useState<{ playerCount: 6 | 12; rosterType: 'official' | 'custom'; roster: Roster; errors: string[] } | null>(null)
  const [rosterSaving, setRosterSaving] = useState(false)
  // 暴露 applyMessage 供 handleStart 使用
  const applyMessageRef = useRef<(msg: WSMessage) => void>(() => {})

  const store = useGameStore()

  useEffect(() => {
    if (!gameId) return

    // 加载对局详情
    const applyMessage = (msg: WSMessage) => {
      store.addMessage(msg)
      const logEntry = wsMessageToLog(msg)
      if (logEntry) setEventLog(prev => mergePublicEvents(prev, [logEntry]))
      if (msg.type === 'phase_change') store.setPhase(msg.data.round, msg.data.phase)
      if (msg.type === 'night_phase') store.setPhase(msg.data.round, 'night')
      if (['speech', 'pk_speech', 'last_words', 'eliminate'].includes(msg.type)) {
        store.addSpeech({ seat: msg.data.seat, content: msg.data.content || msg.data.description || '', isPk: msg.type === 'pk_speech' })
      }
      if (msg.type === 'vote_result') store.setVotes(msg.data.tally || {})
      if (msg.type === 'eliminate' && typeof msg.data.seat === 'number') {
        store.markPlayerDead(msg.data.seat)
      }
    }
    applyMessageRef.current = applyMessage

    apiService.getGame(gameId).then((detail: GameDetail) => {
      store.setGame(detail.game_id, detail.mode)
      store.setPlayers(detail.players)
      // 初始化阵容编辑状态
      const ownerToken = sessionStorage.getItem(`game-owner-token:${gameId}`)
      if (ownerToken && detail.status === 'waiting' && !detail.roster_locked) {
        setRosterConfig({
          playerCount: (detail.player_count || 6) as 6 | 12,
          rosterType: detail.roster_type || 'official',
          roster: detail.roster || { werewolf: 2, villager: 2, seer: 1, witch: 1, hunter: 0, guard: 0 },
          errors: [],
        })
      }
      if (detail.status === 'waiting') {
        setStarted(false)
      } else if (detail.status === 'playing') {
        setStarted(true)
      } else if (detail.status === 'finished') {
        navigate(`/replay/${gameId}`)
      }
      return apiService.getPublicEvents(gameId)
    }).then((snapshot) => {
      snapshot?.events.forEach(applyMessage)
    }).catch(() => {
      message.error('加载对局失败')
    }).finally(() => setLoading(false))

    // 连接 WebSocket
    wsService.connect(gameId, sessionStorage.getItem(`game-player-token:${gameId}`) || undefined)

    // WebSocket 消息处理（定义在 useEffect 内部，避免闭包陷阱）
    const handleWSMessage = (msg: WSMessage) => {
      applyMessage(msg)

      // 更新游戏状态
      switch (msg.type) {
        case 'game_started':
        case 'identity_sync':
          // 游戏开始：通知人类玩家身份 + 狼人同伴
          store.setMyRole(msg.data.seat, msg.data.role, msg.data.werewolf_companions)
          break
        case 'victory_check':
          if (msg.data.game_over) {
            store.setWinner(msg.data.winner)
          }
          break
        case 'human_action_prompt':
          // 轮到人类操作：显示操作提示
          store.setActionPrompt({
            actionType: msg.data.action_type,
            seat: msg.data.seat,
            role: msg.data.role,
            allowedTargetSeats: msg.data.allowed_target_seats || [],
            lastTarget: msg.data.last_target ?? null,
            canSkip: !!msg.data.can_skip,
          })
          break
        case 'night_verify':
          // 夜晚预言家查验事件：不再广播给所有人，改为私有通知
          // 这个事件现在只作为日志显示（主持人风格）
          break
        case 'private_seer_result':
          // 私有通知：只有预言家本人收到
          if (msg.data.target !== undefined) {
            const isWolf = msg.data.result === 'werewolf'
            setSeerResults(prev => [...prev, { target: msg.data.target, result: msg.data.result, round: msg.data.round }])
            message.info({
              content: `🔍 你查验了 ${msg.data.target}号，结果是：${isWolf ? '🐺 狼人！' : '✅ 好人'}`,
              duration: 8,
            })
          }
          break
        case 'private_witch_info':
          // 私有通知：只有女巫本人收到，告知谁被杀了
          setWitchInfo({
            night_kill_target: msg.data.night_kill_target,
            save_available: msg.data.save_available,
            poison_available: msg.data.poison_available,
          })
          break
        default:
          break
      }
    }

    const unsubscribe = wsService.onMessage(handleWSMessage)

    return () => {
      unsubscribe()
      wsService.disconnect()
      store.reset()
    }
  }, [gameId])

  // 自动滚动到最新日志
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [eventLog])

  const handleStart = async () => {
    if (!gameId) return
    try {
      await apiService.startGame(gameId, sessionStorage.getItem(`game-owner-token:${gameId}`) || undefined)
      setStarted(true)
      message.success('游戏开始！')
      // 重新获取游戏详情和公共事件，同步后端已产生的状态
      const detail = await apiService.getGame(gameId)
      store.setGame(detail.game_id, detail.mode)
      store.setPlayers(detail.players)
      if (detail.status === 'playing') {
        setStarted(true)
      }
      const snapshot = await apiService.getPublicEvents(gameId)
      snapshot?.events.forEach(msg => applyMessageRef.current(msg))
    } catch {
      message.error('开始失败')
    }
  }

  // ─── 房主阵容编辑处理 ───
  const officialRosterFor = (count: 6 | 12): Roster => ({
    werewolf: count === 6 ? 2 : 4,
    villager: count === 6 ? 2 : 4,
    seer: 1, witch: 1,
    hunter: count === 12 ? 1 : 0,
    guard: count === 12 ? 1 : 0,
  })

  const computeRosterErrors = (roster: Roster, playerCount: number): string[] => {
    const errors: string[] = []
    const total = Object.values(roster).reduce((sum, v) => sum + v, 0)
    if (total !== playerCount) errors.push(`当前 ${total} / ${playerCount} 人`)
    const expectedWolves = playerCount === 6 ? 2 : 4
    if (roster.werewolf !== expectedWolves) errors.push(`${playerCount} 人局狼人必须为 ${expectedWolves} 名`)
    for (const role of ['seer', 'witch', 'hunter', 'guard'] as const) {
      if (roster[role] > 1) errors.push(`${role} 最多 1 名`)
    }
    return errors
  }

  const handleRosterChange = (role: keyof Roster, value: number) => {
    if (!rosterConfig) return
    const newRoster = { ...rosterConfig.roster, [role]: Number(value || 0) }
    setRosterConfig({ ...rosterConfig, roster: newRoster, rosterType: 'custom', errors: computeRosterErrors(newRoster, rosterConfig.playerCount) })
  }

  const handlePlayerCountChange = (count: 6 | 12) => {
    if (!rosterConfig) return
    const official = officialRosterFor(count)
    setRosterConfig({ playerCount: count, rosterType: 'official', roster: official, errors: [] })
  }

  const handleSaveRoster = async () => {
    if (!gameId || !rosterConfig) return
    const ownerToken = sessionStorage.getItem(`game-owner-token:${gameId}`)
    if (!ownerToken) return
    try {
      setRosterSaving(true)
      await apiService.updateRoster(gameId, ownerToken, rosterConfig.playerCount, rosterConfig.rosterType, rosterConfig.roster)
      message.success('阵容已更新')
    } catch {
      message.error('更新阵容失败')
    } finally {
      setRosterSaving(false)
    }
  }

  const handleResetOfficial = async () => {
    if (!gameId || !rosterConfig) return
    const ownerToken = sessionStorage.getItem(`game-owner-token:${gameId}`)
    if (!ownerToken) return
    try {
      setRosterSaving(true)
      const result = await apiService.resetOfficialRoster(gameId, ownerToken)
      setRosterConfig({
        playerCount: result.player_count as 6 | 12,
        rosterType: 'official',
        roster: result.roster,
        errors: [],
      })
      message.success('已恢复官方阵容')
    } catch {
      message.error('恢复失败')
    } finally {
      setRosterSaving(false)
    }
  }

  const handleSpeech = async () => {
    if (!gameId || !speechText.trim() || store.mySeat === null || !store.actionPrompt) return
    try {
      const actionType = store.actionPrompt.actionType === 'last_words' ? 'last_words' : 'speech'
      await apiService.submitSpeech(gameId, speechText, false, sessionStorage.getItem(`game-player-token:${gameId}`) || '', actionType)
      setSpeechText('')
      store.clearActionPrompt()
    } catch {
      message.error('发言失败')
    }
  }

  // 处理人类玩家选择座位（夜晚行动/投票）
  const handleActionSelect = async (seat: number | null) => {
    if (!gameId || !store.actionPrompt || store.mySeat === null) return
    const actionType = store.actionPrompt.actionType
    try {
      if (actionType === 'vote') {
        await apiService.submitVote(gameId, seat, false, sessionStorage.getItem(`game-player-token:${gameId}`) || '')
      } else if (actionType === 'kill' || actionType === 'verify' || actionType === 'poison' || actionType === 'guard' || actionType === 'hunter_shoot') {
        await apiService.submitNightAction(gameId, actionType, seat, sessionStorage.getItem(`game-player-token:${gameId}`) || '')
      } else if (actionType === 'save') {
        if (seat === null) {
          await apiService.submitNightAction(gameId, 'skip', null, sessionStorage.getItem(`game-player-token:${gameId}`) || '')
        } else {
          await apiService.submitNightAction(gameId, 'poison', seat, sessionStorage.getItem(`game-player-token:${gameId}`) || '')
        }
      }
      store.clearActionPrompt()
      setWitchInfo(null)
      message.success('操作已提交')
    } catch {
      message.error('操作失败')
    }
  }

  // 女巫使用解药（自动救被杀的人）
  const handleWitchSave = async () => {
    if (!gameId || !store.actionPrompt || store.mySeat === null) return
    try {
      await apiService.submitNightAction(gameId, 'save', null, sessionStorage.getItem(`game-player-token:${gameId}`) || '')
      store.clearActionPrompt()
      setWitchInfo(null)
      message.success('你使用了解药，被击杀的玩家已被救活！')
    } catch {
      message.error('操作失败')
    }
  }

  if (loading) {
    return <div style={{ textAlign: 'center', padding: 100 }}><Spin size="large" /></div>
  }

  const players = store.players
  const speeches = store.speeches
  const currentPhase = store.currentPhase
  const isNight = currentPhase === 'night'
  const isGameOver = !!store.winner

  // 事件日志转文本
  function wsMessageToLog(msg: WSMessage): LogEntry | null {
    const d = msg.data || {}
    const phase = d.phase || (msg.type.includes('night') ? 'night' : msg.type.includes('day') || msg.type.includes('speech') || msg.type.includes('vote') ? 'day' : 'system')
    const time = new Date(msg.timestamp || Date.now()).toLocaleTimeString('zh-CN')

    const textMap: Record<string, string | null> = {
      'role_assign': '🎲 角色分配完成',
      'phase_change': d.phase === 'night' ? `🌙 第${d.round}轮夜晚开始` : `☀️ 第${d.round}轮白天开始`,
      'night_phase': '🌙 夜晚正在进行…',
      'night_kill': '🌙 狼人正在行动...',
      'night_verify': '🔍 预言家正在查验...',
      'night_save': '💊 女巫正在思考是否用药...',
      'night_poison': '☠️ 女巫正在思考是否用药...',
      'night_settle': null,  // 不再公开显示夜晚结算细节
      'death_announce': Array.isArray(d.deaths) && d.deaths.length > 0
        ? `📢 昨晚死亡: ${d.deaths.map((x: any) => (typeof x === 'object' ? x.seat : x) + '号').join('、')}`
        : (d.peace_night ? '📢 昨晚是平安夜' : null),
      'last_words': `🕊️ ${d.seat}号遗言: ${d.content || ''}`,
      'speech': `${d.seat}号发言: ${d.content || ''}`,
      'pk_speech': `[PK] ${d.seat}号发言: ${d.content || ''}`,
      'vote': `${d.seat}号投票 → ${d.target ? d.target + '号' : '弃票'}`,
      'vote_result': `📊 投票结果: ${Object.entries(d.tally || {}).map(([k,v]: any) => `${k}号${v}票`).join('，') || '全部弃票'}`,
      'pk_announce': `⚖️ 平票！PK: ${d.tied_seats?.join('、')}号`,
      'eliminate': `❌ ${d.seat}号被淘汰`,
      'victory_check': d.game_over ? `🏆 游戏结束！${d.winner === 'werewolf' ? '狼人' : '好人'}阵营胜利！` : null,
      'game_over': `🏆 游戏结束 - ${d.winner === 'werewolf' ? '狼人' : '好人'}胜！`,
    }

    const text = textMap[msg.type]
    if (!text) return null
    return { event_id: msg.event_id, event_order: msg.event_order, timestamp: msg.timestamp, time, type: msg.type, text, phase }
  }

  // 角色中文
  const roleCN = (role?: string | null) => {
    const map: Record<string, string> = { werewolf: '狼人', villager: '村民', seer: '预言家', witch: '女巫', hunter: '猎人', guard: '守卫' }
    return role ? (map[role] || role) : null
  }

  return (
    <div style={{
      maxWidth: 1000,
      margin: '0 auto',
      padding: '20px',
      background: isNight ? '#0a0a2e' : '#f0f2f5',
      minHeight: '100vh',
      transition: 'background 0.5s',
    }}>
      {/* 标题栏 */}
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Title level={3} style={{ color: isNight ? '#fff' : '#000', margin: 0 }}>
          {isNight ? '🌙 夜晚' : '☀️ 白天'}
          {currentPhase && ` - 第${store.currentRound}轮`}
        </Title>
        <Space>
          {isGameOver && (
            <Tag color="red" style={{ fontSize: 16, padding: '4px 12px' }}>
              🏆 {store.winner === 'werewolf' ? '狼人胜' : '好人胜'}
            </Tag>
          )}
          {started && !isGameOver && <Badge status="processing" text={<span style={{color: isNight ? '#fff' : '#000'}}>游戏中</span>} />}
        </Space>
      </div>

      <Row gutter={16}>
        {/* 左侧：玩家座位 + 事件日志 */}
        <Col span={16}>
          {/* 玩家座位区 */}
          <Card title={<span style={{color: isNight ? '#fff' : '#000'}}>🪑 玩家座位</span>}
                style={{ marginBottom: 16, background: isNight ? 'rgba(255,255,255,0.1)' : '#fff', border: 'none' }}
                headStyle={{ background: 'transparent', borderBottom: '1px solid rgba(255,255,255,0.2)' }}
          >
            <Row gutter={[8, 8]}>
              {players.map((p) => (
                <Col key={p.seat_number} xs={12} md={players.length > 6 ? 6 : 8}>
                  <Card size="small" style={{
                    opacity: p.is_alive ? 1 : 0.35,
                    borderColor: p.player_type === 'human' ? '#52c41a' : (isNight ? '#444' : '#d9d9d9'),
                    background: isNight ? 'rgba(255,255,255,0.08)' : '#fff',
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <Text strong style={{color: isNight ? '#fff' : '#000'}}>{p.seat_number}号</Text>
                      {p.is_alive
                        ? <Tag color="green" style={{fontSize: 10}}>存活</Tag>
                        : <Tag color="red" style={{fontSize: 10}}>💀淘汰</Tag>
                      }
                    </div>
                    <Text type="secondary" style={{ fontSize: 12, color: isNight ? '#ccc' : '#999' }}>{p.player_name}</Text>
                    <br />
                    <Space size={4}>
                      {p.player_type === 'human' && <Tag color="blue" style={{fontSize: 10}}>人类</Tag>}
                      {p.role && <Tag color={p.role === 'werewolf' ? 'red' : 'blue'} style={{fontSize: 10}}>{roleCN(p.role)}</Tag>}
                    </Space>
                  </Card>
                </Col>
              ))}
            </Row>
          </Card>

          {/* 事件日志流 */}
          <Card title={<span style={{color: isNight ? '#fff' : '#000'}}>📜 事件日志</span>}
                style={{ background: isNight ? 'rgba(255,255,255,0.1)' : '#fff', border: 'none' }}
                headStyle={{ background: 'transparent', borderBottom: '1px solid rgba(255,255,255,0.2)' }}
          >
            <div style={{ maxHeight: 350, overflowY: 'auto' }}>
              {eventLog.length === 0 ? (
                <Text type="secondary" style={{color: isNight ? '#888' : '#999'}}>等待游戏开始...</Text>
              ) : (
                  eventLog.map((entry, i) => (
                  <div key={entry.event_id || i} style={{
                    marginBottom: 6,
                    padding: '6px 10px',
                    borderRadius: 6,
                    background: entry.phase === 'night' ? 'rgba(50,50,150,0.3)' : entry.phase === 'day' ? 'rgba(200,180,50,0.15)' : 'rgba(128,128,128,0.15)',
                    color: isNight ? '#eee' : '#333',
                    fontSize: 13,
                  }}>
                    <Text type="secondary" style={{ fontSize: 11, color: isNight ? '#888' : '#999', marginRight: 8 }}>{entry.time}</Text>
                    {entry.text}
                  </div>
                ))
              )}
              <div ref={logEndRef} />
            </div>
          </Card>
        </Col>

        {/* 右侧：发言区 + 操作面板 */}
        <Col span={8}>
          {/* 发言记录 */}
          <Card title={<span style={{color: isNight ? '#fff' : '#000'}}>💬 发言记录</span>}
                style={{ marginBottom: 16, background: isNight ? 'rgba(255,255,255,0.1)' : '#fff', border: 'none' }}
                headStyle={{ background: 'transparent', borderBottom: '1px solid rgba(255,255,255,0.2)' }}
          >
            <div style={{ maxHeight: 200, overflowY: 'auto' }}>
              {speeches.length === 0 ? (
                <Text type="secondary" style={{color: isNight ? '#888' : '#999'}}>暂无发言</Text>
              ) : (
                speeches.map((s, i) => (
                  <div key={i} style={{
                    marginBottom: 8,
                    padding: '6px 10px',
                    borderRadius: 6,
                    background: s.isPk ? 'rgba(200,100,50,0.2)' : (isNight ? 'rgba(255,255,255,0.08)' : '#f5f5f5'),
                    color: isNight ? '#eee' : '#333',
                  }}>
                    <Text strong style={{color: isNight ? '#fff' : '#000'}}>{s.seat}号</Text>
                    {s.isPk && <Tag color="orange" style={{fontSize: 10, marginLeft: 4}}>PK</Tag>}
                    <br />
                    <span style={{ fontSize: 13 }}>{s.content}</span>
                  </div>
                ))
              )}
            </div>
          </Card>

          {/* 操作面板 */}
          <Card title={<span style={{color: isNight ? '#fff' : '#000'}}>🎮 操作面板</span>}
                style={{ background: isNight ? 'rgba(255,255,255,0.1)' : '#fff', border: 'none' }}
                headStyle={{ background: 'transparent', borderBottom: '1px solid rgba(255,255,255,0.2)' }}
          >
            {!started ? (
              rosterConfig ? (
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Text strong style={{ color: isNight ? '#fff' : '#000' }}>房间配置（房主）</Text>
                  <div>
                    <Text style={{ marginRight: 12, fontSize: 13, color: isNight ? '#ccc' : '#666' }}>对局人数：</Text>
                    <Radio.Group size="small" value={rosterConfig.playerCount} onChange={(e) => handlePlayerCountChange(e.target.value)}>
                      <Radio.Button value={6}>6人</Radio.Button>
                      <Radio.Button value={12}>12人</Radio.Button>
                    </Radio.Group>
                  </div>
                  <div>
                    <Radio.Group size="small" value={rosterConfig.rosterType} onChange={(e) => {
                      if (e.target.value === 'official') {
                        setRosterConfig({ ...rosterConfig, rosterType: 'official', roster: officialRosterFor(rosterConfig.playerCount), errors: [] })
                      } else {
                        setRosterConfig({ ...rosterConfig, rosterType: 'custom' })
                      }
                    }}>
                      <Radio value="official">官方默认</Radio>
                      <Radio value="custom">自定义</Radio>
                    </Radio.Group>
                  </div>
                  <Space wrap size="small">
                    {(Object.keys(rosterConfig.roster) as (keyof Roster)[]).map(role => (
                      <span key={role} style={{ fontSize: 12 }}>
                        {roleCN(role)}: <InputNumber size="small" min={0} max={role === 'werewolf' ? (rosterConfig.playerCount === 6 ? 2 : 4) : role === 'villager' ? rosterConfig.playerCount : 1} disabled={rosterConfig.rosterType === 'official' || role === 'werewolf'} value={rosterConfig.roster[role]} onChange={(v) => handleRosterChange(role, v || 0)} style={{ width: 50 }} />
                      </span>
                    ))}
                  </Space>
                  {rosterConfig.errors.map(err => (
                    <Text key={err} type="danger" style={{ fontSize: 12 }}>{err}</Text>
                  ))}
                  <Space>
                    <Button size="small" onClick={handleResetOfficial} loading={rosterSaving}>恢复官方阵容</Button>
                    <Button size="small" onClick={handleSaveRoster} loading={rosterSaving} disabled={rosterConfig.errors.length > 0}>保存阵容</Button>
                  </Space>
                  <Button type="primary" block size="large" onClick={handleStart} disabled={rosterConfig.errors.length > 0}>开始对局</Button>
                </Space>
              ) : (
                <Button type="primary" block size="large" onClick={handleStart}>开始对局</Button>
              )
            ) : isGameOver ? (
              <Space direction="vertical" style={{ width: '100%' }}>
                <Button type="primary" block size="large" onClick={() => navigate(`/replay/${gameId!}`)}>
                  📺 查看回放
                </Button>
                <Button block onClick={() => navigate('/')}>返回大厅</Button>
              </Space>
            ) : store.actionPrompt ? (
              /* 显示操作提示：轮到你了 */
              <Space direction="vertical" style={{ width: '100%' }}>
                <div style={{ padding: '8px 12px', background: 'rgba(82,196,26,0.15)', borderRadius: 8, textAlign: 'center' }}>
                  <Text strong style={{ color: '#52c41a' }}>
                    ✋ 轮到你了！
                  </Text>
                  <br />
                  <Text style={{ color: isNight ? '#eee' : '#333', fontSize: 13 }}>
                    {store.actionPrompt.actionType === 'kill' && '你是狼人，请选择今晚要击杀的目标'}
                    {store.actionPrompt.actionType === 'verify' && '你是预言家，请选择要查验的玩家'}
                     {store.actionPrompt.actionType === 'save' && '你是女巫，请选择是否使用解药/毒药'}
                     {store.actionPrompt.actionType === 'guard' && '你是守卫，请选择本夜守护的玩家'}
                     {store.actionPrompt.actionType === 'hunter_shoot' && '你是猎人，请选择带走的玩家或跳过'}
                    {store.actionPrompt.actionType === 'speech' && '请输入你的发言'}
                    {store.actionPrompt.actionType === 'vote' && '请选择你要投票淘汰的玩家'}
                    {store.actionPrompt.actionType === 'last_words' && '你被淘汰了，请输入遗言'}
                  </Text>
                  {store.actionPrompt.actionType === 'kill' && store.myCompanions.length > 0 && (
                    <div style={{ marginTop: 4, fontSize: 12, color: '#ff4d4f' }}>
                      🐺 同伴: {store.myCompanions.map(c => `${c}号`).join('、')}（不能击杀同伴）
                    </div>
                  )}
                  {store.actionPrompt.allowedTargetSeats && store.actionPrompt.allowedTargetSeats.length > 0 && (
                    <div style={{ marginTop: 4, fontSize: 12, color: isNight ? '#bbb' : '#666' }}>
                      可选目标: {store.actionPrompt.allowedTargetSeats.map(s => `${s}号`).join('、')}
                      {store.actionPrompt.actionType === 'guard' && store.actionPrompt.lastTarget ? `（上夜守护 ${store.actionPrompt.lastTarget}号，不可连续守护）` : ''}
                    </div>
                  )}
                </div>
                {/* 女巫专属操作面板 */}
                {store.actionPrompt.actionType === 'save' ? (
                  <Space direction="vertical" style={{ width: '100%' }}>
                    {/* 显示今晚被杀的人 */}
                    {witchInfo && witchInfo.night_kill_target !== null && (
                      <div style={{ padding: '8px 12px', background: 'rgba(255,77,79,0.15)', borderRadius: 8, textAlign: 'center' }}>
                        <Text style={{ color: '#ff4d4f', fontSize: 14 }}>
                          💀 今晚被狼人击杀的是: <Text strong style={{ color: '#ff4d4f', fontSize: 16 }}>{witchInfo.night_kill_target}号</Text>
                        </Text>
                      </div>
                    )}
                    {witchInfo && witchInfo.night_kill_target === null && (
                      <div style={{ padding: '8px 12px', background: 'rgba(82,196,26,0.1)', borderRadius: 8, textAlign: 'center' }}>
                        <Text style={{ color: '#52c41a' }}>🌙 今晚是平安夜，无人被击杀</Text>
                      </div>
                    )}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                      {witchInfo?.save_available && (
                        <Button type="primary" danger block onClick={handleWitchSave}>
                          💚 使用解药救人
                        </Button>
                      )}
                      {witchInfo?.poison_available && (
                        <>
                          <Text style={{ fontSize: 12, color: isNight ? '#ccc' : '#666', textAlign: 'center' }}>
                            ☠️ 选择毒药目标（或跳过）:
                          </Text>
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'center' }}>
                            {players.filter(p => (store.actionPrompt?.allowedTargetSeats?.length ? store.actionPrompt.allowedTargetSeats.includes(p.seat_number) : (p.is_alive && p.seat_number !== store.mySeat))).map(p => (
                              <Button key={p.seat_number} size="small" danger
                                onClick={() => handleActionSelect(p.seat_number)}>
                                {p.seat_number}号
                              </Button>
                            ))}
                          </div>
                        </>
                      )}
                      <Button block onClick={() => handleActionSelect(null)}>
                        ⏭️ 跳过不用药
                      </Button>
                    </div>
                  </Space>
                ) : store.actionPrompt.actionType === 'speech' || store.actionPrompt.actionType === 'last_words' ? (
                  <Space direction="vertical" style={{ width: '100%' }}>
                    <Input.TextArea
                      value={speechText}
                      onChange={(e) => setSpeechText(e.target.value)}
                      placeholder="输入你的发言..."
                      rows={3}
                      maxLength={500}
                      style={{ background: isNight ? 'rgba(255,255,255,0.1)' : '#fff', color: isNight ? '#fff' : '#000' }}
                    />
                    <Button type="primary" block onClick={handleSpeech} disabled={!speechText.trim()}>
                      提交发言
                    </Button>
                  </Space>
                ) : (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'center' }}>
                    {players.filter(p => (store.actionPrompt?.allowedTargetSeats?.length
                      ? store.actionPrompt.allowedTargetSeats.includes(p.seat_number)
                      : (p.is_alive && p.seat_number !== store.mySeat
                        && !(store.actionPrompt!.actionType === 'kill' && store.myCompanions.includes(p.seat_number))))
                    ).map(p => (
                      <Button
                        key={p.seat_number}
                        size="small"
                        onClick={() => handleActionSelect(p.seat_number)}
                      >
                        {p.seat_number}号
                      </Button>
                    ))}
                    {((store.actionPrompt!.actionType === 'vote' || store.actionPrompt!.actionType === 'hunter_shoot') && (store.actionPrompt.canSkip ?? true)) && (
                      <Button size="small" onClick={() => handleActionSelect(null)}>{store.actionPrompt!.actionType === 'hunter_shoot' ? '跳过不开枪' : '弃票'}</Button>
                    )}
                  </div>
                )}
              </Space>
            ) : store.myRole ? (
              /* 游戏进行中，但不是你的回合 */
              <Space direction="vertical" style={{ width: '100%' }}>
                <div style={{ textAlign: 'center', padding: 8 }}>
                  <Tag color={store.myRole === 'werewolf' ? 'red' : 'blue'} style={{ fontSize: 14, padding: '4px 12px' }}>
                    你的身份: {roleCN(store.myRole)} ({store.mySeat}号)
                  </Tag>
                  {store.myRole === 'werewolf' && store.myCompanions.length > 0 && (
                    <div style={{ marginTop: 4 }}>
                      <Text style={{ color: '#ff4d4f', fontSize: 13 }}>
                        🐺 你的狼人同伴: {store.myCompanions.map(c => `${c}号`).join('、')}
                      </Text>
                    </div>
                  )}
                  {store.myRole === 'seer' && seerResults.length > 0 && (
                    <div style={{ marginTop: 8 }}>
                      <Text style={{ fontSize: 12, color: isNight ? '#aaa' : '#666' }}>🔍 查验记录:</Text>
                      {seerResults.map((r, i) => (
                        <div key={i} style={{ fontSize: 12, marginTop: 2 }}>
                          <Tag color={r.result === 'werewolf' ? 'red' : 'green'} style={{ fontSize: 11 }}>
                            第{r.round}轮: {r.target}号{r.result === 'werewolf' ? '🐺 狼人' : '✅ 好人'}
                          </Tag>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <Divider style={{ margin: '4px 0' }} />
                <Text type="secondary" style={{ fontSize: 12, color: isNight ? '#aaa' : '#999' }}>
                  {store.gameMode === 'mixed'
                    ? '等待轮到你的操作...AI 玩家正在思考'
                    : '纯AI模式下无需操作，游戏自动运行'}
                </Text>
              </Space>
            ) : store.gameMode === 'mixed' ? (
              <Text type="secondary" style={{ color: isNight ? '#aaa' : '#999' }}>
                正在同步你的私密身份信息…
              </Text>
            ) : (
              <Text type="secondary" style={{ color: isNight ? '#aaa' : '#999' }}>
                纯AI模式，游戏自动运行
              </Text>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  )
}
