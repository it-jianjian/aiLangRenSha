/**
 * 圆桌玩家区域 — 6人环形布局 / 12人椭圆布局
 * 纯展示组件，所有数据通过 props 传入
 */
import { Avatar } from 'antd'
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
  /** 当前正在发言的座位号 */
  speakingSeat?: number | null
}

/** 6人圆桌座位位置 (百分比) */
const SEAT_POS_6: Record<number, { top: string; left: string }> = {
  1: { top: '42%', left: '2%' },
  2: { top: '2%', left: '30%' },
  3: { top: '42%', left: '68%' },
  4: { top: '78%', left: '68%' },
  5: { top: '92%', left: '30%' },
  6: { top: '78%', left: '2%' },
}

/** 12人椭圆座位位置 (百分比) */
const SEAT_POS_12: Record<number, { top: string; left: string }> = {
  1:  { top: '40%', left: '0%' },
  2:  { top: '12%', left: '6%' },
  3:  { top: '0%',  left: '26%' },
  4:  { top: '0%',  left: '48%' },
  5:  { top: '12%', left: '68%' },
  6:  { top: '40%', left: '78%' },
  7:  { top: '60%', left: '78%' },
  8:  { top: '88%', left: '68%' },
  9:  { top: '100%', left: '48%' },
  10: { top: '100%', left: '26%' },
  11: { top: '88%', left: '6%' },
  12: { top: '60%', left: '0%' },
}

export default function PlayerTable({
  players, mySeat, myCompanions, currentPhase, currentRound, isNight,
  selectableSeats, onSeatClick, speakingSeat,
}: PlayerTableProps) {
  const is6 = players.length <= 6
  const posMap = is6 ? SEAT_POS_6 : SEAT_POS_12

  return (
    <div className="round-table">
      {/* 玩家座位 */}
      <div className="round-table__seats">
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
              {/* 圆形头像占位 */}
              <div className="round-card__avatar">
                <Avatar
                  size={42}
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
