import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client'

// Mirrors panel/app/api/players.py.

export interface PlayerInfo {
  // "id:<SteamID/UUID>" or "name:<name>"
  key: string
  name: string | null
  game_id: string | null
  ip: string | null
  online: boolean
  online_since: string | null
  first_seen: string
  last_seen: string
}

export type BanKind = 'player' | 'ip'

export interface BanInfo {
  kind: BanKind
  value: string
  name: string | null
  reason: string | null
  banned_at: string | null
  // null: permanent (or unknown, for a ban made in the game)
  expires_at: string | null
  banned_by: string | null
}

export interface PlayerAbilities {
  tracked: boolean
  refresh: boolean
  kick: boolean
  ban_by: 'name' | 'id' | null
  ip_bans: boolean
  bans_need_restart: boolean
  bans_by_panel: boolean
}

export interface PlayersData {
  players: PlayerInfo[]
  bans: BanInfo[]
  bans_error: string | null
  abilities: PlayerAbilities
}

// When a ban or unban takes effect.
export type Effective = 'now' | 'after restart' | 'on join'

const key = (serverId: string) => ['servers', serverId, 'players'] as const

export function usePlayers(serverId: string) {
  return useQuery({
    queryKey: key(serverId),
    queryFn: () => api<PlayersData>(`/servers/${serverId}/players`),
    // Joins and leaves arrive on their own.
    refetchInterval: 5000,
  })
}

export function usePlayerActions(serverId: string) {
  const queryClient = useQueryClient()
  const base = `/servers/${serverId}/players`
  const refresh = () => queryClient.invalidateQueries({ queryKey: key(serverId) })
  const post = <T>(path: string, body?: unknown) =>
    api<T>(`${base}/${path}`, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
  return {
    refresh: useMutation({ mutationFn: () => post<void>('refresh'), onSettled: refresh }),
    kick: useMutation({
      mutationFn: (body: { key: string; reason?: string }) => post<void>('kick', body),
      onSettled: refresh,
    }),
    ban: useMutation({
      mutationFn: (body: { kind: BanKind; key?: string; value?: string; reason?: string; minutes?: number }) =>
        post<{ effective: Effective }>('ban', body),
      onSettled: refresh,
    }),
    unban: useMutation({
      mutationFn: (body: { kind: BanKind; value: string }) => post<{ effective: Effective }>('unban', body),
      onSettled: refresh,
    }),
  }
}
