/**
 * 游戏大厅页面
 *
 * 视觉参考 werewolf_ui.html「月夜旅人」：topbar 金环头像 + 衬线 Hero + 竖排模式卡 +
 * banner + 规则面板 + 对局大厅（多局管理：进行中优先、状态徽标、快速进入/回放、实时刷新）。
 * 路由：/
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Empty, Input, InputNumber, Modal, Select, Spin, Tooltip, message } from 'antd'
import { ReloadOutlined, HistoryOutlined, PlayCircleOutlined } from '@ant-design/icons'
import { apiService } from '../services/api'
import { useAuthStore } from '../stores/authStore'
import type { GameSummary, Roster } from '../types'
import './LobbyPage.css'

const roleCN: Record<string, string> = {
  werewolf: '狼人', villager: '村民', seer: '预言家', witch: '女巫', hunter: '猎人', guard: '守卫',
}

const statusMeta: Record<string, { label: string; tone: 'gold' | 'blue' | 'muted' }> = {
  waiting: { label: '等待开始', tone: 'gold' },
  playing: { label: '进行中', tone: 'blue' },
  finished: { label: '已结束', tone: 'muted' },
}

type FilterKey = 'all' | 'active' | 'finished'

function officialRosterOf(count: 6 | 12): Roster {
  return {
    werewolf: count === 6 ? 2 : 4,
    villager: count === 6 ? 2 : 4,
    seer: 1,
    witch: 1,
    hunter: count === 12 ? 1 : 0,
    guard: count === 12 ? 1 : 0,
  }
}

function shortId(id: string) {
  return id.length > 8 ? id.slice(0, 8) : id
}

function fmtTime(iso: string) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

export default function LobbyPage() {
  const navigate = useNavigate()
  const authUser = useAuthStore(s => s.user)
  const authLoad = useAuthStore(s => s.load)

  // ─── 创建对局表单 ───
  const [selectedMode, setSelectedMode] = useState<'pure_ai' | 'mixed' | null>(null)
  const [playerCount, setPlayerCount] = useState<6 | 12>(6)
  const [rosterType, setRosterType] = useState<'official' | 'custom'>('official')
  const [roster, setRoster] = useState<Roster>(officialRosterOf(6))
  const [playerName, setPlayerName] = useState('')
  const [creating, setCreating] = useState(false)

  const updateCount = (count: 6 | 12) => {
    setPlayerCount(count)
    setRosterType('official')
    setRoster(officialRosterOf(count))
  }

  const validationErrors = useMemo(() => {
    const total = Object.values(roster).reduce((s, v) => s + v, 0)
    return [
      total !== playerCount ? `当前 ${total} / ${playerCount} 人` : '',
      roster.werewolf !== (playerCount === 6 ? 2 : 4) ? `${playerCount} 人局狼人必须为 ${playerCount === 6 ? 2 : 4} 名` : '',
      ...(['seer', 'witch', 'hunter', 'guard'] as const).filter((r) => roster[r] > 1).map((r) => `${roleCN[r]}最多 1 名`),
    ].filter(Boolean)
  }, [roster, playerCount])

  // ─── 对局大厅（多局管理）───
  const [games, setGames] = useState<GameSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState<FilterKey>('all')
  const [poolOpen, setPoolOpen] = useState(false)
  const [poolItems, setPoolItems] = useState<{ name: string; base_url: string; temperature: number | null; api_key_masked: string }[]>([])
  const [poolForm, setPoolForm] = useState({ name: '', api_key: '', base_url: '', temperature: null as number | null })
  const [poolSaving, setPoolSaving] = useState(false)
  const [presets, setPresets] = useState<{ name: string; mode: 'pure_ai' | 'mixed'; playerCount: 6 | 12; rosterType: 'official' | 'custom'; roster: Roster }[]>([])
  const [presetName, setPresetName] = useState('')

  useEffect(() => {
    try {
      const raw = localStorage.getItem('ww-presets')
      if (raw) setPresets(JSON.parse(raw))
    } catch { /* ignore */ }
  }, [])
  const savePreset = () => {
    if (!selectedMode) { message.warning('先选择模式'); return }
    const name = presetName.trim() || `预设${presets.length + 1}`
    const next = [...presets.filter(p => p.name !== name), { name, mode: selectedMode, playerCount, rosterType, roster }]
    setPresets(next)
    localStorage.setItem('ww-presets', JSON.stringify(next))
    message.success(`已保存预设：${name}`)
    setPresetName('')
  }
  const applyPreset = (name: string) => {
    const p = presets.find(x => x.name === name)
    if (!p) return
    setSelectedMode(p.mode)
    setPlayerCount(p.playerCount)
    setRosterType(p.rosterType)
    setRoster(p.roster)
    message.success(`已套用预设：${name}`)
  }

  const openPool = async () => {
    try {
      const d = await apiService.listModelPool()
      setPoolItems(d.items || [])
      setPoolOpen(true)
    } catch {
      message.error('获取模型池失败')
    }
  }
  const refreshPool = async () => {
    const d = await apiService.listModelPool()
    setPoolItems(d.items || [])
  }
  const addPool = async () => {
    if (!poolForm.name.trim() || !poolForm.api_key.trim() || !poolForm.base_url.trim()) {
      message.warning('名称 / API Key / Base URL 必填')
      return
    }
    try {
      setPoolSaving(true)
      await apiService.upsertModelPool({
        name: poolForm.name.trim(),
        api_key: poolForm.api_key.trim(),
        base_url: poolForm.base_url.trim(),
        temperature: poolForm.temperature,
      })
      message.success('模型已保存')
      setPoolForm({ name: '', api_key: '', base_url: '', temperature: null })
      await refreshPool()
    } catch {
      message.error('保存失败')
    } finally {
      setPoolSaving(false)
    }
  }
  const delPool = async (name: string) => {
    try {
      await apiService.deleteModelPool(name)
      message.success('已删除')
      await refreshPool()
    } catch {
      message.error('删除失败')
    }
  }

  const loadGames = useCallback(async () => {
    try {
      setLoading(true)
      const { items } = await apiService.listGames(1, 50)
      setGames(items)
    } catch {
      // 忽略：大厅列表拉取失败不打断创建流程
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadGames()
    authLoad()
    const timer = setInterval(loadGames, 5000) // 实时反映多局并行状态
    return () => clearInterval(timer)
  }, [loadGames])

  const handleCreate = async () => {
    if (!selectedMode) { message.warning('请先选择游戏模式'); return }
    if (selectedMode === 'mixed' && !playerName.trim()) { message.warning('请输入玩家昵称'); return }
    try {
      setCreating(true)
      const result = await apiService.createGame({
        mode: selectedMode,
        player_name: selectedMode === 'mixed' ? playerName.trim() : undefined,
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

  const openGame = (g: GameSummary) =>
    navigate(g.status === 'finished' ? `/replay/${g.game_id}` : `/game/${g.game_id}`)

  const sortedGames = useMemo(() => {
    const rank = (s: string) => (s === 'playing' ? 0 : s === 'waiting' ? 1 : 2)
    return [...games].sort((a, b) => rank(a.status) - rank(b.status) || (a.created_at < b.created_at ? 1 : -1))
  }, [games])

  const visibleGames = useMemo(() => {
    if (filter === 'active') return sortedGames.filter((g) => g.status !== 'finished')
    if (filter === 'finished') return sortedGames.filter((g) => g.status === 'finished')
    return sortedGames
  }, [sortedGames, filter])

  const activeCount = games.filter((g) => g.status !== 'finished').length

  return (
    <div className="lobby">
      <div className="app-shell">
        {/* ─── topbar：金环头像 + 药丸 ─── */}
        <div className="lobby-topbar">
          <div className="lobby-profile">
            <div className="avatar-ring lobby-profile__avatar">月</div>
            <div>
              <div className="lobby-profile__name">月下的旅人</div>
              <small className="muted">真相，永不沉眠。</small>
            </div>
          </div>
          <div className="lobby-topbar__pills">
            {authUser ? (
              <Button size="small" onClick={() => navigate('/profile')}>{authUser.avatar || '👤'} {authUser.nickname || authUser.username}</Button>
            ) : (
              <Button size="small" onClick={() => navigate('/login')}>登录/注册</Button>
            )}
            <Button size="small" onClick={openPool}>🧩 模型池</Button>
            <span className="tag-pill"><i className="dot dot--gold" />{activeCount} 局进行中</span>
            <span className="tag-pill">◈ 共 {games.length} 场</span>
          </div>
        </div>

        {/* ─── Hero ─── */}
        <header className="lobby-hero">
          <div className="eyebrow">谎 言 之 下 · 是 更 近 的 我 们</div>
          <h1 className="display lobby-hero__title">狼人杀</h1>
          <p className="lobby-hero__sub">月光照见人心，也唤醒另一种生物。</p>
          <div className="lobby-quote">“好人会说谎，但狼人从不说真话。”</div>
        </header>

        {/* ─── 模式卡 ─── */}
        <div className="mode-grid">
          <button
            type="button"
            className={`mode-card ${selectedMode === 'pure_ai' ? 'mode-card--gold is-active' : ''}`}
            onClick={() => setSelectedMode('pure_ai')}
          >
            <span className="mode-card__icon">☀</span>
            <h3 className="display">纯 AI 对战</h3>
            <p>{playerCount} 个 AI Agent 自动博弈，可旁观推理</p>
          </button>
          <button
            type="button"
            className={`mode-card ${selectedMode === 'mixed' ? 'is-active' : ''}`}
            onClick={() => setSelectedMode('mixed')}
          >
            <span className="mode-card__icon">◈</span>
            <h3 className="display">人机混合</h3>
            <p>1 名人类 + {playerCount - 1} 个 AI，亲历推理博弈</p>
          </button>
        </div>

        {/* ─── 配置预设 ─── */}
        <div className="lobby-presets">
          <Select
            style={{ flex: 1 }}
            placeholder="套用预设（人数/阵容/模式）"
            value={undefined}
            onChange={applyPreset}
            options={presets.map(p => ({ label: p.name, value: p.name }))}
          />
          <Input style={{ width: 120 }} placeholder="预设名" value={presetName} onChange={e => setPresetName(e.target.value)} />
          <Button onClick={savePreset}>存为预设</Button>
        </div>

        {/* ─── banner ─── */}
        <div className="lobby-banner">
          <div>
            <strong className="display">真正的较量，从天黑开始</strong>
            <br />
            <span>逻辑 · 演技 · 社交 · 谁才是最后赢家？</span>
          </div>
          <span className="lobby-banner__arrow">›</span>
        </div>

        {/* ─── 创建对局（规则面板）─── */}
        <section className="panel lobby-create">
          <div className="section-title display">
            创建对局 <small className="muted">配置你的推理之夜</small>
          </div>

          {selectedMode === 'mixed' && (
            <div className="rule">
              <span className="rule-label">你的昵称</span>
              <input
                className="lobby-input"
                placeholder="输入昵称"
                value={playerName}
                maxLength={12}
                onChange={(e) => setPlayerName(e.target.value)}
              />
            </div>
          )}

          <div className="rule">
            <span className="rule-label">对局人数</span>
            <div className="seg">
              <button type="button" className={`seg-btn ${playerCount === 6 ? 'on' : ''}`} onClick={() => updateCount(6)}>6人</button>
              <button type="button" className={`seg-btn ${playerCount === 12 ? 'on' : ''}`} onClick={() => updateCount(12)}>12人</button>
            </div>
          </div>

          <div className="rule">
            <span className="rule-label">阵容配置</span>
            <div className="seg">
              <button
                type="button"
                className={`seg-btn ${rosterType === 'official' ? 'on' : ''}`}
                onClick={() => { setRosterType('official'); setRoster(officialRosterOf(playerCount)) }}
              >官方默认</button>
              <button type="button" className={`seg-btn ${rosterType === 'custom' ? 'on' : ''}`} onClick={() => setRosterType('custom')}>自定义</button>
            </div>
          </div>

          <div className="roster-grid">
            {(Object.keys(roster) as (keyof Roster)[]).map((role) => (
              <label key={role} className="roster-cell">
                <span className="roster-cell__name">{roleCN[role]}</span>
                <InputNumber
                  size="small"
                  min={0}
                  max={role === 'werewolf' ? (playerCount === 6 ? 2 : 4) : role === 'villager' ? playerCount : 1}
                  disabled={rosterType === 'official' || role === 'werewolf'}
                  value={roster[role]}
                  onChange={(v) => setRoster({ ...roster, [role]: Number(v || 0) })}
                />
              </label>
            ))}
          </div>
          {validationErrors.length > 0 && (
            <div className="roster-errors">
              {validationErrors.map((e) => <span key={e}>{e}</span>)}
            </div>
          )}

          <Button
            className="lobby-create__btn"
            type="primary"
            size="large"
            block
            loading={creating}
            disabled={!selectedMode || validationErrors.length > 0}
            onClick={handleCreate}
          >
            开始对局
          </Button>
        </section>

        {/* ─── 对局大厅（多局管理）─── */}
        <section className="lobby-games">
          <div className="section-title display">
            对局大厅
            <small className="muted">
              {activeCount} 局进行中
              <Tooltip title="每 5 秒自动刷新">
                <Button size="small" type="text" icon={<ReloadOutlined spin={loading} />} onClick={loadGames} />
              </Tooltip>
            </small>
          </div>

          <div className="seg lobby-games__filter">
            <button type="button" className={`seg-btn ${filter === 'all' ? 'on' : ''}`} onClick={() => setFilter('all')}>全部 {games.length}</button>
            <button type="button" className={`seg-btn ${filter === 'active' ? 'on' : ''}`} onClick={() => setFilter('active')}>进行中 {activeCount}</button>
            <button type="button" className={`seg-btn ${filter === 'finished' ? 'on' : ''}`} onClick={() => setFilter('finished')}>已结束 {games.length - activeCount}</button>
          </div>

          {loading && games.length === 0 ? (
            <div className="lobby-games__loading"><Spin /></div>
          ) : visibleGames.length === 0 ? (
            <Empty
              className="lobby-games__empty"
              image={<HistoryOutlined style={{ fontSize: 40, color: 'var(--text-3)' }} />}
              imageStyle={{ height: 56 }}
              description={<span className="muted">暂无对局，创建一局开始吧</span>}
            />
          ) : (
            <div className="game-cards">
              {visibleGames.map((g) => {
                const meta = statusMeta[g.status] || { label: g.status, tone: 'muted' as const }
                return (
                  <article key={g.game_id} className={`panel game-card game-card--${meta.tone}`} onClick={() => openGame(g)}>
                    <div className="game-card__top">
                      <span className={`dot dot--${meta.tone}`} />
                      <span className="game-card__status">{meta.label}</span>
                      <span className="tag-pill game-card__mode">{g.mode === 'pure_ai' ? '纯 AI' : '混合'}</span>
                    </div>
                    <div className="game-card__id display">#{shortId(g.game_id)}</div>
                    <div className="game-card__meta">
                      <span>第 {g.total_rounds || 0} 轮</span>
                      {g.status === 'finished' && g.winner && (
                        <span className={g.winner === 'werewolf' ? 'win-wolf' : 'win-good'}>
                          {g.winner === 'werewolf' ? '🐺 狼人胜' : '🕊️ 好人胜'}
                        </span>
                      )}
                      <span className="muted">{fmtTime(g.created_at)}</span>
                    </div>
                    <Button
                      className="game-card__btn"
                      size="small"
                      type={g.status === 'finished' ? 'default' : 'primary'}
                      icon={g.status === 'finished' ? <HistoryOutlined /> : <PlayCircleOutlined />}
                      onClick={(e) => { e.stopPropagation(); openGame(g) }}
                    >
                      {g.status === 'finished' ? '回放' : g.status === 'waiting' ? '开始' : '进入'}
                    </Button>
                  </article>
                )
              })}
            </div>
          )}
        </section>
      </div>

      {/* ─── 模型池管理弹窗 ─── */}
      <Modal
        title="自定义模型池"
        open={poolOpen}
        onCancel={() => setPoolOpen(false)}
        footer={null}
        width={520}
      >
        <p className="muted" style={{ fontSize: 12 }}>添加自己的模型（名称 / API Key / Base URL），存库后即刻可选，无需改服务器配置。</p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 12 }}>
          <Input placeholder="模型名称（唯一）" value={poolForm.name} onChange={e => setPoolForm({ ...poolForm, name: e.target.value })} />
          <Input placeholder="API Key" value={poolForm.api_key} onChange={e => setPoolForm({ ...poolForm, api_key: e.target.value })} />
          <Input placeholder="Base URL（OpenAI-compatible）" value={poolForm.base_url} onChange={e => setPoolForm({ ...poolForm, base_url: e.target.value })} />
          <InputNumber style={{ width: 140 }} placeholder="温度(可选)" min={0} max={2} step={0.1} value={poolForm.temperature} onChange={v => setPoolForm({ ...poolForm, temperature: v as number | null })} />
          <Button type="primary" loading={poolSaving} onClick={addPool}>保存模型</Button>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {poolItems.length === 0 && <span className="muted" style={{ fontSize: 12 }}>暂无自定义模型</span>}
          {poolItems.map(it => (
            <div key={it.name} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
              <span style={{ flex: 1 }}>{it.name} <span className="muted">({it.api_key_masked})</span></span>
              <Button size="small" danger onClick={() => delPool(it.name)}>删除</Button>
            </div>
          ))}
        </div>
      </Modal>
    </div>
  )
}
