import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client'
import type { Option, Server, ServerCreate, TemplateDetail, TemplateSummary } from './types'

// Query keys identify cached data. Same key → same cache entry, shared by every component.
const keys = {
  templates: ['templates'] as const,
  template: (id: string) => ['templates', id] as const,
  options: (templateId: string, fieldId: string, params: Record<string, string>) =>
    ['templates', templateId, 'options', fieldId, params] as const,
  servers: ['servers'] as const,
}

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
  })
}

export function useCreateServer() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: ServerCreate) =>
      api<Server>('/servers', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.servers }),
  })
}

export function useDeleteServer() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api<void>(`/servers/${id}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.servers }),
  })
}
