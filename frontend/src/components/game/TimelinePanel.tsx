/**
 * 对局时间线速览 — 按轮聚合关键事件（夜死/出局/投票/结束/发言数），点击展开该轮详情。
 * 纯展示组件，数据来自 GamePage 的 eventLog（条目带 round）。
 */
import { useMemo, useState } from 'react'

interface LogEntry {
  event_id?: string
  timestamp: string
  time: string
  type: string
  text: string
  phase: string
  round?: number
}

const DETAIL_TYPES = new Set(['death_announce', 'eliminate', 'vote_result', 'game_over', 'pk_announce', 'speech', 'last_words'])

export default function TimelinePanel({ eventLog }: { eventLog: LogEntry[] }) {
  const [openRound, setOpenRound] = useState<number | null>(null)

  const rounds = useMemo(() => {
    const map = new Map<number, LogEntry[]>()
    for (const e of eventLog) {
      const r = e.round ?? 0
      if (!map.has(r)) map.set(r, [])
      map.get(r)!.push(e)
    }
    return [...map.entries()].sort((a, b) => a[0] - b[0])
  }, [eventLog])

  if (rounds.length === 0) return null

  return (
    <div className="panel timeline-panel">
      <div className="gp-panel__header"><span>⏱</span><span>时间线速览</span></div>
      <div className="timeline">
        {rounds.map(([round, entries]) => {
          const deaths = entries.filter(e => e.type === 'death_announce' || e.type === 'eliminate')
          const votes = entries.filter(e => e.type === 'vote_result')
          const speeches = entries.filter(e => e.type === 'speech' || e.type === 'pk_speech')
          const over = entries.find(e => e.type === 'game_over')
          const open = openRound === round
          return (
            <div key={round} className="timeline__round">
              <button type="button" className="timeline__head" onClick={() => setOpenRound(open ? null : round)}>
                <span className="timeline__dot" />
                <span className="timeline__label">第 {round} 轮</span>
                <span className="timeline__badges">
                  {deaths.length > 0 && <i className="tb tb--death">☠ {deaths.length}</i>}
                  {votes.length > 0 && <i className="tb tb--vote">🗳 {votes.length}</i>}
                  {speeches.length > 0 && <i className="tb">💬 {speeches.length}</i>}
                  {over && <i className="tb tb--over">🏁 结束</i>}
                </span>
                <span className="timeline__toggle">{open ? '▾' : '▸'}</span>
              </button>
              {open && (
                <div className="timeline__detail">
                  {entries.filter(e => DETAIL_TYPES.has(e.type)).map((e, i) => (
                    <div key={e.event_id || i} className="timeline__item">{e.time} · {e.text}</div>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
