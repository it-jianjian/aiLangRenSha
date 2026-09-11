/**
 * 个人主页（需登录）— 资料编辑 + 我的对局 + 战绩统计
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Input, Statistic, Row, Col, Tag, message } from 'antd'
import { apiService } from '../services/api'
import { useAuthStore } from '../stores/authStore'
import BackButton from '../components/BackButton'

export default function ProfilePage() {
  const navigate = useNavigate()
  const { token, user, load, setAuth, logout } = useAuthStore()
  const [form, setForm] = useState({ nickname: '', avatar: '', bio: '', email: '' })
  const [games, setGames] = useState<any[]>([])
  const [stats, setStats] = useState<any>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!token) {
      navigate('/login')
      return
    }
    (async () => {
      await load()
      try {
        const [g, s] = await Promise.all([apiService.myGames(), apiService.myStats()])
        setGames(g.items || [])
        setStats(s)
      } catch { /* ignore */ }
    })()
  }, [token])

  useEffect(() => {
    if (user) setForm({ nickname: user.nickname || '', avatar: user.avatar || '', bio: user.bio || '', email: user.email || '' })
  }, [user])

  const save = async () => {
    try {
      setBusy(true)
      const d = await apiService.updateMe(form)
      setAuth(token!, d.user)
      message.success('已保存')
    } catch {
      message.error('保存失败')
    } finally {
      setBusy(false)
    }
  }

  if (!token) return null

  return (
    <div className="app-shell" style={{ maxWidth: 860 }}>
      <div style={{ display: 'flex', marginBottom: 8 }}>
        <BackButton />
      </div>
      <div className="panel" style={{ padding: 16, marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
          <span className="avatar-ring" style={{ width: 52, height: 52, fontSize: 22 }}>{user?.avatar || (user?.nickname || '游')[0]}</span>
          <div>
            <div style={{ fontSize: 18, fontWeight: 700 }}>{user?.nickname || user?.username}</div>
            <div className="muted" style={{ fontSize: 12 }}>@{user?.username}</div>
          </div>
          <Button style={{ marginLeft: 'auto' }} onClick={() => { logout(); navigate('/') }}>退出登录</Button>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 8 }}>
          <Input addonBefore="昵称" value={form.nickname} onChange={e => setForm({ ...form, nickname: e.target.value })} />
          <Input addonBefore="头像" placeholder="emoji/单字" value={form.avatar} onChange={e => setForm({ ...form, avatar: e.target.value })} />
          <Input addonBefore="邮箱" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} />
        </div>
        <Input.TextArea style={{ marginTop: 8 }} rows={2} placeholder="个人简介" value={form.bio} onChange={e => setForm({ ...form, bio: e.target.value })} />
        <Button type="primary" style={{ marginTop: 10 }} loading={busy} onClick={save}>保存资料</Button>
      </div>

      {stats && (
        <div className="panel" style={{ padding: 16, marginBottom: 12 }}>
          <div className="section-title" style={{ marginBottom: 10 }}>战绩</div>
          <Row gutter={12}>
            <Col span={6}><Statistic title="参与对局" value={stats.games ?? 0} /></Col>
            <Col span={6}><Statistic title="胜" value={stats.wins ?? 0} valueStyle={{ color: '#58d786' }} /></Col>
            <Col span={6}><Statistic title="负" value={stats.losses ?? 0} valueStyle={{ color: '#ff6b6b' }} /></Col>
            <Col span={6}><Statistic title="创建对局" value={stats.games_created ?? 0} /></Col>
          </Row>
          <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
            狼人侧 {stats.as_werewolf?.wins ?? 0}/{stats.as_werewolf?.games ?? 0} · 好人侧 {stats.as_good?.wins ?? 0}/{stats.as_good?.games ?? 0}
          </div>
        </div>
      )}

      <div className="panel" style={{ padding: 16 }}>
        <div className="section-title" style={{ marginBottom: 10 }}>我的对局</div>
        {games.length === 0 && <div className="muted" style={{ fontSize: 13 }}>暂无对局</div>}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {games.map(g => (
            <div key={g.game_id} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
              <Tag color={g.status === 'finished' ? 'default' : g.status === 'playing' ? 'blue' : 'gold'}>{g.status}</Tag>
              <span style={{ flex: 1 }}>{g.mode === 'mixed' ? '人机混合' : '纯AI'} · 第{g.total_rounds}轮 {g.winner ? (g.winner === 'werewolf' ? '🐺狼人胜' : '🕊好人胜') : ''}</span>
              <Button size="small" onClick={() => navigate(g.status === 'finished' ? `/replay/${g.game_id}` : `/game/${g.game_id}`)}>
                {g.status === 'finished' ? '回放' : '进入'}
              </Button>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
