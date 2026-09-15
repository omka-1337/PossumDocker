import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from './client'

// Mirrors panel/app/api/files.py. Paths are relative to the server's data volume, '' is the root.

export interface FileEntry {
  name: string
  type: 'file' | 'dir' | 'symlink' | 'other'
  size: number
  mtime: number // unix seconds
}

export interface Listing {
  path: string
  entries: FileEntry[]
}

export interface UploadItem {
  // Relative to the target folder, may include subfolders: "world/level.dat"
  path: string
  file: File
}

export const joinPath = (dir: string, name: string) => (dir ? `${dir}/${name}` : name)
export const parentPath = (path: string) => (path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '')
export const baseName = (path: string) => path.slice(path.lastIndexOf('/') + 1)
export const extension = (name: string) =>
  name.includes('.') ? name.slice(name.lastIndexOf('.') + 1).toLowerCase() : ''

const filesKey = (serverId: string) => ['servers', serverId, 'files'] as const
const base = (serverId: string) => `/servers/${serverId}/files`

export function useListing(serverId: string, path: string) {
  return useQuery({
    queryKey: [...filesKey(serverId), path],
    queryFn: () => api<Listing>(`${base(serverId)}?${new URLSearchParams({ path })}`),
    // Keep showing the old folder while the new one loads: no flash of "loading…".
    placeholderData: (previous) => previous,
  })
}

const post = (url: string, body: unknown) => api<unknown>(url, { method: 'POST', body: JSON.stringify(body) })

type Transfer = { sources: string[]; destination: string }

/** A file mutation that refreshes all cached folders of the server: a move touches two of them. */
function useFileMutation<T>(serverId: string, action: string, body: (arg: T) => unknown) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (arg: T) => post(`${base(serverId)}/${action}`, body(arg)),
    onSettled: () => queryClient.invalidateQueries({ queryKey: filesKey(serverId) }),
  })
}

export function useFileActions(serverId: string) {
  const queryClient = useQueryClient()
  return {
    refresh: () => queryClient.invalidateQueries({ queryKey: filesKey(serverId) }),
    mkdir: useFileMutation(serverId, 'mkdir', (path: string) => ({ path })),
    rename: useFileMutation(serverId, 'rename', (arg: { path: string; name: string }) => arg),
    move: useFileMutation(serverId, 'move', (arg: Transfer) => arg),
    copy: useFileMutation(serverId, 'copy', (arg: Transfer) => arg),
    remove: useFileMutation(serverId, 'delete', (paths: string[]) => ({ paths })),
    extract: useFileMutation(serverId, 'extract', (path: string) => ({ path })),
  }
}

export function readText(serverId: string, path: string) {
  return api<{ path: string; content: string }>(`${base(serverId)}/content?${new URLSearchParams({ path })}`)
}

export function writeText(serverId: string, path: string, content: string) {
  return api<void>(`${base(serverId)}/content`, { method: 'PUT', body: JSON.stringify({ path, content }) })
}

export function downloadUrl(serverId: string, directory: string, names: string[]) {
  const params = new URLSearchParams()
  if (names.length === 1) {
    params.set('path', joinPath(directory, names[0]))
  } else {
    params.set('path', directory)
    names.forEach((n) => params.append('names', n))
  }
  return `/api${base(serverId)}/download?${params}`
}

/** XMLHttpRequest instead of fetch: fetch can't report upload progress. */
export function uploadFiles(
  serverId: string,
  directory: string,
  items: UploadItem[],
  onProgress: (loaded: number, total: number) => void,
): Promise<void> {
  const form = new FormData()
  form.append('directory', directory)
  for (const item of items) {
    form.append('paths', item.path)
    form.append('files', item.file, baseName(item.path))
  }
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `/api${base(serverId)}/upload`)
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded, e.total)
    xhr.onload = () => {
      if (xhr.status < 300) return resolve()
      let message = `upload failed (${xhr.status})`
      try {
        const detail = JSON.parse(xhr.responseText).detail
        if (typeof detail === 'string') message = detail
      } catch {
        // not JSON: keep the generic message
      }
      reject(new ApiError(xhr.status, message))
    }
    xhr.onerror = () => reject(new ApiError(0, 'upload failed: network error'))
    xhr.send(form)
  })
}
