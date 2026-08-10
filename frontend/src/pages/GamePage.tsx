/**
 * 对局界面
 *
 * 职责：展示游戏进程 + 人类玩家操作面板
 * 路由：/game/:gameId
 *
 * 展示内容：
 * - 夜晚/白天阶段指示器（暗色/亮色主题）
 * - 6/12 个玩家座位卡（存活/淘汰状态、角色揭示）
 * - 事件日志流（发言、死亡、投票、淘汰等所有事件）
 * - 操作面板（人类玩家发言/投票）
 * - 游戏结束结算面板
 */

import { useEffect, useState, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Button, Input, Radio, InputNumber, Spin, message, Divider, Tag } from 'antd'
import { wsService } from '../services/ws'
import { apiService } from '../services/api'
import { mergePublicEvents } from '../services/eventStream'
import { useGameStore } from '../stores/gameStore'
import type { WSMessage, GameDetail, Roster, PlayerInfo } from '../types'
import IdentityPanel from '../components/game/IdentityPanel'
import PlayerTable from '../components/game/PlayerTable'
import CurrentSpeechPanel from '../components/game/CurrentSpeechPanel'
import EventLogPanel from '../components/game/EventLogPanel'
import './GamePage.css'

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

// ─── 辅助函数 ───

/** 日志条目 CSS 类名映射 */
function getLogEntryClass(type: string): string {
  const base = 'log-entry'
  if (type === 'game_over') return `${base} ${base}--gameover`
  if (type === 'death_announce' || type === 'eliminate') return `${base} ${base}--death`
  if (type === 'last_words') return `${base} ${base}--last-words`
  if (type === 'speech' || type === 'pk_speech') return `${base} ${base}--speech`
  if (type === 'vote') return `${base} ${base}--vote`
  if (type === 'vote_result' || type === 'pk_announce') return `${base} ${base}--result`
  if (type === 'phase_change' || type === 'night_phase' || type.includes('night')) return `${base} ${base}--night`
  return `${base} ${base}--system`
}

/** 角色中文名 */
const ROLE_CN: Record<string, string> = {
  werewolf: '狼人', villager: '村民', seer: '预言家', witch: '女巫', hunter: '猎人', guard: '守卫',
}
const roleCN = (role?: string | null) => (role ? (ROLE_CN[role] || role) : null)

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
      store.setGame(detail.game_id, detail.mode, detail.model_name)
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
        case 'game_over':
          store.setWinner(msg.data.winner)
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
      store.setGame(detail.game_id, detail.mode, detail.model_name)
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
  const aliveCount = players.filter(p => p.is_alive).length
  const recentSpeeches = speeches.slice(-5)
  const themeClass = isNight ? 'game-page game-page--night' : 'game-page game-page--day'

  // 判断玩家是否可选
  const isPlayerSelectable = (p: PlayerInfo): boolean => {
    if (!store.actionPrompt || !store.mySeat) return false
    const ap = store.actionPrompt
    if (ap.actionType === 'speech' || ap.actionType === 'last_words' || ap.actionType === 'save') return false
    if (ap.allowedTargetSeats?.length) return ap.allowedTargetSeats.includes(p.seat_number)
    if (!p.is_alive || p.seat_number === store.mySeat) return false
    if (ap.actionType === 'kill' && store.myCompanions.includes(p.seat_number)) return false
    return true
  }

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
      'victory_check': null,  // game_over 事件会展示完整信息，此处不重复
      'game_over': (() => {
        const winText = d.winner === 'werewolf' ? '狼人' : '好人'
        const lines: string[] = [`🏆 游戏结束 - ${winText}胜！`]
        if (d.reason_text) lines.push(`📋 胜利原因: ${d.reason_text}`)
        const nd = d.night_deaths
        if (Array.isArray(nd) && nd.length > 0) lines.push(`💀 最后死亡: ${nd.map((s: any) => `${s}号`).join('、')}`)
        if (d.alive_werewolves !== undefined && d.alive_goods !== undefined)
          lines.push(`🐺 存活狼人: ${d.alive_werewolves} | 好人: ${d.alive_goods}`)
        lines.push(`⏱️ 总轮数: ${d.total_rounds}`)
        if (d.all_roles) {
          lines.push('🎭 身份揭晓:')
          const seats = Object.keys(d.all_roles).sort((a: string, b: string) => Number(a) - Number(b))
          for (const seat of seats) {
            const p = d.all_roles[seat]
            const rname = ROLE_CN[p.role] || p.role
            const status = p.is_alive ? '存活' : '已淘汰'
            lines.push(`  ${seat}号(${p.name}) - ${rname} ${status === '存活' ? '✓' : '✗'}`)
          }
        }
        return lines.join('\n')
      })(),
    }

    const text = textMap[msg.type]
    if (!text) return null
    return { event_id: msg.event_id, event_order: msg.event_order, timestamp: msg.timestamp, time, type: msg.type, text, phase }
  }

  // 计算可选座位列表
  const selectableSeats = players.filter(p => isPlayerSelectable(p)).map(p => p.seat_number)
  // 当前发言座位
  const speakingSeat = speeches.length > 0 ? speeches[speeches.length - 1].seat : null
  // 操作面板主题类
  const actionThemeClass = store.actionPrompt ? `action-prompt--${store.actionPrompt.actionType}` : 'action-prompt--default'

  return (
    <div className={themeClass}>
      <div className="game-page__inner">
        {/* ─── 顶部状态栏 ─── */}
        <header className="game-header">
          <div className="game-header__left">
            <span className="game-header__phase-icon">{isNight ? '🌙' : '☀️'}</span>
            <span className="game-header__phase-text">{isNight ? '夜晚' : '白天'}</span>
            {currentPhase && <span className="game-header__round">第 {store.currentRound} 轮</span>}
          </div>
          <div className="game-header__right">
            {isGameOver ? (
              <Tag color={store.winner === 'werewolf' ? 'red' : 'green'} style={{ fontSize: 14, padding: '2px 10px' }}>
                🏆 {store.winner === 'werewolf' ? '狼人胜' : '好人胜'}
              </Tag>
            ) : started ? (
              <>
                <span className="game-header__status-dot" />
                <span className="game-header__status-text">游戏中</span>
              </>
            ) : (
              <span className="game-header__status-text">等待开始</span>
            )}
            <span className="game-header__alive-count">存活 {aliveCount} / {players.length}</span>
            <span className="game-header__mode-badge">{store.gameMode === 'mixed' ? '人机混合' : '纯AI'}</span>
          </div>
        </header>

        {/* ─── 三栏主布局 ─── */}
        <div className="game-layout">
          {/* 左侧：我的身份 */}
          <div className="game-layout__left">
            {store.myRole ? (
              <IdentityPanel
                myRole={store.myRole}
                mySeat={store.mySeat}
                myCompanions={store.myCompanions}
                seerResults={seerResults}
                witchInfo={witchInfo}
                players={players}
                aliveCount={aliveCount}
                totalPlayers={players.length}
                currentRound={store.currentRound}
                gameMode={store.gameMode}
                roster={null}
              />
            ) : (
              <div className="gp-panel">
                <div className="waiting-panel">
                  <div className="waiting-panel__icon">{started ? '🤖' : '🎭'}</div>
                  {started && store.gameMode === 'pure_ai'
                    ? (
                      <>
                        <div>纯 AI 模式，游戏自动运行</div>
                        {store.modelName && <div style={{ fontSize: 11, color: 'var(--gp-text3)', marginTop: 6 }}>模型: {store.modelName}</div>}
                      </>
                    )
                    : store.gameMode === 'mixed'
                      ? '正在同步你的私密身份信息…'
                      : '等待游戏开始'}
                </div>
              </div>
            )}
          </div>

          {/* 中间：圆桌玩家区域 */}
          <div className="game-layout__center">
            <div className="gp-panel">
              <PlayerTable
                players={players}
                mySeat={store.mySeat}
                myRole={store.myRole}
                myCompanions={store.myCompanions}
                currentPhase={currentPhase}
                currentRound={store.currentRound}
                isNight={isNight}
                selectableSeats={selectableSeats}
                onSeatClick={handleActionSelect}
                speakingSeat={speakingSeat}
                gameMode={store.gameMode}
                modelName={store.modelName}
              />
            </div>
          </div>

          {/* 右侧：当前发言 + 操作面板 */}
          <div className="game-layout__right">
            {/* 当前发言 */}
            <div className="gp-panel">
              <CurrentSpeechPanel speeches={speeches} eventLog={eventLog} />
            </div>

            {/* 操作面板 */}
            <div className="gp-panel action-panel">
              <div className="gp-panel__header">
                <span>🎮</span>
                <span>操作面板</span>
              </div>
              <div className="gp-panel__body">
                {/* 未开始：房间配置 */}
                {!started ? (
                  rosterConfig ? (
                    <div className="roster-config">
                      <div style={{ fontWeight: 700 }}>房间配置（房主）</div>
                      <div>
                        <span style={{ marginRight: 12, fontSize: 13, color: 'var(--gp-text2)' }}>对局人数：</span>
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
                      <div className="roster-config__roles">
                        {(Object.keys(rosterConfig.roster) as (keyof Roster)[]).map(role => (
                          <span key={role} className="roster-config__role-item">
                            {roleCN(role)}: <InputNumber size="small" min={0} max={role === 'werewolf' ? (rosterConfig.playerCount === 6 ? 2 : 4) : role === 'villager' ? rosterConfig.playerCount : 1} disabled={rosterConfig.rosterType === 'official' || role === 'werewolf'} value={rosterConfig.roster[role]} onChange={(v) => handleRosterChange(role, v || 0)} style={{ width: 50 }} />
                          </span>
                        ))}
                      </div>
                      {rosterConfig.errors.map(err => (
                        <div key={err} style={{ fontSize: 12, color: 'var(--gp-danger)' }}>{err}</div>
                      ))}
                      <div style={{ display: 'flex', gap: 8 }}>
                        <Button size="small" onClick={handleResetOfficial} loading={rosterSaving}>恢复官方阵容</Button>
                        <Button size="small" onClick={handleSaveRoster} loading={rosterSaving} disabled={rosterConfig.errors.length > 0}>保存阵容</Button>
                      </div>
                      <Button type="primary" block size="large" onClick={handleStart} disabled={rosterConfig.errors.length > 0}>开始对局</Button>
                    </div>
                  ) : (
                    <Button type="primary" block size="large" onClick={handleStart}>开始对局</Button>
                  )
                ) : isGameOver ? (
                  <div className="game-over-panel">
                    <Button type="primary" block size="large" onClick={() => navigate(`/replay/${gameId!}`)}>
                      📺 查看回放
                    </Button>
                    <Button block onClick={() => navigate('/')}>返回大厅</Button>
                  </div>
                ) : store.actionPrompt ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                    <div className={`action-prompt ${actionThemeClass}`}>
                      <div className="action-prompt__title">✋ 轮到你了！</div>
                      <div className="action-prompt__desc">
                        {store.actionPrompt.actionType === 'kill' && '你是狼人，请选择今晚要击杀的目标'}
                        {store.actionPrompt.actionType === 'verify' && '你是预言家，请选择要查验的玩家'}
                        {store.actionPrompt.actionType === 'save' && '你是女巫，请选择是否使用解药/毒药'}
                        {store.actionPrompt.actionType === 'guard' && '你是守卫，请选择本夜守护的玩家'}
                        {store.actionPrompt.actionType === 'hunter_shoot' && '你是猎人，请选择带走的玩家或跳过'}
                        {store.actionPrompt.actionType === 'speech' && '请输入你的发言'}
                        {store.actionPrompt.actionType === 'vote' && '请选择你要投票淘汰的玩家'}
                        {store.actionPrompt.actionType === 'last_words' && '你被淘汰了，请输入遗言'}
                      </div>
                      {store.actionPrompt.actionType === 'kill' && store.myCompanions.length > 0 && (
                        <div className="action-prompt__companions">🐺 同伴: {store.myCompanions.map(c => `${c}号`).join('、')}（不能击杀同伴）</div>
                      )}
                      {store.actionPrompt.allowedTargetSeats && store.actionPrompt.allowedTargetSeats.length > 0 && (
                        <div className="action-prompt__targets">
                          可选目标: {store.actionPrompt.allowedTargetSeats.map(s => `${s}号`).join('、')}
                          {store.actionPrompt.actionType === 'guard' && store.actionPrompt.lastTarget ? `（上夜守护 ${store.actionPrompt.lastTarget}号，不可连续守护）` : ''}
                        </div>
                      )}
                    </div>

                    {/* 女巫专属操作面板 */}
                    {store.actionPrompt.actionType === 'save' ? (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {witchInfo && witchInfo.night_kill_target !== null && (
                          <div className="witch-info witch-info--kill">
                            💀 今晚被狼人击杀的是: <strong>{witchInfo.night_kill_target}号</strong>
                          </div>
                        )}
                        {witchInfo && witchInfo.night_kill_target === null && (
                          <div className="witch-info witch-info--peace">🌙 今晚是平安夜，无人被击杀</div>
                        )}
                        {witchInfo?.save_available && (
                          <Button type="primary" danger block onClick={handleWitchSave}>💚 使用解药救人</Button>
                        )}
                        {witchInfo?.poison_available && (
                          <>
                            <div style={{ fontSize: 12, color: 'var(--gp-text2)', textAlign: 'center' }}>☠️ 选择毒药目标（或跳过）:</div>
                            <div className="action-buttons">
                              {players.filter(p => (store.actionPrompt?.allowedTargetSeats?.length ? store.actionPrompt.allowedTargetSeats.includes(p.seat_number) : (p.is_alive && p.seat_number !== store.mySeat))).map(p => (
                                <Button key={p.seat_number} size="small" danger onClick={() => handleActionSelect(p.seat_number)}>{p.seat_number}号</Button>
                              ))}
                            </div>
                          </>
                        )}
                        <Button block onClick={() => handleActionSelect(null)}>⏭️ 跳过不用药</Button>
                      </div>
                    ) : store.actionPrompt.actionType === 'speech' || store.actionPrompt.actionType === 'last_words' ? (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        <Input.TextArea
                          value={speechText}
                          onChange={(e) => setSpeechText(e.target.value)}
                          placeholder="输入你的发言..."
                          rows={3}
                          maxLength={500}
                        />
                        <Button type="primary" block onClick={handleSpeech} disabled={!speechText.trim()}>提交发言</Button>
                      </div>
                    ) : (
                      <div className="action-buttons">
                        {players.filter(p => (store.actionPrompt?.allowedTargetSeats?.length
                          ? store.actionPrompt.allowedTargetSeats.includes(p.seat_number)
                          : (p.is_alive && p.seat_number !== store.mySeat
                            && !(store.actionPrompt!.actionType === 'kill' && store.myCompanions.includes(p.seat_number))))
                        ).map(p => (
                          <Button key={p.seat_number} size="small" onClick={() => handleActionSelect(p.seat_number)}>{p.seat_number}号</Button>
                        ))}
                        {((store.actionPrompt!.actionType === 'vote' || store.actionPrompt!.actionType === 'hunter_shoot') && (store.actionPrompt.canSkip ?? true)) && (
                          <Button size="small" onClick={() => handleActionSelect(null)}>{store.actionPrompt!.actionType === 'hunter_shoot' ? '跳过不开枪' : '弃票'}</Button>
                        )}
                      </div>
                    )}
                  </div>
                ) : store.myRole ? (
                  <div className="action-identity">
                    <Tag color={store.myRole === 'werewolf' ? 'red' : 'blue'} className="action-identity__tag">
                      你的身份: {roleCN(store.myRole)} ({store.mySeat}号)
                    </Tag>
                    {store.myRole === 'werewolf' && store.myCompanions.length > 0 && (
                      <div className="action-identity__companions">🐺 你的狼人同伴: {store.myCompanions.map(c => `${c}号`).join('、')}</div>
                    )}
                    {store.myRole === 'seer' && seerResults.length > 0 && (
                      <div className="action-identity__seer-records">
                        <div className="action-identity__seer-title">🔍 查验记录:</div>
                        {seerResults.map((r, i) => (
                          <div key={i} style={{ marginTop: 2 }}>
                            <Tag color={r.result === 'werewolf' ? 'red' : 'green'} style={{ fontSize: 11 }}>
                              第{r.round}轮: {r.target}号{r.result === 'werewolf' ? '🐺 狼人' : '✅ 好人'}
                            </Tag>
                          </div>
                        ))}
                      </div>
                    )}
                    <Divider style={{ margin: '8px 0' }} />
                    <div className="action-loading">
                      {store.gameMode === 'mixed' ? '等待轮到你的操作...AI 玩家正在思考' : '纯AI模式下无需操作，游戏自动运行'}
                    </div>
                  </div>
                ) : store.gameMode === 'mixed' ? (
                  <div className="action-loading">等待身份同步…</div>
                ) : (
                  <div className="action-loading">纯AI模式，游戏自动运行</div>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* ─── 底部事件日志 ─── */}
        <EventLogPanel eventLog={eventLog} logEndRef={logEndRef} getLogEntryClass={getLogEntryClass} />
      </div>
    </div>
  )
}
