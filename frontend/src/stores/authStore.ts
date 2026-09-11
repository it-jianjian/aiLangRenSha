/**
 * 账号状态（可选登录）— token 存 localStorage，user 内存缓存。
 */
import { create } from 'zustand'
import { apiService } from '../services/api'

export interface AuthUser {
  id: string
  username: string
  nickname: string | null
  avatar: string | null
  bio: string | null
  email: string | null
}

interface AuthState {
  token: string | null
  user: AuthUser | null
  setAuth: (token: string, user: AuthUser) => void
  logout: () => void
  load: () => Promise<void>
}

export const useAuthStore = create<AuthState>((set, get) => ({
  token: localStorage.getItem('ww-token'),
  user: null,
  setAuth: (token, user) => {
    localStorage.setItem('ww-token', token)
    set({ token, user })
  },
  logout: () => {
    localStorage.removeItem('ww-token')
    set({ token: null, user: null })
  },
  load: async () => {
    const t = get().token
    if (!t) {
      set({ user: null })
      return
    }
    try {
      const d = await apiService.me()
      set({ user: d.user })
    } catch {
      set({ user: null })
    }
  },
}))
