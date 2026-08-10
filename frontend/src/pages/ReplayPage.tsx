/**
 * 回放界面
 *
 * 职责：展示历史对局的完整过程，支持逐步播放
 * 路由：/replay/:gameId
 */

import { useEffect, useState, useMemo } from 'react'
import { useParams } from 'react-router-dom'
import { Button, Space, Slider, Spin, message } from 'antd'
import { StepBackwardOutlined, StepForwardOutlined, PlayCircleOutlined, PauseCircleOutlined } from '@ant-design/icons'
import { apiService } from '../services/api'
import type { ReplayData, ReplayStep, Roster } from '../types'
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

export default function ReplayPage() {
  const { gameId } = useParams<{ gameId: string }>()
  const [loading, setLoading] = useState(true)
  const [replay, setReplay] = useState<ReplayData | null>(null)
  const [currentStep, setCurrentStep] = useState(0)
  const [playing, setPlaying] = useState(false)

  useEffect(() => {
    if (!gameId) return
    apiService.getReplay(gameId).then(setReplay).catch(() => message.error('加载回放失败')).finally(() => setLoading(false))
  }, [gameId])

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

  if (loading) return <div style={{ textAlign: 'center', padding: 100 }}><Spin size="large" /></div>
  if (!replay) return <div style={{ textAlign: 'center', padding: 100 }}><span style={{ color: 'var(--gp-text2)' }}>回放数据不可用</span></div>

  const currentEvent: ReplayStep | undefined = replay.steps[currentStep]

  const rosterSummary = (r: Roster) => {
    return Object.entries(r).filter(([, count]) => count > 0).map(([role, count]) => ({ role, count }))
  }

  return (
    <div className="replay-page">
      <div className="replay-page__inner">
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
      </div>
    </div>
  )
}
