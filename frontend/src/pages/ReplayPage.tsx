/**
 * 回放界面
 *
 * 职责：展示历史对局的完整过程，支持逐步播放
 * 路由：/replay/:gameId
 *
 * 2.0 改进：
 * - 顶部显示阵容计数摘要
 * - 事件时间线按轮次/昼夜分组
 * - 去除原始 JSON 展示，仅显示中文描述
 */

import { useEffect, useState, useMemo } from 'react'
import { useParams } from 'react-router-dom'
import { Card, Button, Space, Slider, Tag, Typography, Spin, message } from 'antd'
import { StepBackwardOutlined, StepForwardOutlined, PlayCircleOutlined, PauseCircleOutlined } from '@ant-design/icons'
import { apiService } from '../services/api'
import type { ReplayData, ReplayStep, Roster } from '../types'

const { Title, Text } = Typography

// 角色中文映射
const roleCN: Record<string, string> = {
  werewolf: '狼人', villager: '村民', seer: '预言家', witch: '女巫', hunter: '猎人', guard: '守卫'
}

export default function ReplayPage() {
  const { gameId } = useParams<{ gameId: string }>()
  const [loading, setLoading] = useState(true)
  const [replay, setReplay] = useState<ReplayData | null>(null)
  const [currentStep, setCurrentStep] = useState(0)
  const [playing, setPlaying] = useState(false)

  useEffect(() => {
    if (!gameId) return
    apiService.getReplay(gameId).then((data: ReplayData) => {
      setReplay(data)
    }).catch(() => {
      message.error('加载回放失败')
    }).finally(() => setLoading(false))
  }, [gameId])

  // 自动播放
  useEffect(() => {
    if (!playing || !replay) return
    if (currentStep >= replay.total_steps - 1) {
      setPlaying(false)
      return
    }
    const timer = setTimeout(() => {
      setCurrentStep(s => s + 1)
    }, 1500) // 1.5秒一步
    return () => clearTimeout(timer)
  }, [playing, currentStep, replay])

  // 按轮次分组事件
  const groupedSteps = useMemo(() => {
    if (!replay) return []
    const groups: { round: number; phase: string; steps: { step: ReplayStep; index: number }[] }[] = []
    for (let i = 0; i < replay.steps.length; i++) {
      const step = replay.steps[i]
      const round = step.round ?? 0
      const phase = step.phase || 'system'
      let group = groups.find(g => g.round === round && g.phase === phase)
      if (!group) {
        group = { round, phase, steps: [] }
        groups.push(group)
      }
      group.steps.push({ step, index: i })
    }
    return groups
  }, [replay])

  if (loading) {
    return <div style={{ textAlign: 'center', padding: 100 }}><Spin size="large" /></div>
  }

  if (!replay) {
    return <div style={{ textAlign: 'center', padding: 100 }}><Text type="secondary">回放数据不可用</Text></div>
  }

  const currentEvent: ReplayStep | undefined = replay.steps[currentStep]

  const phaseColor = (phase: string) => {
    if (phase === 'night') return 'blue'
    if (phase === 'day') return 'orange'
    return 'default'
  }

  const phaseLabel = (phase: string) => {
    if (phase === 'night') return '夜晚'
    if (phase === 'day') return '白天'
    return '系统'
  }

  // 阵容计数摘要
  const rosterSummary = (roster: Roster) => {
    return Object.entries(roster)
      .filter(([, count]) => count > 0)
      .map(([role, count]) => `${count} ${roleCN[role] || role}`)
      .join('、')
  }

  return (
    <div style={{ maxWidth: 800, margin: '0 auto', padding: '20px' }}>
      <Title level={3}>对局完整回放</Title>
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Tag>{replay.player_count} 人局</Tag>
          <Tag color={replay.winner === 'werewolf' ? 'red' : 'blue'}>
            {replay.winner === 'werewolf' ? '狼人胜利' : '好人胜利'}
          </Tag>
          <Text>胜负原因：{replay.end_reason}</Text>
          {rosterSummary(replay.roster) && (
            <Text type="secondary">阵容：{rosterSummary(replay.roster)}</Text>
          )}
        </Space>
      </Card>

      {/* 角色映射 */}
      <Card size="small" title="角色揭示" style={{ marginBottom: 16 }}>
        <Space wrap>
          {Object.entries(replay.role_mapping).map(([seat, role]) => (
            <Tag key={seat} color={role === 'werewolf' ? 'red' : role === 'seer' ? 'purple' : role === 'witch' ? 'green' : 'blue'}>
              {seat}号: {roleCN[role] || role}
            </Tag>
          ))}
        </Space>
      </Card>

      {/* 当前事件展示 */}
      <Card
        title="当前事件"
        style={{ marginBottom: 16 }}
        extra={
          <Tag color={phaseColor(currentEvent?.phase || 'system')}>
            {phaseLabel(currentEvent?.phase || 'system')}
          </Tag>
        }
      >
        {currentEvent && (
          <div>
            <Text strong>步骤 {currentStep + 1} / {replay.total_steps}</Text>
            {currentEvent.round != null && (
              <Text type="secondary"> · 第 {currentEvent.round} 轮</Text>
            )}
            <br /><br />
            <Text>{currentEvent.description}</Text>
          </div>
        )}
      </Card>

      {/* 播放控制 */}
      <Card>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Slider
            min={0}
            max={replay.total_steps - 1}
            value={currentStep}
            onChange={setCurrentStep}
          />
          <Space style={{ justifyContent: 'center', width: '100%' }} size="large">
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
                if (currentStep >= replay.total_steps - 1) {
                  setCurrentStep(0)
                  setPlaying(true)
                } else {
                  setPlaying(!playing)
                }
              }}
            >
              {playing ? '暂停' : currentStep >= replay.total_steps - 1 ? '重播' : '播放'}
            </Button>
            <Button
              icon={<StepForwardOutlined />}
              disabled={currentStep >= replay.total_steps - 1}
              onClick={() => setCurrentStep(s => Math.min(replay.total_steps - 1, s + 1))}
            />
          </Space>
        </Space>
      </Card>

      {/* 事件时间线 — 按轮次/昼夜分组 */}
      <Card title="事件时间线" style={{ marginTop: 16 }}>
        <div style={{ maxHeight: 400, overflowY: 'auto' }}>
          {groupedSteps.map((group, gi) => (
            <div key={gi} style={{ marginBottom: 12 }}>
              <div style={{
                padding: '4px 12px',
                background: group.phase === 'night' ? 'rgba(50,50,150,0.1)' : 'rgba(200,180,50,0.1)',
                borderRadius: 6,
                marginBottom: 4,
                fontWeight: 'bold',
              }}>
                <Tag color={phaseColor(group.phase)} style={{ marginRight: 8 }}>
                  {phaseLabel(group.phase)}
                </Tag>
                第 {group.round} 轮
              </div>
              {group.steps.map(({ step, index }) => (
                <div
                  key={index}
                  style={{
                    padding: '6px 12px 6px 24px',
                    marginBottom: 2,
                    borderRadius: 4,
                    background: index === currentStep ? '#e6f4ff' : '#f5f5f5',
                    cursor: 'pointer',
                    fontSize: 13,
                  }}
                  onClick={() => { setCurrentStep(index); setPlaying(false) }}
                >
                  <Text type={index === currentStep ? undefined : 'secondary'}>
                    {step.description}
                  </Text>
                </div>
              ))}
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}
