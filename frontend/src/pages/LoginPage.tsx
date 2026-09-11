/**
 * 登录 / 注册页（可选登录）
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Input, Tabs, message } from 'antd'
import { apiService } from '../services/api'
import { useAuthStore } from '../stores/authStore'
import BackButton from '../components/BackButton'

export default function LoginPage() {
  const navigate = useNavigate()
  const setAuth = useAuthStore(s => s.setAuth)
  const [tab, setTab] = useState<'login' | 'register'>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [nickname, setNickname] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (!username.trim() || !password) {
      message.warning('请输入用户名和密码')
      return
    }
    try {
      setBusy(true)
      const d = tab === 'login'
        ? await apiService.login({ username: username.trim(), password })
        : await apiService.register({ username: username.trim(), password, nickname: nickname.trim() || undefined })
      setAuth(d.token, d.user)
      message.success(tab === 'login' ? '登录成功' : '注册成功')
      navigate('/')
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '操作失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card panel">
        <div style={{ display: 'flex' }}>
          <BackButton label="返回大厅" />
        </div>
        <h2 className="display" style={{ textAlign: 'center', margin: '4px 0 12px' }}>狼人杀 · 账号</h2>
        <Tabs
          activeKey={tab}
          onChange={k => setTab(k as 'login' | 'register')}
          items={[
            { key: 'login', label: '登录' },
            { key: 'register', label: '注册' },
          ]}
        />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <Input placeholder="用户名" value={username} onChange={e => setUsername(e.target.value)} />
          <Input.Password placeholder="密码（至少 6 位）" value={password} onChange={e => setPassword(e.target.value)} onPressEnter={submit} />
          {tab === 'register' && (
            <Input placeholder="昵称（可选）" value={nickname} onChange={e => setNickname(e.target.value)} />
          )}
          <Button type="primary" block loading={busy} onClick={submit}>
            {tab === 'login' ? '登录' : '注册并登录'}
          </Button>
          <Button block type="text" onClick={() => navigate('/')}>暂不登录，直接进去</Button>
        </div>
      </div>
    </div>
  )
}
