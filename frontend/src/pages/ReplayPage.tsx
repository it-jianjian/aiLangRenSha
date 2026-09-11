/**
 * 回放界面
 *
 * 职责：展示历史对局的完整过程，支持逐步播放
 * 路由：/replay/:gameId
 */

import { useEffect, useState, useMemo } from 'react'
import { useParams } from 'react-router-dom'
import { Button, Space, Slider, Spin, message } from 'antd'
import { StepBackwardOutlined, StepForwardOutlined, PlayCircleOutlined, PauseCircleOutlined, ThunderboltOutlined, ReloadOutlined } from '@ant-design/icons'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine, ReferenceDot, ResponsiveContainer } from 'recharts'
import { apiService } from '../services/api'
import BackButton from '../components/BackButton'
import type { ReplayData, ReplayStep, Roster, ReviewData, WinPoint } from '../types'
import './ReplayPage.css'

const roleCN: Record<string, string> = {
  werewolf: '狼人', villager: '村民', seer: '预言家', witch: '女巫', hunter: '猎人', guard: '守卫'
}

const roleTagClass = (role: string) => {
  const map: Record<string, string> = {
    werewolf: 'replay-role-tag--werewolf',
    seer: 'replay-role-tag--seer',
    witch: 'replay-role-tag--witch',
    hunter: 'replay-role-tag--hunter',
    guard: 'replay-role-tag--guard',
    villager: 'replay-role-tag--villager',
  }
  return map[role] || 'replay-role-tag'
}

const phaseClass = (phase: string) => {
  if (phase === 'night') return 'replay-event__phase-tag--night'
  if (phase === 'day') return 'replay-event__phase-tag--day'
  return 'replay-event__phase-tag--system'
}

const phaseLabel = (phase: string) => {
  if (phase === 'night') return '夜晚'
  if (phase === 'day') return '白天'
  return '系统'
}

const phaseIcon = (phase: string) => {
  if (phase === 'night') return '🌙'
  if (phase === 'day') return '☀️'
  return '⚙️'
}

// 胜率曲线节点标签：开局 / R1夜 / R1日 / 终局
const checkpointLabel = (wp: WinPoint): string => {
  if (wp.checkpoint === 'start') return '开局'
  if (wp.checkpoint === 'final') return '终局'
  return `R${wp.round}${wp.checkpoint === 'after_night' ? '夜' : '日'}`
}

// 把胜率曲线节点映射到最接近的回放步骤索引（用于点击联动）
function winPointToReplayStep(wp: WinPoint, steps: ReplayStep[]): number {
  if (wp.checkpoint === 'start') return 0
  if (wp.checkpoint === 'final') return Math.max(0, steps.length - 1)
  const phase = wp.checkpoint === 'after_night' ? 'night' : 'day'
  let last = -1
  steps.forEach((s, i) => { if (s.round === wp.round && s.phase === phase) last = i })
  if (last >= 0) return last
  const r = steps.findIndex(s => s.round === wp.round)
  return r >= 0 ? r : 0
}

// 胜率曲线自定义 Tooltip
function CurveTooltip({ active, payload }: any) {
  if (!active || !payload || !payload.length) return null
  const d = payload[0].payload
  return (
    <div className="curve-tooltip">
      <div className="curve-tooltip__label">{d.label}</div>
      <div className="curve-tooltip__prob">好人胜率 {d.prob}%</div>
      {d.event_label && <div className="curve-tooltip__meta">{d.event_label}</div>}
      <div className="curve-tooltip__meta">🐺 {d.alive_wolves} 存活 · 👤 {d.alive_goods} 存活</div>
      {d.deaths && d.deaths.length > 0 && <div className="curve-tooltip__meta">💀 {d.deaths.join('、')}号出局</div>}
    </div>
  )
}

export default function ReplayPage() {
  const { gameId } = useParams<{ gameId: string }>()
  const [loading, setLoading] = useState(true)
  const [replay, setReplay] = useState<ReplayData | null>(null)
  const [review, setReview] = useState<ReviewData | null>(null)
  const [currentStep, setCurrentStep] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [generating, setGenerating] = useState(false)

  useEffect(() => {
    if (!gameId) return
    Promise.all([
      apiService.getReplay(gameId),
      apiService.getReview(gameId).catch(() => null),
    ])
      .then(([rp, rv]) => { setReplay(rp); setReview(rv) })
      .catch(() => message.error('加载回放失败'))
      .finally(() => setLoading(false))
  }, [gameId])

  const handleGenerateReview = async () => {
    if (!gameId) return
    setGenerating(true)
    try {
      const rv = await apiService.generateReview(gameId)
      setReview(rv)
      if (rv.is_fallback || !rv.insight) message.warning('AI 点评暂不可用，胜率曲线不受影响')
      else message.success('复盘点评已生成')
    } catch {
      message.error('生成复盘失败')
    } finally {
      setGenerating(false)
    }
  }

  useEffect(() => {
    if (!playing || !replay) return
    if (currentStep >= replay.total_steps - 1) { setPlaying(false); return }
    const timer = setTimeout(() => setCurrentStep(s => s + 1), 1500)
    return () => clearTimeout(timer)
  }, [playing, currentStep, replay])

  const groupedSteps = useMemo(() => {
    if (!replay) return []
    const groups: { round: number; phase: string; steps: { step: ReplayStep; index: number }[] }[] = []
    for (let i = 0; i < replay.steps.length; i++) {
      const step = replay.steps[i]
      const round = step.round ?? 0
      const phase = step.phase || 'system'
      let group = groups.find(g => g.round === round && g.phase === phase)
      if (!group) { group = { round, phase, steps: [] }; groups.push(group) }
      group.steps.push({ step, index: i })
    }
    return groups
  }, [replay])

  const curveData = useMemo(() => {
    if (!review) return []
    return review.win_curve.map(wp => ({
      ...wp,
      label: checkpointLabel(wp),
      prob: Math.round(wp.good_win_prob * 1000) / 10,
    }))
  }, [review])

  if (loading) return <div style={{ textAlign: 'center', padding: 100 }}><Spin size="large" /></div>
  if (!replay) return <div style={{ textAlign: 'center', padding: 100 }}><span style={{ color: 'var(--gp-text2)' }}>回放数据不可用</span></div>

  const currentEvent: ReplayStep | undefined = replay.steps[currentStep]

  const rosterSummary = (r: Roster) => {
    return Object.entries(r).filter(([, count]) => count > 0).map(([role, count]) => ({ role, count }))
  }

  return (
    <div className="replay-page">
      <div className="replay-page__inner">
        {/* ─── 返回 ─── */}
        <div style={{ display: 'flex', marginBottom: 4 }}>
          <BackButton />
        </div>

        {/* ─── 标题 ─── */}
        <div className="replay-header">
          <h2 className="replay-header__title">对局回放</h2>
          <p className="replay-header__subtitle">完整重现每一个推理瞬间</p>
        </div>

        {/* ─── 阵容摘要 ─── */}
        <div className="replay-panel">
          <div className="replay-panel__header">对局信息</div>
          <div className="replay-panel__body">
            <div className="replay-summary">
              <span className="replay-summary__item">{replay.player_count} 人局</span>
              <span className="replay-summary__item">
                胜方：<span className="replay-summary__value" style={{ color: replay.winner === 'werewolf' ? 'var(--gp-danger)' : 'var(--gp-success)' }}>
                  {replay.winner === 'werewolf' ? '狼人' : '好人'}
                </span>
              </span>
              <span className="replay-summary__item">原因：<span className="replay-summary__value">{replay.end_reason}</span></span>
            </div>
            {rosterSummary(replay.roster).length > 0 && (
              <div style={{ marginTop: 10 }}>
                <div style={{ fontSize: 12, color: 'var(--gp-text3)', marginBottom: 6 }}>阵容配置</div>
                <div className="replay-roles">
                  {rosterSummary(replay.roster).map(({ role, count }) => (
                    <span key={role} className={`replay-role-tag ${roleTagClass(role)}`}>
                      {roleCN[role] || role} ×{count}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* ─── 角色揭示 ─── */}
        <div className="replay-panel">
          <div className="replay-panel__header">角色揭示</div>
          <div className="replay-panel__body">
            <div className="replay-roles">
              {Object.entries(replay.role_mapping).map(([seat, role]) => (
                <span key={seat} className={`replay-role-tag ${roleTagClass(role)}`}>
                  {seat}号 {roleCN[role] || role}
                </span>
              ))}
            </div>
          </div>
        </div>

        {/* ─── 当前事件 ─── */}
        <div className="replay-panel">
          <div className="replay-panel__header" style={{ justifyContent: 'space-between' }}>
            <span>当前事件</span>
            {currentEvent && (
              <span className={`replay-event__phase-tag ${phaseClass(currentEvent.phase || 'system')}`}>
                {phaseIcon(currentEvent.phase || 'system')} {phaseLabel(currentEvent.phase || 'system')}
              </span>
            )}
          </div>
          <div className="replay-panel__body replay-event">
            {currentEvent && (
              <>
                <div className="replay-event__step">
                  <span className="replay-event__step-num">步骤 {currentStep + 1} / {replay.total_steps}</span>
                  {currentEvent.round != null && ` · 第 ${currentEvent.round} 轮`}
                </div>
                <div className="replay-event__desc">{currentEvent.description}</div>
              </>
            )}
          </div>
        </div>

        {/* ─── 播放控制 ─── */}
        <div className="replay-panel">
          <div className="replay-controls">
            <Slider
              className="replay-controls__slider"
              min={0}
              max={replay.total_steps - 1}
              value={currentStep}
              onChange={setCurrentStep}
            />
            <div className="replay-controls__buttons">
              <Button
                icon={<StepBackwardOutlined />}
                disabled={currentStep === 0}
                onClick={() => setCurrentStep(s => Math.max(0, s - 1))}
              />
              <Button
                type="primary"
                size="large"
                icon={playing ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
                onClick={() => {
                  if (currentStep >= replay.total_steps - 1) { setCurrentStep(0); setPlaying(true) }
                  else { setPlaying(!playing) }
                }}
              >
                {playing ? '暂停' : currentStep >= replay.total_steps - 1 ? '重播' : '播放'}
              </Button>
              <Button
                icon={<StepForwardOutlined />}
                disabled={currentStep >= replay.total_steps - 1}
                onClick={() => setCurrentStep(s => Math.min(replay.total_steps - 1, s + 1))}
              />
            </div>
          </div>
        </div>

        {/* ─── 事件时间线 ─── */}
        <div className="replay-panel">
          <div className="replay-panel__header">事件时间线</div>
          <div className="replay-timeline">
            {groupedSteps.map((group, gi) => (
              <div key={gi} className="replay-timeline__group">
                <div className={`replay-timeline__group-header ${group.phase === 'day' ? 'replay-timeline__group-header--day' : ''}`}>
                  {phaseIcon(group.phase)} 第 {group.round} 轮
                </div>
                {group.steps.map(({ step, index }) => (
                  <div
                    key={index}
                    className={`replay-timeline__item ${index === currentStep ? 'replay-timeline__item--active' : ''}`}
                    onClick={() => { setCurrentStep(index); setPlaying(false) }}
                  >
                    {step.description}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>

        {/* ─── 胜率曲线 ─── */}
        {review && review.win_curve.length > 0 && (
          <div className="replay-panel">
            <div className="replay-panel__header">📈 好人胜率曲线</div>
            <div className="replay-panel__body">
              <ResponsiveContainer width="100%" height={240}>
                <LineChart
                  data={curveData}
                  margin={{ top: 10, right: 16, bottom: 4, left: -12 }}
                  onClick={(state: any) => {
                    if (state && state.activeTooltipIndex != null && replay) {
                      const wp = review.win_curve[state.activeTooltipIndex]
                      if (wp) { setCurrentStep(winPointToReplayStep(wp, replay.steps)); setPlaying(false) }
                    }
                  }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" />
                  <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#8a93b0' }} interval={0} />
                  <YAxis domain={[0, 100]} unit="%" width={46} tick={{ fontSize: 11, fill: '#8a93b0' }} />
                  <Tooltip content={<CurveTooltip />} />
                  <ReferenceLine y={50} stroke="rgba(255,255,255,0.25)" strokeDasharray="4 4" />
                  <Line type="monotone" dataKey="prob" stroke="#6c8cff" strokeWidth={2} dot={{ r: 3, fill: '#6c8cff', strokeWidth: 0 }} activeDot={{ r: 6 }} />
                  {review.turning_points.map((tp, i) => {
                    const idx = review.win_curve.findIndex(w => w.step === tp.step)
                    if (idx < 0 || !curveData[idx]) return null
                    return <ReferenceDot key={i} x={curveData[idx].label} y={curveData[idx].prob} r={6} fill="#ffa502" stroke="#0a0e20" strokeWidth={1.5} />
                  })}
                </LineChart>
              </ResponsiveContainer>
              <div className="curve-legend">
                <span className="curve-legend__item"><i className="curve-legend__dot curve-legend__dot--turn" />胜率转折点</span>
                <span className="curve-legend__item muted">点击曲线节点可跳转到对应回放步骤</span>
              </div>
            </div>
          </div>
        )}

        {/* ─── AI 复盘点评 ─── */}
        {review && (
          <div className="replay-panel">
            <div className="replay-panel__header" style={{ justifyContent: 'space-between' }}>
              <span>🪄 AI 复盘点评</span>
              {review.generated && !review.is_fallback && (
                <Button size="small" type="text" icon={<ReloadOutlined />} loading={generating} onClick={handleGenerateReview}>重新生成</Button>
              )}
            </div>
            <div className="replay-panel__body">
              {!review.generated ? (
                <div className="review-empty">
                  <p className="muted" style={{ marginBottom: 12 }}>基于全局事实生成 MVP、关键转折与操作点评（上帝视角，仅本局结束后可用）。</p>
                  <Button type="primary" icon={<ThunderboltOutlined />} loading={generating} onClick={handleGenerateReview}>生成 AI 复盘</Button>
                </div>
              ) : (!review.insight || review.is_fallback) ? (
                <div className="review-empty"><p className="muted">AI 点评暂不可用（模型未配置或调用失败），胜率曲线不受影响。可稍后重试。</p></div>
              ) : (
                <div className="review-body">
                  {review.insight.summary && <div className="review-summary">「{review.insight.summary}」</div>}
                  {review.insight.mvp && (
                    <div className="review-mvp">
                      <span className="review-mvp__badge">🏆 MVP</span>
                      <span className="review-mvp__seat">{review.insight.mvp.seat}号</span>
                      <span className={`replay-role-tag ${roleTagClass(review.insight.mvp.role)}`}>{roleCN[review.insight.mvp.role] || review.insight.mvp.role}</span>
                      <span className="review-mvp__reason">{review.insight.mvp.reason}</span>
                    </div>
                  )}
                  {(review.insight.key_moments || []).length > 0 && (
                    <div className="review-section">
                      <div className="review-section__title">⚡ 关键转折</div>
                      {(review.insight.key_moments || []).map((m, i) => (
                        <div key={i} className="review-item">
                          <div className="review-item__head">{m.round != null && <span className="review-item__tag">第{m.round}轮</span>}{m.event}</div>
                          {m.impact && <div className="review-item__impact">影响：{m.impact}</div>}
                          {m.comment && <div className="review-item__comment">{m.comment}</div>}
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="review-cols">
                    {(review.insight.best_plays || []).length > 0 && (
                      <div className="review-section">
                        <div className="review-section__title review-section__title--good">👍 最佳操作</div>
                        {(review.insight.best_plays || []).map((p, i) => (
                          <div key={i} className="review-item">
                            <div className="review-item__head">{p.seat != null && <span className="review-item__tag">{p.seat}号</span>}{p.action}</div>
                            {p.comment && <div className="review-item__comment">{p.comment}</div>}
                          </div>
                        ))}
                      </div>
                    )}
                    {(review.insight.worst_plays || []).length > 0 && (
                      <div className="review-section">
                        <div className="review-section__title review-section__title--bad">👎 失误操作</div>
                        {(review.insight.worst_plays || []).map((p, i) => (
                          <div key={i} className="review-item">
                            <div className="review-item__head">{p.seat != null && <span className="review-item__tag">{p.seat}号</span>}{p.action}</div>
                            {p.comment && <div className="review-item__comment">{p.comment}</div>}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                  {(review.insight.camp_analysis?.good || review.insight.camp_analysis?.wolf) && (
                    <div className="review-section">
                      <div className="review-section__title">⚖️ 阵营分析</div>
                      {review.insight.camp_analysis?.good && <div className="review-camp"><b>好人：</b>{review.insight.camp_analysis.good}</div>}
                      {review.insight.camp_analysis?.wolf && <div className="review-camp"><b>狼人：</b>{review.insight.camp_analysis.wolf}</div>}
                    </div>
                  )}
                  {review.model_name && <div className="review-model muted">生成模型：{review.model_name}</div>}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
