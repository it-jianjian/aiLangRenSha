/**
 * 玩家列表 — 参考稿「游戏局内」双列 mini-player 布局
 * 发言中高亮蓝框、死亡置灰、可选目标可点击；保留 存活/💀淘汰/自己/同伴 等状态文本。
 */
import type { PlayerInfo } from '../../types'

/** 角色中文名 */
const ROLE_CN: Record<string, string> = {
  werewolf: '狼人', villager: '村民', seer: '预言家', witch: '女巫', hunter: '猎人', guard: '守卫',
}

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
  seatModels?: Record<number, string>
  marks?: Record<number, { mark: 'suspect' | 'trust' | ''; note: string }>
  onMark?: (seat: number) => void
}

/** 提取模型名简短显示 */
const shortModelName = (fullName: string) => {
  if (!fullName) return ''
  const parts = fullName.split('/')
  const name = parts.length > 1 ? parts[1] : parts[0]
  const dashIdx = name.indexOf('-')
  if (dashIdx === -1) return name
  const base = name.slice(0, dashIdx)
  const suffix = name.slice(dashIdx + 1)
  if (/^v?\d/i.test(suffix) && suffix.length <= 6) return `${base}-${suffix}`
  return base
}

export default function PlayerTable({
  players, mySeat, myCompanions, selectableSeats, onSeatClick, speakingSeat, gameMode, modelName, seatModels, marks, onMark,
}: PlayerTableProps) {
  const half = Math.ceil(players.length / 2)
  const columns = [players.slice(0, half), players.slice(half)]

  return (
    <div className="gp-players">
      {columns.map((col, ci) => (
        <div className="gp-players__col" key={ci}>
          {col.map((p) => {
            const isSelf = mySeat === p.seat_number
            const isCompanion = myCompanions.includes(p.seat_number) && !isSelf
            const isSelectable = selectableSeats.includes(p.seat_number)
            const isSpeaking = speakingSeat === p.seat_number && p.is_alive
            const revealed = p.role ? ROLE_CN[p.role] || p.role : null
            const pModel = seatModels?.[p.seat_number] || p.llm_model_name || (gameMode === 'pure_ai' ? modelName : '')
            const model = shortModelName(pModel || '')

            const status = !p.is_alive
              ? '💀淘汰'
              : isSpeaking
                ? '发言中…'
                : isSelf
                  ? '自己'
                  : isCompanion
                    ? '🐺同伴'
                    : revealed
                      ? revealed
                      : '存活'

            const cls = [
              'mini-player',
              !p.is_alive && 'mini-player--dead',
              isSelf && 'mini-player--self',
              isCompanion && 'mini-player--companion',
              isSpeaking && 'mini-player--speaking',
              isSelectable && 'mini-player--selectable',
            ].filter(Boolean).join(' ')

            return (
              <div
                key={p.seat_number}
                className={cls}
                onClick={isSelectable ? () => onSeatClick(p.seat_number) : undefined}
              >
                <span className="avatar-ring mini-player__avatar">{p.seat_number}</span>
                <span className="mini-player__body">
                  <b>{p.seat_number}. {p.player_name}</b>
                  <small className={isSpeaking ? 'mini-player__status mini-player__status--speaking' : 'mini-player__status'}>
                    {status}
                  </small>
                  {model && <small className="mini-player__model" title={`模型: ${pModel}`}>{model}</small>}
                </span>
                {onMark && (
                  <button
                    type="button"
                    className={`mini-player__markbtn ${marks?.[p.seat_number]?.mark ? 'mini-player__markbtn--' + marks[p.seat_number].mark : ''}`}
                    onClick={(e) => { e.stopPropagation(); onMark(p.seat_number) }}
                    title="标记/笔记（仅本地）"
                  >
                    {marks?.[p.seat_number]?.mark === 'suspect' ? '⚠' : marks?.[p.seat_number]?.mark === 'trust' ? '🛡' : '✎'}
                  </button>
                )}
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}
