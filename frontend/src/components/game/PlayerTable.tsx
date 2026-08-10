/**
 * 圆桌玩家区域 — 6人环形布局 / 12人椭圆布局
 * 支持鼠标拖拽平移 + 滚轮缩放
 */
import { useState, useRef, useCallback } from 'react'
import { Avatar, Tooltip } from 'antd'
import type { PlayerInfo } from '../../types'

/** 角色中文名 */
const ROLE_CN: Record<string, string> = {
  werewolf: '狼人', villager: '村民', seer: '预言家', witch: '女巫', hunter: '猎人', guard: '守卫',
}

/** 角色头像渐变色 */
const ROLE_AVATAR_BG: Record<string, string> = {
  werewolf: 'linear-gradient(135deg, #8b0000, #cc2233)',
  villager: 'linear-gradient(135deg, #4a5568, #718096)',
  seer: 'linear-gradient(135deg, #4c3fa0, #7c6cff)',
  witch: 'linear-gradient(135deg, #6b21a8, #a855f7)',
  hunter: 'linear-gradient(135deg, #b45309, #f59e0b)',
  guard: 'linear-gradient(135deg, #0e7490, #22d3ee)',
}

/** 座位号默认渐变色 */
const SEAT_GRADIENTS = [
  'linear-gradient(135deg, #3a5a8c, #5a8abc)',
  'linear-gradient(135deg, #5a3a7c, #8a5aac)',
  'linear-gradient(135deg, #3a6a5c, #5a9a8c)',
  'linear-gradient(135deg, #7a4a3c, #aa7a6c)',
  'linear-gradient(135deg, #3a4a6c, #6a7a9c)',
  'linear-gradient(135deg, #6a3a5c, #9a6a8c)',
  'linear-gradient(135deg, #4a6a3c, #7a9a6c)',
  'linear-gradient(135deg, #5a4a2c, #8a7a5c)',
  'linear-gradient(135deg, #2c5a6a, #5c8a9a)',
  'linear-gradient(135deg, #6a4a5c, #9a7a8c)',
  'linear-gradient(135deg, #3a5a4c, #6a8a7c)',
  'linear-gradient(135deg, #5a3a4c, #8a6a7c)',
]

interface PlayerTableProps {
  players: PlayerInfo[]
  mySeat: number | null
  myRole: string | null
  myCompanions: number[]
  currentPhase: string | null
  currentRound: number
  isNight: boolean
  selectableSeats: number[]
  onSeatClick: (seat: number) => void
  speakingSeat?: number | null
  gameMode?: string | null
  modelName?: string
}

/** 6人圆桌座位位置 (百分比) — 均匀分布 */
const SEAT_POS_6: Record<number, { top: string; left: string }> = {
  1: { top: '44%', left: '16%' },
  2: { top: '10%', left: '38%' },
  3: { top: '44%', left: '60%' },
  4: { top: '74%', left: '60%' },
  5: { top: '88%', left: '38%' },
  6: { top: '74%', left: '16%' },
}

/** 12人椭圆座位位置 (百分比) — 已内收避免卡片贴边溢出 */
const SEAT_POS_12: Record<number, { top: string; left: string }> = {
  1:  { top: '40%', left: '8%' },
  2:  { top: '14%', left: '14%' },
  3:  { top: '4%',  left: '30%' },
  4:  { top: '4%',  left: '50%' },
  5:  { top: '14%', left: '66%' },
  6:  { top: '40%', left: '74%' },
  7:  { top: '58%', left: '74%' },
  8:  { top: '82%', left: '66%' },
  9:  { top: '92%', left: '50%' },
  10: { top: '92%', left: '30%' },
  11: { top: '82%', left: '14%' },
  12: { top: '58%', left: '8%' },
}

export default function PlayerTable({
  players, mySeat, myCompanions, currentPhase, currentRound, isNight,
  selectableSeats, onSeatClick, speakingSeat, gameMode, modelName,
}: PlayerTableProps) {
  const is6 = players.length <= 6
  const posMap = is6 ? SEAT_POS_6 : SEAT_POS_12
  const isPureAI = gameMode === 'pure_ai'

  /** 提取模型名简短显示 */
  const shortModelName = (fullName: string) => {
    if (!fullName) return ''
    const parts = fullName.split('/')
    const name = parts.length > 1 ? parts[1] : parts[0]
    // 取第一个 '-' 之前的部分作为短名
    const dashIdx = name.indexOf('-')
    if (dashIdx === -1) return name
    const base = name.slice(0, dashIdx)
    const suffix = name.slice(dashIdx + 1)
    // 如果后缀很短（版本号），保留；如果是参数量/B/Instruct 等，丢弃
    if (/^v?\d/i.test(suffix) && suffix.length <= 6) return `${base}-${suffix}`
    return base
  }

  // ─── 拖拽 + 缩放状态 ───
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [zoom, setZoom] = useState(1)
  const isDragging = useRef(false)
  const dragStart = useRef({ x: 0, y: 0 })
  const panStart = useRef({ x: 0, y: 0 })
  const containerRef = useRef<HTMLDivElement>(null)

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    // 只响应左键
    if (e.button !== 0) return
    isDragging.current = true
    dragStart.current = { x: e.clientX, y: e.clientY }
    panStart.current = { ...pan }
    e.preventDefault()
  }, [pan])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!isDragging.current) return
    const dx = e.clientX - dragStart.current.x
    const dy = e.clientY - dragStart.current.y
    setPan({ x: panStart.current.x + dx, y: panStart.current.y + dy })
  }, [])

  const handleMouseUp = useCallback(() => {
    isDragging.current = false
  }, [])

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault()
    const delta = e.deltaY > 0 ? -0.08 : 0.08
    setZoom(z => Math.min(2.5, Math.max(0.5, z + delta)))
  }, [])

  const resetView = useCallback(() => {
    setPan({ x: 0, y: 0 })
    setZoom(1)
  }, [])

  return (
    <div className="round-table">
      {/* 缩放控制按钮 */}
      <div className="round-table__zoom-controls">
        <button onClick={() => setZoom(z => Math.min(2.5, z + 0.15))} title="放大">＋</button>
        <button onClick={() => setZoom(z => Math.max(0.5, z - 0.15))} title="缩小">－</button>
        <button onClick={resetView} title="重置视角">⟲</button>
      </div>

      {/* 可拖拽内容层 */}
      <div
        ref={containerRef}
        className="round-table__seats"
        style={{
          transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
          transformOrigin: 'center center',
          transition: isDragging.current ? 'none' : 'transform 0.15s ease-out',
          cursor: isDragging.current ? 'grabbing' : 'grab',
        }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onWheel={handleWheel}
      >
        {players.map((p) => {
          const pos = posMap[p.seat_number]
          if (!pos) return null

          const isSelf = mySeat === p.seat_number
          const isCompanion = myCompanions.includes(p.seat_number) && !isSelf
          const isSelectable = selectableSeats.includes(p.seat_number)
          const isSpeaking = speakingSeat === p.seat_number
          const revealedRole = p.role ? ROLE_CN[p.role] || p.role : null

          const classes = [
            'round-card',
            !p.is_alive && 'round-card--dead',
            isSelf && 'round-card--self',
            isCompanion && 'round-card--companion',
            isSelectable && 'round-card--selectable',
            isSpeaking && 'round-card--speaking',
          ].filter(Boolean).join(' ')

          return (
            <div
              key={p.seat_number}
              className={classes}
              style={{ top: pos.top, left: pos.left }}
              onClick={isSelectable ? () => onSeatClick(p.seat_number) : undefined}
            >
              {/* 圆形头像 */}
              <div className="round-card__avatar">
                <Tooltip title={(() => {
                  const pModel = p.llm_model_name || (isPureAI ? modelName : '')
                  return pModel ? `${p.player_name} · ${pModel}` : p.player_name
                })()}>
                  <Avatar
                    size={36}
                    style={{
                      background: !p.is_alive
                        ? '#444'
                        : p.role && ROLE_AVATAR_BG[p.role]
                          ? ROLE_AVATAR_BG[p.role]
                          : isSelf
                            ? 'linear-gradient(135deg, #1677ff, #4096ff)'
                            : isCompanion
                              ? 'linear-gradient(135deg, #8b0000, #cc2233)'
                              : SEAT_GRADIENTS[(p.seat_number - 1) % SEAT_GRADIENTS.length],
                      fontSize: 16,
                      fontWeight: 700,
                    }}
                  >
                    {p.seat_number}
                  </Avatar>
                </Tooltip>
              </div>

              {/* 座位号 */}
              <div className="round-card__seat">{p.seat_number}号</div>

              {/* 名称 */}
              <div className="round-card__name">{p.player_name}</div>

              {/* 标签区 */}
              <div className="round-card__tags">
                {p.is_alive
                  ? <span className="round-card__tag round-card__tag--alive">存活</span>
                  : <span className="round-card__tag round-card__tag--dead">💀淘汰</span>
                }
                {isSelf && <span className="round-card__tag round-card__tag--self">自己</span>}
                {isCompanion && <span className="round-card__tag round-card__tag--companion">🐺同伴</span>}
                {revealedRole && (
                  <span className={`round-card__tag ${p.role === 'werewolf' ? 'round-card__tag--werewolf' : 'round-card__tag--role'}`}>
                    {revealedRole}
                  </span>
                )}
              </div>

              {/* 纯AI模式：显示该玩家使用的模型名 */}
              {(() => {
                const pModel = p.llm_model_name || (isPureAI ? modelName : '')
                return pModel && revealedRole ? (
                  <div className="round-card__model" title={`模型: ${pModel}`}>
                    {shortModelName(pModel)}
                  </div>
                ) : null
              })()}

              {/* 死亡蒙层 */}
              {!p.is_alive && <div className="round-card__death-overlay" />}
            </div>
          )
        })}
      </div>

      {/* 中央装饰 */}
      <div className="round-table__center">
        <div className="round-table__icon">{isNight ? '🌙' : '☀️'}</div>
        <div className="round-table__phase">
          {isNight ? '夜晚' : currentPhase === 'day' ? '白天' : currentPhase || '等待中'}
        </div>
        {currentRound > 0 && (
          <div className="round-table__round">第 {currentRound} 轮</div>
        )}
      </div>
    </div>
  )
}
