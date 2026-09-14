import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client'
import {
  TRANSITIONAL,
  type Option,
  type Server,
  type ServerCreate,
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
