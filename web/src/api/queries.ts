import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client'
import {
  TRANSITIONAL,
  type BackupList,
  type ConfigRead,
  type Schedule,
  type ScheduleWrite,
  type ConfigSummary,
  type FieldValues,
  type Option,
  type Server,
  type ServerCreate,
  type ServerUpdateResult,
  type TemplateDetail,
  type TemplateSummary,
} from './types'

// Query keys identify cached data. Same key → same cache entry, shared by every component.
const keys = {
  templates: ['templates'] as const,
  template: (id: string) => ['templates', id] as const,
  options: (templateId: string, fieldId: string, params: Record<string, string>) =>
    ['templates', templateId, 'options', fieldId, params] as const,
  servers: ['servers'] as const,
  server: (id: string) => ['servers', id] as const,
  installLog: (id: string) => ['servers', id, 'install-log'] as const,
  configs: (id: string) => ['servers', id, 'configs'] as const,
  config: (id: string, configId: string) => ['servers', id, 'configs', configId] as const,
  backups: (id: string) => ['servers', id, 'backups'] as const,
  schedules: (id: string) => ['servers', id, 'schedules'] as const,
  meta: ['meta'] as const,
}

const POLL_MS = 1500

export function useTemplates() {
  return useQuery({
    queryKey: keys.templates,
    queryFn: () => api<TemplateSummary[]>('/templates'),
  })
}

export function useTemplate(id: string) {
  return useQuery({
    queryKey: keys.template(id),
    queryFn: () => api<TemplateDetail>(`/templates/${id}`),
  })
}

export function useFieldOptions(
  templateId: string,
  fieldId: string,
  params: Record<string, string>,
  enabled: boolean,
) {
  return useQuery({
    // params are part of the key: change `loader` → new request, old list stays cached.
    queryKey: keys.options(templateId, fieldId, params),
    queryFn: () =>
      api<Option[]>(
        `/templates/${templateId}/fields/${fieldId}/options?${new URLSearchParams(params)}`,
      ),
    enabled,
    staleTime: 10 * 60 * 1000,
  })
}

export function useServers() {
  return useQuery({
    queryKey: keys.servers,
    queryFn: () => api<Server[]>('/servers'),
    // Keep refreshing while something is installing / starting / stopping.
    refetchInterval: (query) =>
      query.state.data?.some((s) => TRANSITIONAL.includes(s.status)) ? POLL_MS : false,
  })
}

export function useServer(id: string) {
  return useQuery({
    queryKey: keys.server(id),
    queryFn: () => api<Server>(`/servers/${id}`),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status && TRANSITIONAL.includes(status) ? POLL_MS : false
    },
  })
}

export function useInstallLog(id: string, live: boolean) {
  return useQuery({
    queryKey: keys.installLog(id),
    queryFn: () => api<string[]>(`/servers/${id}/install-log`),
    refetchInterval: live ? POLL_MS : false,
  })
}

/** After any change to a server, refresh both the list and that server's page. */
function useUpdateServerCache() {
  const queryClient = useQueryClient()
  return (server: Server) => {
    queryClient.setQueryData(keys.server(server.id), server)
    queryClient.invalidateQueries({ queryKey: keys.servers, exact: true })
    queryClient.invalidateQueries({ queryKey: keys.installLog(server.id) })
  }
}

export function useCreateServer() {
  const update = useUpdateServerCache()
  return useMutation({
    mutationFn: (body: ServerCreate) =>
      api<Server>('/servers', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: update,
  })
}

export type ServerAction = 'start' | 'stop' | 'restart' | 'reinstall'

export function useServerAction(id: string) {
  const update = useUpdateServerCache()
  return useMutation({
    mutationFn: (action: ServerAction) => api<Server>(`/servers/${id}/${action}`, { method: 'POST' }),
    onSuccess: update,
  })
}

export function useUpdateServer(id: string) {
  const update = useUpdateServerCache()
  return useMutation({
    mutationFn: (body: { name?: string; values: FieldValues }) =>
      api<ServerUpdateResult>(`/servers/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    onSuccess: (result) => update(result.server),
  })
}

export function useConfigs(serverId: string) {
  return useQuery({
    queryKey: keys.configs(serverId),
    queryFn: () => api<ConfigSummary[]>(`/servers/${serverId}/configs`),
  })
}

export function useConfig(serverId: string, configId: string) {
  return useQuery({
    queryKey: keys.config(serverId, configId),
    queryFn: () => api<ConfigRead>(`/servers/${serverId}/configs/${configId}`),
  })
}

export function useUpdateConfig(serverId: string, configId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (values: Record<string, string>) =>
      api<ConfigRead>(`/servers/${serverId}/configs/${configId}`, {
        method: 'PUT',
        body: JSON.stringify({ values }),
      }),
    onSuccess: (config) => queryClient.setQueryData(keys.config(serverId, configId), config),
  })
}

export function useBackups(serverId: string, serverBusy: boolean) {
  return useQuery({
    queryKey: keys.backups(serverId),
    queryFn: () => api<BackupList>(`/servers/${serverId}/backups`),
    // Poll while a backup is being made or restored, to show when it's done.
    refetchInterval: (query) =>
      serverBusy || query.state.data?.backups.some((b) => b.status === 'creating') ? POLL_MS : false,
  })
}

export function useBackupActions(serverId: string) {
  const queryClient = useQueryClient()
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: keys.backups(serverId) })
    queryClient.invalidateQueries({ queryKey: keys.server(serverId) })
  }
  const base = `/servers/${serverId}/backups`
  return {
    create: useMutation({
      mutationFn: (note: string) => api(base, { method: 'POST', body: JSON.stringify({ note }) }),
      onSettled: refresh,
    }),
    restore: useMutation({
      mutationFn: (backupId: string) => api(`${base}/${backupId}/restore`, { method: 'POST' }),
      onSettled: refresh,
    }),
    remove: useMutation({
      mutationFn: (backupId: string) => api(`${base}/${backupId}`, { method: 'DELETE' }),
      onSettled: refresh,
    }),
  }
}

export function useMeta() {
  return useQuery({
    queryKey: keys.meta,
    queryFn: () => api<{ timezone: string }>('/meta'),
    staleTime: Infinity,
  })
}

export function useSchedules(serverId: string) {
  return useQuery({
    queryKey: keys.schedules(serverId),
    queryFn: () => api<Schedule[]>(`/servers/${serverId}/schedules`),
    // Next/last run times change on their own as schedules fire.
    refetchInterval: 30_000,
  })
}

export function useScheduleActions(serverId: string) {
  const queryClient = useQueryClient()
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: keys.schedules(serverId) })
    // Running one now may start a backup or change the server's status.
    queryClient.invalidateQueries({ queryKey: keys.backups(serverId) })
    queryClient.invalidateQueries({ queryKey: keys.server(serverId) })
  }
  const base = `/servers/${serverId}/schedules`
  return {
    save: useMutation({
      mutationFn: ({ id, body }: { id: string | null; body: ScheduleWrite }) =>
        api<Schedule>(id ? `${base}/${id}` : base, { method: id ? 'PUT' : 'POST', body: JSON.stringify(body) }),
      onSettled: refresh,
    }),
    remove: useMutation({
      mutationFn: (id: string) => api(`${base}/${id}`, { method: 'DELETE' }),
      onSettled: refresh,
    }),
    runNow: useMutation({
      mutationFn: (id: string) => api<Schedule>(`${base}/${id}/run`, { method: 'POST' }),
      onSettled: refresh,
    }),
  }
}

export function useDeleteServer() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api<void>(`/servers/${id}`, { method: 'DELETE' }),
    onSuccess: (_, id) => {
      queryClient.removeQueries({ queryKey: keys.server(id) })
      queryClient.invalidateQueries({ queryKey: keys.servers, exact: true })
    },
  })
}
