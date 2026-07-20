/**
 * 我的身份侧栏 — 显示角色、同伴、查验记录等私有信息
 * 纯展示组件，所有数据通过 props 传入
 */
import { Tag } from 'antd'
import type { Roster } from '../../types'

/** 角色中文名 */
const ROLE_CN: Record<string, string> = {
  werewolf: '狼人', villager: '村民', seer: '预言家', witch: '女巫', hunter: '猎人', guard: '守卫',
}

/** 角色图标 */
const ROLE_ICON: Record<string, string> = {
  werewolf: '🐺', villager: '👤', seer: '🔮', witch: '🧪', hunter: '🏹', guard: '🛡️',
}

/** 角色主题色 class */
const ROLE_THEME: Record<string, string> = {
  werewolf: 'identity-panel--werewolf',
  villager: 'identity-panel--villager',
  seer: 'identity-panel--seer',
  witch: 'identity-panel--witch',
  hunter: 'identity-panel--hunter',
  guard: 'identity-panel--guard',
}

interface IdentityPanelProps {
  myRole: string | null
  mySeat: number | null
  myCompanions: number[]
  seerResults: { target: number; result: string; round: number }[]
  witchInfo: { night_kill_target: number | null; save_available: boolean; poison_available: boolean } | null
  guardLastTarget?: number | null
  players: { seat_number: number; player_name: string; is_alive: boolean; role?: string | null }[]
  aliveCount: number
  totalPlayers: number
  currentRound: number
  gameMode: string | null
  roster: Roster | null
}

export default function IdentityPanel({
  myRole, mySeat, myCompanions, seerResults, witchInfo,
  aliveCount, totalPlayers, currentRound, gameMode,
}: IdentityPanelProps) {
  if (!myRole) return null

  const themeClass = ROLE_THEME[myRole] || 'identity-panel--villager'

  return (
    <div className={`identity-panel ${themeClass}`}>
      {/* 角色头像区 */}
      <div className="identity-panel__hero">
        <div className="identity-panel__avatar">
          {ROLE_ICON[myRole] || '👤'}
        </div>
        <div className="identity-panel__role-name">{ROLE_CN[myRole] || myRole}</div>
        {mySeat && <div className="identity-panel__seat">{mySeat}号座位</div>}
      </div>
      {/* 狼人同伴 */}
      {myRole === 'werewolf' && myCompanions.length > 0 && (
        <div className="identity-panel__section">
          <div className="identity-panel__section-title">🐺 狼人同伴</div>
          <div className="identity-panel__companions">
            {myCompanions.map(c => (
              <Tag key={c} color="red" style={{ margin: '2px' }}>{c}号</Tag>
            ))}
          </div>
        </div>
      )}

      {/* 预言家查验记录 */}
      {myRole === 'seer' && seerResults.length > 0 && (
        <div className="identity-panel__section">
          <div className="identity-panel__section-title">🔮 查验记录</div>
          {seerResults.map((r, i) => (
            <div key={i} className="identity-panel__seer-record">
              <Tag color={r.result === 'werewolf' ? 'red' : 'green'} style={{ fontSize: 11, margin: '2px 0' }}>
                第{r.round}轮: {r.target}号 {r.result === 'werewolf' ? '🐺 狼人' : '✅ 好人'}
              </Tag>
            </div>
          ))}
        </div>
      )}

      {/* 女巫药物状态 */}
      {myRole === 'witch' && witchInfo && (
        <div className="identity-panel__section">
          <div className="identity-panel__section-title">🧪 药物状态</div>
          <div className="identity-panel__witch-drugs">
            <div className={`witch-drug ${witchInfo.save_available ? 'witch-drug--available' : 'witch-drug--used'}`}>
              💚 解药: {witchInfo.save_available ? '可用' : '已用'}
            </div>
            <div className={`witch-drug ${witchInfo.poison_available ? 'witch-drug--available' : 'witch-drug--used'}`}>
              ☠️ 毒药: {witchInfo.poison_available ? '可用' : '已用'}
            </div>
          </div>
        </div>
      )}

      {/* 游戏信息 */}
      <div className="identity-panel__section">
        <div className="identity-panel__section-title">📊 对局信息</div>
        <div className="identity-panel__info-row">
          <span>当前轮次</span>
          <span className="identity-panel__info-value">第 {currentRound} 轮</span>
        </div>
        <div className="identity-panel__info-row">
          <span>存活人数</span>
          <span className="identity-panel__info-value">{aliveCount} / {totalPlayers}</span>
        </div>
        {gameMode && (
          <div className="identity-panel__info-row">
            <span>对局模式</span>
            <span className="identity-panel__info-value">{gameMode === 'mixed' ? '人机混合' : '纯AI'}</span>
          </div>
        )}
      </div>
    </div>
  )
}
