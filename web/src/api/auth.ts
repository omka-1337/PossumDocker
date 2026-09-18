import { type QueryClient, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api } from './client'

// Mirrors panel/app/api/auth.py and users.py.

export type Permission =
  | 'view'
  | 'control'
  | 'console'
  | 'files'
  | 'backups'
  | 'restore'
  | 'settings'
  | 'schedules'
  | 'players'

export const PERMISSIONS: { value: Permission; label: string; help: string }[] = [
  { value: 'view', label: 'view', help: 'see the server, its status and console' },
  { value: 'control', label: 'start / stop', help: 'start, stop and restart' },
  { value: 'console', label: 'console', help: 'send console commands' },
  { value: 'files', label: 'files', help: 'file manager' },
  { value: 'backups', label: 'backups', help: 'make and download backups' },
  { value: 'restore', label: 'restore', help: "restore a backup over the server's files" },
  { value: 'settings', label: 'settings', help: 'game settings and config files' },
  { value: 'schedules', label: 'schedules', help: 'manage schedules' },
  { value: 'players', label: 'players', help: 'see players and their addresses, kick and ban' },
]

export interface Me {
  id: string
  username: string
  // Optional: null when the account has no address.
  email: string | null
  is_admin: boolean
}

export interface UserInfo extends Me {
  disabled: boolean
  created_at: string
  servers: number
}

export interface AccessEntry {
  user_id: string
  username: string
  disabled: boolean
  permissions: Permission[]
}

export const meKey = ['auth', 'me'] as const

/** null: not logged in. */
export function useMe() {
  return useQuery({
    queryKey: meKey,
    queryFn: async () => {
      try {
        return await api<Me>('/auth/me')
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null
        throw e
      }
    },
    staleTime: Infinity,
    retry: false,
  })
}

/**
 * Forget everything cached for the previous session and set who is logged in now.
 * Not queryClient.clear(): that also drops the `me` query the app is watching, and the new
 * value would land in a fresh query nobody observes, leaving the login page up until a reload.
 */
function resetSession(queryClient: QueryClient, me: Me | null) {
  queryClient.removeQueries({ predicate: (query) => query.queryHash !== JSON.stringify(meKey) })
  queryClient.setQueryData(meKey, me)
}

export function useLogin() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { username: string; password: string }) =>
      api<Me>('/auth/login', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: (me) => {
      // Drop anything cached for a previous user before showing the app.
      resetSession(queryClient, me)
    },
  })
}

export function useLogout() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api('/auth/logout', { method: 'POST' }),
    onSettled: () => resetSession(queryClient, null),
  })
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) =>
      api('/auth/password', { method: 'POST', body: JSON.stringify(body) }),
  })
}

const usersKey = ['users'] as const

export function useUsers() {
  return useQuery({ queryKey: usersKey, queryFn: () => api<UserInfo[]>('/users') })
}

export function useUserActions() {
  const queryClient = useQueryClient()
  const refresh = () => queryClient.invalidateQueries({ queryKey: usersKey })
  return {
    create: useMutation({
      mutationFn: (body: { username: string; password: string; email: string | null; is_admin: boolean }) =>
        api<UserInfo>('/users', { method: 'POST', body: JSON.stringify(body) }),
      onSettled: refresh,
    }),
    update: useMutation({
      mutationFn: ({
        id,
        ...body
      }: {
        id: string
        password?: string
        // "": remove the address the user has.
        email?: string
        is_admin?: boolean
        disabled?: boolean
      }) =>
        api<UserInfo>(`/users/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
      onSettled: refresh,
    }),
    remove: useMutation({
      mutationFn: (id: string) => api(`/users/${id}`, { method: 'DELETE' }),
      onSettled: refresh,
    }),
  }
}

export function useServerPermissions(serverId: string) {
  return useQuery({
    queryKey: ['servers', serverId, 'permissions'],
    queryFn: () => api<Permission[]>(`/servers/${serverId}/permissions`),
  })
}

export function useServerAccess(serverId: string) {
  return useQuery({
    queryKey: ['servers', serverId, 'access'],
    queryFn: () => api<AccessEntry[]>(`/servers/${serverId}/access`),
  })
}

export function useSetAccess(serverId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ userId, permissions }: { userId: string; permissions: Permission[] }) =>
      api<AccessEntry>(`/servers/${serverId}/access/${userId}`, {
        method: 'PUT',
        body: JSON.stringify({ permissions }),
      }),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['servers', serverId, 'access'] })
      queryClient.invalidateQueries({ queryKey: usersKey })
    },
  })
}
