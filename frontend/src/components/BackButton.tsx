/**
 * 通用返回按钮 — 用于非根页面顶部，提供统一的「退出 / 返回」入口。
 *
 * 优先回退浏览器历史（navigate(-1)），符合「从哪进来退回哪」的直觉；
 * 若为直接打开的 URL（无历史栈），则回落到 to（默认大厅 '/'），
 * 保证任何入口进入的页面都能退出，不会「点进去出不来」。
 */
import { useNavigate } from 'react-router-dom'
import { ArrowLeftOutlined } from '@ant-design/icons'
import { Button } from 'antd'

interface BackButtonProps {
  /** 无浏览器历史时的回落目标，默认大厅 */
  to?: string
  label?: string
}

export default function BackButton({ to = '/', label = '返回' }: BackButtonProps) {
  const navigate = useNavigate()
  const goBack = () => {
    if (window.history.length > 1) navigate(-1)
    else navigate(to)
  }
  return (
    <Button type="text" icon={<ArrowLeftOutlined />} onClick={goBack}>
      {label}
    </Button>
  )
}
