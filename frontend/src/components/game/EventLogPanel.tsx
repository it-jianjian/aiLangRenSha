/**
 * 底部事件日志面板 — 横向宽面板 + 筛选标签
 * 纯展示组件
 */
import { useState, useRef, useEffect } from 'react'

interface LogEntry {
  event_id?: string
  event_order?: string
  timestamp: string
  time: string
  type: string
  text: string
  phase: string
}

interface EventLogPanelProps {
  eventLog: LogEntry[]
  logEndRef: React.MutableRefObject<HTMLDivElement | null>
  getLogEntryClass: (type: string) => string
}

type FilterType = 'all' | 'speech' | 'vote' | 'death' | 'night' | 'system'

const FILTER_TABS: { key: FilterType; label: string; icon: string }[] = [
  { key: 'all', label: '全部', icon: '📋' },
  { key: 'speech', label: '发言', icon: '💬' },
  { key: 'vote', label: '投票', icon: '🗳️' },
  { key: 'death', label: '死亡', icon: '💀' },
  { key: 'night', label: '夜晚', icon: '🌙' },
  { key: 'system', label: '系统', icon: '⚙️' },
]

function matchesFilter(entry: LogEntry, filter: FilterType): boolean {
  if (filter === 'all') return true
  if (filter === 'speech') return ['speech', 'pk_speech', 'last_words'].includes(entry.type)
  if (filter === 'vote') return ['vote', 'vote_result', 'pk_announce'].includes(entry.type)
  if (filter === 'death') return ['death_announce', 'eliminate'].includes(entry.type)
  if (filter === 'night') return entry.type.includes('night') || entry.type === 'phase_change' && entry.phase === 'night'
  if (filter === 'system') return ['game_over', 'role_assign', 'phase_change'].includes(entry.type) || (!['speech', 'pk_speech', 'last_words', 'vote', 'vote_result', 'pk_announce', 'death_announce', 'eliminate'].includes(entry.type) && !entry.type.includes('night'))
  return true
}

export default function EventLogPanel({ eventLog, logEndRef, getLogEntryClass }: EventLogPanelProps) {
  const [filter, setFilter] = useState<FilterType>('all')
  const scrollRef = useRef<HTMLDivElement>(null)

  // 自动滚动到底部
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [eventLog, filter])

  const filtered = eventLog.filter(e => matchesFilter(e, filter))

  return (
    <div className="event-log-panel">
      {/* 筛选标签 */}
      <div className="event-log-panel__filters">
        {FILTER_TABS.map(tab => (
          <button
            key={tab.key}
            className={`event-log-panel__filter ${filter === tab.key ? 'event-log-panel__filter--active' : ''}`}
            onClick={() => setFilter(tab.key)}
          >
            <span>{tab.icon}</span>
            <span>{tab.label}</span>
          </button>
        ))}
      </div>

      {/* 日志内容 */}
      <div className="event-log-panel__scroll" ref={scrollRef}>
        {filtered.length === 0 ? (
          <div className="event-log-panel__empty">等待游戏开始...</div>
        ) : (
          filtered.map((entry, i) => (
            <div key={entry.event_id || i} className={getLogEntryClass(entry.type)}>
              <span className="log-entry__time">{entry.time}</span>
              {entry.text}
            </div>
          ))
        )}
        <div ref={logEndRef} />
      </div>
    </div>
  )
}
