import { apiFetch } from '@daastaan/api-types'

export interface AuthUser { id: string; email: string; role: string }

export const getCurrentUser = () => apiFetch<AuthUser>('/auth/me')
export const login = (email: string, password: string) => apiFetch<AuthUser>('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) })
export const signup = (email: string, password: string) => apiFetch<AuthUser>('/auth/signup', { method: 'POST', body: JSON.stringify({ email, password }) })
export const logout = () => apiFetch<void>('/auth/logout', { method: 'POST' })
