/**
 * 回放界面
 *
 * 职责：展示历史对局的完整过程，支持逐步播放
 * 路由：/replay/:gameId
 */

import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Card, Button, Space, Slider, Tag, Typography, Spin, message } from 'antd'
import { StepBackwardOutlined, StepForwardOutlined, PlayCircleOutlined, PauseCircleOutlined } from '@ant-design/icons'
import { apiService } from '../services/api'
import type { ReplayData, ReplayStep } from '../types'

const { Title, Text } = Typography

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

  return (
    <div style={{ maxWidth: 800, margin: '0 auto', padding: '20px' }}>
      <Title level={3}>对局回放</Title>

      {/* 角色映射 */}
      <Card size="small" title="角色揭示" style={{ marginBottom: 16 }}>
        <Space wrap>
          {Object.entries(replay.role_mapping).map(([seat, role]) => (
            <Tag key={seat} color={role === 'werewolf' ? 'red' : role === 'seer' ? 'purple' : role === 'witch' ? 'green' : 'blue'}>
              {seat}号: {role === 'werewolf' ? '狼人' : role === 'seer' ? '预言家' : role === 'witch' ? '女巫' : '村民'}
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
            <br /><br />
            <Text>{currentEvent.description}</Text>
            {currentEvent.event_data && (
              <pre style={{ marginTop: 8, background: '#f5f5f5', padding: 12, borderRadius: 8, fontSize: 12 }}>
                {JSON.stringify(currentEvent.event_data, null, 2)}
              </pre>
            )}
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

      {/* 事件时间线 */}
      <Card title="事件时间线" style={{ marginTop: 16 }}>
        <div style={{ maxHeight: 300, overflowY: 'auto' }}>
          {replay.steps.map((step, i) => (
            <div
              key={i}
              style={{
                padding: '6px 12px',
                marginBottom: 4,
                borderRadius: 4,
                background: i === currentStep ? '#e6f4ff' : '#f5f5f5',
                cursor: 'pointer',
              }}
              onClick={() => { setCurrentStep(i); setPlaying(false) }}
            >
              <Tag color={phaseColor(step.phase)} style={{ marginRight: 8 }}>
                {phaseLabel(step.phase)}
              </Tag>
              <Text type={i === currentStep ? undefined : 'secondary'}>
                {step.description}
              </Text>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}
