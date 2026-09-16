import {
  IconArrowLeft,
  IconArrowRight,
  IconArrowUp,
  IconChevronRight,
  IconClipboard,
  IconCopy,
  IconDownload,
  IconEdit,
  IconEye,
  IconEyeOff,
  IconFileZip,
  IconFolderOpen,
  IconFolderPlus,
  IconLayoutGrid,
  IconList,
  IconPencil,
  IconRefresh,
  IconScissors,
  IconSearch,
  IconX,
  IconSelectAll,
  IconServer2,
  IconTrash,
  IconUpload,
} from '@tabler/icons-react'
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
} from 'react'
import {
  baseName,
  downloadUrl,
  extension,
  joinPath,
  parentPath,
  uploadFiles,
  useFileActions,
  useFileSearch,
  useListing,
  type FileEntry,
  type SearchMatch,
  type SearchResults,
  type UploadItem,
} from '../../api/files'
import { useStorage } from '../../api/queries'
import { collectDropped } from '../../lib/dropped'
import { formatSize } from '../../lib/format'
import { useStoredState } from '../../lib/storage'
import { Button, IconButton, Modal } from '../ui'
import { ContextMenu, type MenuItem } from './ContextMenu'
import { FileIcon } from './FileIcon'
import { DetailsView, IconsView, type Sort, type SortKey } from './FileViews'
import { TextEditor } from './TextEditor'

const DRAG_TYPE = 'application/x-possum-files'
const TEXT_EXTENSIONS = new Set([
  'txt', 'log', 'json', 'json5', 'yml', 'yaml', 'toml', 'properties', 'cfg', 'conf', 'ini', 'xml',
  'md', 'sh', 'bat', 'csv', 'js', 'lua', 'sk', 'secret', 'env', 'list', 'mcmeta', 'txt_',
])  // prettier-ignore

const mod = (e: KeyboardEvent) => e.ctrlKey || e.metaKey

interface Clipboard {
  mode: 'copy' | 'cut'
  dir: string
  names: string[]
}

interface UploadState {
  id: number
  label: string
  loaded: number
  total: number
  error?: string
}

interface Band {
  x0: number
  y0: number
  x1: number
  y1: number
}

export function FileBrowser({ serverId }: { serverId: string }) {
  const [path, setPath] = useState('')
  const [back, setBack] = useState<string[]>([])
  const [forward, setForward] = useState<string[]>([])
  const { data: listing, isFetching, error: listError } = useListing(serverId, path)
  const actions = useFileActions(serverId)

  const [selected, setSelected] = useState<Set<string>>(new Set())
  const anchor = useRef<string | null>(null)
  const [view, setView] = useStoredState<'icons' | 'details'>('possum.files.view', 'icons')
  const [showHidden, setShowHidden] = useStoredState('possum.files.hidden', false)
  const [sort, setSort] = useState<Sort>({ key: 'name', dir: 1 })
  const [renaming, setRenaming] = useState<string | null>(null)
  const [clipboard, setClipboard] = useState<Clipboard | null>(null)
  const [menu, setMenu] = useState<{ x: number; y: number; target: FileEntry | null } | null>(null)
  const [dialog, setDialog] = useState<'mkdir' | 'delete' | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [dropTarget, setDropTarget] = useState<string | null>(null)
  const [uploads, setUploads] = useState<UploadState[]>([])
  const [actionError, setActionError] = useState<string | null>(null)
  const [band, setBand] = useState<Band | null>(null)

  const areaRef = useRef<HTMLDivElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const folderInput = useRef<HTMLInputElement>(null)
  // What is being dragged inside the browser; null for drags coming from the desktop.
  const dragging = useRef<{ dir: string; names: string[] } | null>(null)
  const uploadSeq = useRef(0)

  // Search bar, like Dolphin's: names under this folder ("here") or anywhere on the server.
  const [searchOpen, setSearchOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [scope, setScope] = useState<'here' | 'everywhere'>('here')
  const [searching, setSearching] = useState<string | null>(null)
  const searchInput = useRef<HTMLInputElement>(null)
  const search = useFileSearch(serverId, scope === 'here' ? path : '', searching)

  // Results follow the typing, once it pauses.
  useEffect(() => {
    const text = query.trim()
    const timer = setTimeout(() => setSearching(text.length >= 2 ? text : null), 350)
    return () => clearTimeout(timer)
  }, [query])

  const openSearch = () => {
    setSearchOpen(true)
    setTimeout(() => searchInput.current?.focus())
  }
  const closeSearch = () => {
    setSearchOpen(false)
    setQuery('')
    setSearching(null)
    areaRef.current?.focus()
  }

  const entries = useMemo(() => {
    const visible = (listing?.entries ?? []).filter((e) => showHidden || !e.name.startsWith('.'))
    const value = (e: FileEntry) => (sort.key === 'name' ? e.name.toLowerCase() : sort.key === 'size' ? e.size : e.mtime)
    return visible.sort((a, b) => {
      if ((a.type === 'dir') !== (b.type === 'dir')) return a.type === 'dir' ? -1 : 1 // folders first, always
      const [x, y] = [value(a), value(b)]
      return (x < y ? -1 : x > y ? 1 : 0) * sort.dir
    })
  }, [listing, showHidden, sort])

  const selectedEntries = entries.filter((e) => selected.has(e.name))
  const fail = (e: Error) => setActionError(e.message)

  // --- navigation --------------------------------------------------------------

  const navigate = useCallback(
    (to: string) => {
      if (to === path) return
      setBack((b) => [...b, path])
      setForward([])
      setPath(to)
      setSelected(new Set())
      setRenaming(null)
    },
    [path],
  )

  const goBack = () => {
    const previous = back.at(-1)
    if (previous === undefined) return
    setBack((b) => b.slice(0, -1))
    setForward((f) => [path, ...f])
    setPath(previous)
    setSelected(new Set())
  }

  const goForward = () => {
    const next = forward[0]
    if (next === undefined) return
    setForward((f) => f.slice(1))
    setBack((b) => [...b, path])
    setPath(next)
    setSelected(new Set())
  }

  // --- actions -----------------------------------------------------------------

  const open = (entry: FileEntry) => {
    if (entry.type === 'dir') return navigate(joinPath(path, entry.name))
    const ext = extension(entry.name)
    if (TEXT_EXTENSIONS.has(ext) || (!ext && entry.size < 512 * 1024)) return setEditing(joinPath(path, entry.name))
    download([entry.name])
  }

  const download = (names: string[]) => {
    const link = document.createElement('a')
    link.href = downloadUrl(serverId, path, names)
    link.click()
  }

  const paste = (destination = path) => {
    if (!clipboard) return
    const sources = clipboard.names.map((n) => joinPath(clipboard.dir, n))
    const action = clipboard.mode === 'cut' ? actions.move : actions.copy
    action.mutate(
      { sources, destination },
      { onSuccess: () => clipboard.mode === 'cut' && setClipboard(null), onError: fail },
    )
  }

  const startUpload = (directory: string, items: UploadItem[]) => {
    if (items.length === 0) return
    const id = ++uploadSeq.current
    const total = items.reduce((sum, i) => sum + i.file.size, 0)
    const label = items.length === 1 ? items[0].path : `${items.length} files`
    setUploads((u) => [...u, { id, label, loaded: 0, total }])
    uploadFiles(serverId, directory, items, (loaded, all) =>
      setUploads((u) => u.map((x) => (x.id === id ? { ...x, loaded, total: all } : x))),
    )
      .then(() => {
        setUploads((u) => u.filter((x) => x.id !== id))
        actions.refresh()
      })
      .catch((e: Error) => setUploads((u) => u.map((x) => (x.id === id ? { ...x, error: e.message } : x))))
  }

  const commitRename = (entry: FileEntry, name: string | null) => {
    setRenaming(null)
    if (!name) return
    actions.rename.mutate(
      { path: joinPath(path, entry.name), name },
      { onSuccess: () => setSelected(new Set([name])), onError: fail },
    )
  }

  // --- selection ---------------------------------------------------------------

  const clickItem = (e: MouseEvent, entry: FileEntry) => {
    e.stopPropagation()
    areaRef.current?.focus()
    const name = entry.name
    if (e.shiftKey && anchor.current) {
      const names = entries.map((x) => x.name)
      const [a, b] = [names.indexOf(anchor.current), names.indexOf(name)].sort((x, y) => x - y)
      const range = names.slice(a, b + 1)
      setSelected((s) => new Set(e.ctrlKey || e.metaKey ? [...s, ...range] : range))
      return
    }
    anchor.current = name
    if (e.ctrlKey || e.metaKey) {
      setSelected((s) => {
        const next = new Set(s)
        if (next.has(name)) next.delete(name)
        else next.add(name)
        return next
      })
    } else {
      setSelected(new Set([name]))
    }
  }

  // Rubber band: drag on empty space to select everything the rectangle touches.
  const startBand = (e: MouseEvent<HTMLDivElement>) => {
    if (e.button !== 0 || (e.target as HTMLElement).closest('[data-name]')) return
    const area = areaRef.current!
    area.focus()
    const rect = area.getBoundingClientRect()
    const point = (ev: { clientX: number; clientY: number }) => ({
      x: ev.clientX - rect.left + area.scrollLeft,
      y: ev.clientY - rect.top + area.scrollTop,
    })
    const start = point(e)
    const initial = e.ctrlKey || e.metaKey ? new Set(selected) : new Set<string>()
    setSelected(initial)
    let moved = false

    const onMove = (ev: globalThis.MouseEvent) => {
      const p = point(ev)
      if (!moved && Math.hypot(p.x - start.x, p.y - start.y) < 4) return
      moved = true
      const next: Band = { x0: start.x, y0: start.y, x1: p.x, y1: p.y }
      setBand(next)
      const [left, right] = [Math.min(next.x0, next.x1), Math.max(next.x0, next.x1)]
      const [top, bottom] = [Math.min(next.y0, next.y1), Math.max(next.y0, next.y1)]
      const hit = new Set(initial)
      area.querySelectorAll<HTMLElement>('[data-name]').forEach((el) => {
        const r = el.getBoundingClientRect()
        const [l, t] = [r.left - rect.left + area.scrollLeft, r.top - rect.top + area.scrollTop]
        if (l < right && l + r.width > left && t < bottom && t + r.height > top) hit.add(el.dataset.name!)
      })
      setSelected(hit)
    }
    const onUp = () => {
      setBand(null)
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  // --- drag & drop -------------------------------------------------------------

  const canDrop = (e: DragEvent, destination: string) => {
    const types = e.dataTransfer.types
    if (types.includes('Files')) return true // from the desktop
    const drag = dragging.current
    if (!drag || !types.includes(DRAG_TYPE)) return false
    // Not into the folder it already is in, and not into itself or its children.
    if (drag.dir === destination) return false
    return !drag.names.some((n) => {
      const source = joinPath(drag.dir, n)
      return destination === source || destination.startsWith(`${source}/`)
    })
  }

  const dropProps = (destination: string, key: string) => ({
    onDragOver: (e: DragEvent) => {
      if (!canDrop(e, destination)) {
        // Bubbled up from something that isn't a target (a file): drop the stale highlight.
        if (key === '') setDropTarget(null)
        return
      }
      e.preventDefault()
      e.stopPropagation()
      e.dataTransfer.dropEffect = dragging.current && !(e.ctrlKey || e.altKey) ? 'move' : 'copy'
      setDropTarget(key)
    },
    onDrop: (e: DragEvent) => {
      if (!canDrop(e, destination)) return
      e.preventDefault()
      e.stopPropagation()
      setDropTarget(null)
      const drag = dragging.current
      if (drag) {
        const sources = drag.names.map((n) => joinPath(drag.dir, n))
        // Hold Ctrl while dropping to copy instead of move, like in Dolphin.
        const action = e.ctrlKey || e.altKey ? actions.copy : actions.move
        action.mutate({ sources, destination }, { onError: fail })
      } else {
        collectDropped(e.dataTransfer).then((items) => startUpload(destination, items), fail)
      }
    },
  })

  const itemProps = (entry: FileEntry) => ({
    'data-name': entry.name,
    draggable: renaming !== entry.name,
    onClick: (e: MouseEvent) => clickItem(e, entry),
    onDoubleClick: () => open(entry),
    onContextMenu: (e: MouseEvent) => {
      e.preventDefault()
      e.stopPropagation()
      if (!selected.has(entry.name)) setSelected(new Set([entry.name]))
      setMenu({ x: e.clientX, y: e.clientY, target: entry })
    },
    onDragStart: (e: DragEvent) => {
      const names = selected.has(entry.name) ? [...selected] : [entry.name]
      if (!selected.has(entry.name)) setSelected(new Set([entry.name]))
      dragging.current = { dir: path, names }
      e.dataTransfer.setData(DRAG_TYPE, JSON.stringify(dragging.current))
      e.dataTransfer.effectAllowed = 'copyMove'
    },
    onDragEnd: () => {
      dragging.current = null
      setDropTarget(null)
    },
    ...(entry.type === 'dir' ? dropProps(joinPath(path, entry.name), entry.name) : {}),
  })

  // --- keyboard ----------------------------------------------------------------

  // Dolphin's shortcuts. Each returns true when it handled the key.
  const shortcuts: { when: (e: KeyboardEvent) => boolean; run: () => void }[] = [
    { when: (e) => mod(e) && e.key === 'a', run: () => setSelected(new Set(entries.map((x) => x.name))) },
    { when: (e) => mod(e) && e.key === 'c' && selected.size > 0, run: () => setClipboard({ mode: 'copy', dir: path, names: [...selected] }) },
    { when: (e) => mod(e) && e.key === 'x' && selected.size > 0, run: () => setClipboard({ mode: 'cut', dir: path, names: [...selected] }) },
    { when: (e) => mod(e) && e.key === 'v', run: () => paste() },
    { when: (e) => mod(e) && e.key === 'h', run: () => setShowHidden(!showHidden) },
    { when: (e) => mod(e) && e.key === 'f', run: openSearch },
    { when: (e) => e.key === 'Delete' && selected.size > 0, run: () => setDialog('delete') },
    { when: (e) => e.key === 'F2' && selected.size === 1, run: () => setRenaming([...selected][0]) },
    { when: (e) => e.key === 'F5', run: () => actions.refresh() },
    { when: (e) => e.key === 'Enter' && selectedEntries.length === 1, run: () => open(selectedEntries[0]) },
    { when: (e) => e.key === 'Escape', run: () => setSelected(new Set()) },
    { when: (e) => (e.key === 'Backspace' || (e.altKey && e.key === 'ArrowUp')) && path !== '', run: () => navigate(parentPath(path)) },
    { when: (e) => e.altKey && e.key === 'ArrowLeft', run: goBack },
    { when: (e) => e.altKey && e.key === 'ArrowRight', run: goForward },
  ]  // prettier-ignore

  const onKeyDown = (e: KeyboardEvent) => {
    if ((e.target as HTMLElement).tagName === 'INPUT') return
    const shortcut = shortcuts.find((s) => s.when(e))
    if (shortcut) {
      e.preventDefault()
      shortcut.run()
    }
  }

  // --- menus -------------------------------------------------------------------

  const menuItems = (target: FileEntry | null): MenuItem[] => {
    if (!target) {
      return [
        { label: 'new folder', icon: <IconFolderPlus size={16} />, onSelect: () => setDialog('mkdir') },
        { label: 'upload files', icon: <IconUpload size={16} />, onSelect: () => fileInput.current?.click() },
        { label: 'upload folder', icon: <IconUpload size={16} />, onSelect: () => folderInput.current?.click() },
        'separator',
        { label: 'paste', icon: <IconClipboard size={16} />, shortcut: 'Ctrl+V', disabled: !clipboard, onSelect: () => paste() },
        { label: 'select all', icon: <IconSelectAll size={16} />, shortcut: 'Ctrl+A', onSelect: () => setSelected(new Set(entries.map((x) => x.name))) },
        { label: showHidden ? 'hide hidden files' : 'show hidden files', icon: showHidden ? <IconEyeOff size={16} /> : <IconEye size={16} />, shortcut: 'Ctrl+H', onSelect: () => setShowHidden(!showHidden) },
        { label: 'refresh', icon: <IconRefresh size={16} />, shortcut: 'F5', onSelect: () => actions.refresh() },
      ]  // prettier-ignore
    }
    const names = selected.has(target.name) ? [...selected] : [target.name]
    const single = names.length === 1
    const items: MenuItem[] = []
    if (single) {
      items.push({ label: 'open', icon: <IconFolderOpen size={16} />, shortcut: 'Enter', onSelect: () => open(target) })
      if (target.type === 'file') {
        items.push({ label: 'edit as text', icon: <IconEdit size={16} />, onSelect: () => setEditing(joinPath(path, target.name)) })
      }
    }
    items.push({ label: 'download', icon: <IconDownload size={16} />, onSelect: () => download(names) })
    if (single && extension(target.name) === 'zip') {
      items.push({ label: 'extract here', icon: <IconFileZip size={16} />, onSelect: () => actions.extract.mutate(joinPath(path, target.name), { onError: fail }) })
    }
    items.push(
      'separator',
      { label: 'cut', icon: <IconScissors size={16} />, shortcut: 'Ctrl+X', onSelect: () => setClipboard({ mode: 'cut', dir: path, names }) },
      { label: 'copy', icon: <IconCopy size={16} />, shortcut: 'Ctrl+C', onSelect: () => setClipboard({ mode: 'copy', dir: path, names }) },
    )  // prettier-ignore
    if (single && target.type === 'dir' && clipboard) {
      items.push({ label: 'paste into folder', icon: <IconClipboard size={16} />, onSelect: () => paste(joinPath(path, target.name)) })
    }
    if (single) items.push({ label: 'rename', icon: <IconPencil size={16} />, shortcut: 'F2', onSelect: () => setRenaming(target.name) })
    items.push('separator', { label: 'delete', icon: <IconTrash size={16} />, shortcut: 'Del', danger: true, onSelect: () => setDialog('delete') })
    return items
  }

  // --- render ------------------------------------------------------------------

  const crumbs = path ? path.split('/') : []
  const folders = entries.filter((e) => e.type === 'dir').length
  const filesSize = entries.reduce((sum, e) => sum + (e.type === 'dir' ? 0 : e.size), 0)
  const selectedSize = selectedEntries.reduce((sum, e) => sum + (e.type === 'dir' ? 0 : e.size), 0)
  const isCut = (name: string) => clipboard?.mode === 'cut' && clipboard.dir === path && clipboard.names.includes(name)

  const viewProps = {
    entries,
    itemProps,
    isSelected: (name: string) => selected.has(name),
    isCut,
    dropTarget,
    renaming,
    onRename: commitRename,
  }

  return (
    <div className="mb-6 overflow-hidden rounded-xl border border-line-soft bg-frame">
      {/* toolbar */}
      <div className="flex flex-wrap items-center gap-1 border-b border-line-soft p-1.5">
        <IconButton onClick={goBack} disabled={!back.length} aria-label="back" className="disabled:opacity-30">
          <IconArrowLeft size={18} />
        </IconButton>
        <IconButton onClick={goForward} disabled={!forward.length} aria-label="forward" className="disabled:opacity-30">
          <IconArrowRight size={18} />
        </IconButton>
        <IconButton onClick={() => navigate(parentPath(path))} disabled={!path} aria-label="up" className="disabled:opacity-30">
          <IconArrowUp size={18} />
        </IconButton>

        <nav className="flex min-w-0 flex-1 items-center overflow-x-auto rounded-lg bg-page px-1 py-0.5 text-sm">
          <Crumb label={<IconServer2 size={15} />} active={dropTarget === 'crumb:'} onClick={() => navigate('')} {...dropProps('', 'crumb:')} />
          {crumbs.map((part, i) => {
            const to = crumbs.slice(0, i + 1).join('/')
            return (
              <span key={to} className="flex shrink-0 items-center">
                <IconChevronRight size={14} className="text-muted" />
                <Crumb label={part} active={dropTarget === `crumb:${to}`} onClick={() => navigate(to)} {...dropProps(to, `crumb:${to}`)} />
              </span>
            )
          })}
        </nav>

        <IconButton
          onClick={() => (searchOpen ? closeSearch() : openSearch())}
          aria-label="search"
          title="search (Ctrl+F)"
          aria-pressed={searchOpen}
          className={searchOpen ? 'bg-raised' : ''}
        >
          <IconSearch size={18} />
        </IconButton>
        <IconButton onClick={() => setDialog('mkdir')} aria-label="new folder" title="new folder">
          <IconFolderPlus size={18} />
        </IconButton>
        <IconButton onClick={() => fileInput.current?.click()} aria-label="upload files" title="upload files">
          <IconUpload size={18} />
        </IconButton>
        <IconButton onClick={() => setView(view === 'icons' ? 'details' : 'icons')} aria-label="switch view" title={view === 'icons' ? 'details view' : 'icons view'}>
          {view === 'icons' ? <IconList size={18} /> : <IconLayoutGrid size={18} />}
        </IconButton>
      </div>

      {searchOpen && (
        <div className="space-y-2 border-b border-line-soft p-1.5">
          <div className="flex items-center gap-1">
            <input
              ref={searchInput}
              aria-label="search files"
              placeholder="search…"
              className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-page px-3 text-sm outline-none placeholder:text-muted focus:border-zinc-400"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && query.trim().length >= 2) setSearching(query.trim())
                if (e.key === 'Escape') closeSearch()
              }}
            />
            <IconButton onClick={closeSearch} aria-label="close search" title="close search">
              <IconX size={18} />
            </IconButton>
          </div>
          <div className="flex gap-1 text-sm">
            {(['here', 'everywhere'] as const).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={scope === value}
                onClick={() => setScope(value)}
                className={`rounded-lg px-3 py-1 transition ${scope === value ? 'bg-raised text-zinc-100' : 'text-muted hover:bg-panel'}`}
              >
                {value}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* files */}
      <div
        ref={areaRef}
        tabIndex={0}
        onKeyDown={onKeyDown}
        onMouseDown={startBand}
        onContextMenu={(e) => {
          e.preventDefault()
          setSelected(new Set())
          setMenu({ x: e.clientX, y: e.clientY, target: null })
        }}
        onDragLeave={(e) => {
          if (!areaRef.current?.contains(e.relatedTarget as Node)) setDropTarget(null)
        }}
        {...dropProps(path, '')}
        className={`relative h-[28rem] overflow-auto outline-none select-none ${dropTarget === '' ? 'bg-sky-500/10 ring-2 ring-sky-400/60 ring-inset' : ''}`}
      >
        {listError ? (
          // e.g. the folder we're in was deleted or renamed meanwhile
          <div className="p-4 text-sm">
            <p className="mb-3 text-red-400">{listError.message}</p>
            <Button onClick={() => navigate(path ? parentPath(path) : '')}>
              <IconArrowUp size={16} /> go to the parent folder
            </Button>
          </div>
        ) : searchOpen && searching !== null ? (
          <SearchResultsList
            query={searching}
            results={search.data}
            loading={search.isFetching}
            error={search.error}
            onPick={(match) => {
              closeSearch()
              navigate(parentPath(match.path))
              setSelected(new Set([baseName(match.path)]))
            }}
          />
        ) : entries.length === 0 && listing ? (
          <div className="grid h-full place-items-center text-center text-sm text-muted">
            <div>
              <p>this folder is empty</p>
              <p className="mt-1 text-xs">drop files here to upload them</p>
            </div>
          </div>
        ) : view === 'icons' ? (
          <IconsView {...viewProps} />
        ) : (
          <DetailsView {...viewProps} sort={sort} onSort={(key: SortKey) => setSort((s) => ({ key, dir: s.key === key ? (-s.dir as 1 | -1) : 1 }))} />
        )}
        {band && (
          <div
            className="pointer-events-none absolute border border-sky-400 bg-sky-400/15"
            style={{
              left: Math.min(band.x0, band.x1),
              top: Math.min(band.y0, band.y1),
              width: Math.abs(band.x1 - band.x0),
              height: Math.abs(band.y1 - band.y0),
            }}
          />
        )}
      </div>

      {/* status bar */}
      <div className="flex items-center gap-3 border-t border-line-soft px-3 py-1.5 text-xs text-muted">
        <span className="flex-1 truncate">
          {selected.size > 0
            ? `${selected.size} selected${selectedSize ? ` (${formatSize(selectedSize)})` : ''}`
            : `${folders} folders, ${entries.length - folders} files (${formatSize(filesSize)})`}
        </span>
        {clipboard && (
          <span className="truncate">
            {clipboard.names.length} {clipboard.mode === 'cut' ? 'to move' : 'to copy'} ·{' '}
            <button className="underline hover:text-zinc-200" onClick={() => setClipboard(null)}>
              clear
            </button>
          </span>
        )}
        {isFetching && <span>loading…</span>}
        <StorageUsed serverId={serverId} />
      </div>

      {(uploads.length > 0 || actionError) && (
        <div className="space-y-1.5 border-t border-line-soft p-2">
          {actionError && (
            <div className="flex items-center gap-2 rounded-lg bg-red-950/50 px-3 py-1.5 text-xs text-red-300">
              <span className="flex-1">{actionError}</span>
              <button onClick={() => setActionError(null)} className="underline">
                dismiss
              </button>
            </div>
          )}
          {uploads.map((u) => (
            <div key={u.id} className="rounded-lg bg-panel px-3 py-1.5 text-xs">
              <div className="mb-1 flex gap-2">
                <span className="flex-1 truncate">{u.label}</span>
                {u.error ? (
                  <button className="text-red-300 underline" onClick={() => setUploads((all) => all.filter((x) => x.id !== u.id))}>
                    {u.error} · dismiss
                  </button>
                ) : (
                  <span className="text-muted">
                    {formatSize(u.loaded)} / {formatSize(u.total)}
                  </span>
                )}
              </div>
              <div className="h-1 overflow-hidden rounded-full bg-raised">
                <div
                  className={`h-full transition-all ${u.error ? 'bg-red-500' : 'bg-sky-400'}`}
                  style={{ width: `${u.total ? (u.loaded / u.total) * 100 : 0}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}

      <input
        ref={fileInput}
        type="file"
        multiple
        hidden
        onChange={(e) => {
          startUpload(path, Array.from(e.target.files ?? []).map((file) => ({ path: file.name, file })))
          e.target.value = ''
        }}
      />
      <input
        ref={folderInput}
        type="file"
        hidden
        // Non-standard but supported everywhere: pick a folder, get its files with relative paths.
        {...{ webkitdirectory: '' }}
        onChange={(e) => {
          startUpload(path, Array.from(e.target.files ?? []).map((file) => ({ path: file.webkitRelativePath, file })))
          e.target.value = ''
        }}
      />

      {menu && <ContextMenu x={menu.x} y={menu.y} items={menuItems(menu.target)} onClose={() => setMenu(null)} />}
      {editing && <TextEditor serverId={serverId} path={editing} onClose={() => setEditing(null)} />}
      {dialog === 'mkdir' && (
        <NewFolderDialog
          onClose={() => setDialog(null)}
          onCreate={(name) =>
            actions.mkdir.mutate(joinPath(path, name), {
              onSuccess: () => {
                setDialog(null)
                setSelected(new Set([name]))
              },
              onError: fail,
            })
          }
        />
      )}
      {dialog === 'delete' && (
        <Modal title={`delete ${selected.size === 1 ? `"${[...selected][0]}"` : `${selected.size} items`}?`} onClose={() => setDialog(null)}>
          <p className="mb-5 text-sm text-muted">there is no trash: deleted files are gone for good.</p>
          <div className="flex gap-2">
            <Button className="flex-1" onClick={() => setDialog(null)}>
              cancel
            </Button>
            <Button
              variant="danger"
              className="flex-1"
              autoFocus
              onClick={() => {
                actions.remove.mutate([...selected].map((n) => joinPath(path, n)), { onError: fail })
                setSelected(new Set())
                setDialog(null)
                areaRef.current?.focus()
              }}
            >
              delete
            </Button>
          </div>
        </Modal>
      )}
    </div>
  )
}

function Crumb({
  label,
  active,
  onClick,
  ...drop
}: {
  label: ReactNode
  active: boolean
  onClick: () => void
  onDragOver: (e: DragEvent) => void
  onDrop: (e: DragEvent) => void
}) {
  return (
    <button
      onClick={onClick}
      {...drop}
      className={`shrink-0 rounded-md px-2 py-1 transition hover:bg-raised ${active ? 'bg-sky-500/30 ring-1 ring-sky-400' : ''}`}
    >
      {label}
    </button>
  )
}

function NewFolderDialog({ onClose, onCreate }: { onClose: () => void; onCreate: (name: string) => void }) {
  const [name, setName] = useState('')
  const valid = name.trim() && !name.includes('/') && name !== '.' && name !== '..'
  return (
    <Modal title="new folder" onClose={onClose}>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (valid) onCreate(name.trim())
        }}
      >
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="folder name"
          className="mb-4 w-full rounded-xl border-[1.5px] border-line bg-page px-3 py-2.5 text-sm outline-none focus:border-zinc-400"
        />
        <Button type="submit" variant="primary" className="w-full" disabled={!valid}>
          create
        </Button>
      </form>
    </Modal>
  )
}

/** "1.2 GiB of 10 GiB": a server with a disk limit shows how close it is. */
function StorageUsed({ serverId }: { serverId: string }) {
  const { data } = useStorage(serverId)
  if (!data || data.disk_used === null || data.disk_limit === null) return null
  const share = data.disk_used / data.disk_limit
  return (
    <span className={`shrink-0 ${share >= 0.9 ? 'text-amber-300' : ''}`} title="disk space of this server">
      {formatSize(data.disk_used)} of {formatSize(data.disk_limit)}
    </span>
  )
}

/** Everything under the folder whose name matches; a click opens the folder it's in, with it selected. */
function SearchResultsList({
  query,
  results,
  loading,
  error,
  onPick,
}: {
  query: string
  results: SearchResults | undefined
  loading: boolean
  error: Error | null
  onPick: (match: SearchMatch) => void
}) {
  if (error) return <p className="p-4 text-sm text-red-400">{error.message}</p>
  if (!results) return <p className="p-4 text-sm text-muted">searching for &quot;{query}&quot;…</p>
  if (results.matches.length === 0) {
    return (
      <div className="grid h-full place-items-center text-sm text-muted">nothing named like &quot;{query}&quot; here</div>
    )
  }
  return (
    <div className="p-1.5 text-sm">
      <p className="px-2 pt-1 pb-2 text-xs text-muted">
        {results.matches.length} found{results.truncated && ', showing the first ones: narrow the search'}
        {loading && ' · updating…'}
      </p>
      <ul>
        {results.matches.map((match) => {
          const name = baseName(match.path)
          const folder = parentPath(match.path)
          return (
            <li key={match.path}>
              <button
                type="button"
                onClick={() => onPick(match)}
                className="flex w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left hover:bg-zinc-50/5"
              >
                <FileIcon entry={{ name, type: match.type, size: match.size, mtime: match.mtime }} size={20} />
                <span className="min-w-0 flex-1 truncate">{name}</span>
                <span className="max-w-[50%] shrink truncate text-xs text-muted">{folder || '/'}</span>
                {match.type !== 'dir' && <span className="w-16 shrink-0 text-right text-xs text-muted">{formatSize(match.size)}</span>}
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
