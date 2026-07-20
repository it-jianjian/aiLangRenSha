/**
 * 当前发言大卡片 — 显示最近发言 + 摘要
 * 纯展示组件
 */

interface SpeechData {
  seat: number
  content: string
  isPk?: boolean
}

interface CurrentSpeechPanelProps {
  speeches: SpeechData[]
  eventLog: { type: string; text: string }[]
}

export default function CurrentSpeechPanel({ speeches, eventLog }: CurrentSpeechPanelProps) {
  const latest = speeches.length > 0 ? speeches[speeches.length - 1] : null
  const summaries = speeches.length > 1 ? speeches.slice(-4, -1) : []

  // 判断最新发言是否为遗言
  const isLatestLastWords = latest
    ? eventLog.some(e => e.type === 'last_words' && e.text.includes(`${latest.seat}号`))
    : false

  return (
    <div className="current-speech">
      <div className="current-speech__header">
        <span className="current-speech__icon">💬</span>
        <span>当前发言</span>
      </div>

      {latest ? (
        <div className="current-speech__main">
          <div className={`current-speech__card ${isLatestLastWords ? 'current-speech__card--last-words' : ''} ${latest.isPk ? 'current-speech__card--pk' : ''}`}>
            <div className="current-speech__card-header">
              <span className="current-speech__card-seat">{latest.seat}号</span>
              {latest.isPk && <span className="current-speech__pk-badge">PK</span>}
              {isLatestLastWords && <span className="current-speech__lw-badge">遗言</span>}
            </div>
            <div className="current-speech__card-content">
              {latest.content}
            </div>
          </div>
        </div>
      ) : (
        <div className="current-speech__waiting">
          <div className="current-speech__waiting-icon">🌙</div>
          <div>等待玩家发言</div>
        </div>
      )}

      {/* 最近发言摘要 */}
      {summaries.length > 0 && (
        <div className="current-speech__summaries">
          <div className="current-speech__summaries-title">最近发言</div>
          {summaries.map((s, i) => (
            <div key={i} className="current-speech__summary">
              <span className="current-speech__summary-seat">{s.seat}号</span>
              <span className="current-speech__summary-text">
                {s.content.length > 40 ? s.content.slice(0, 40) + '…' : s.content}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
