import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { api } from '../api/client'
import { getStoredToken, setStoredToken } from '../api/client'

export interface AuthUser {
  id: number
  username: string
  role: string
}

type AuthContextValue = {
  user: AuthUser | null
  loading: boolean
  login: (token: string) => Promise<void>
  logout: () => void
  refreshUser: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)

  const refreshUser = useCallback(async () => {
    const token = getStoredToken()
    if (!token) {
      setUser(null)
      setLoading(false)
      return
    }
    try {
      const me = await api.get<AuthUser>('/auth/me')
      setUser(me)
    } catch {
      setStoredToken(null)
      setUser(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refreshUser()
  }, [refreshUser])

  useEffect(() => {
    const on401 = () => {
      setStoredToken(null)
      setUser(null)
    }
    window.addEventListener('auth:401', on401)
    return () => window.removeEventListener('auth:401', on401)
  }, [])

  const login = useCallback(
    async (token: string) => {
      setStoredToken(token)
      try {
        const me = await api.get<AuthUser>('/auth/me')
        setUser(me)
      } catch {
        setStoredToken(null)
        throw new Error('Failed to load user')
      }
    },
    []
  )

  const logout = useCallback(() => {
    setStoredToken(null)
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, refreshUser }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
