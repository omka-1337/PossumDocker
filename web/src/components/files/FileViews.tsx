import { IconArrowDown, IconArrowUp } from '@tabler/icons-react'
import { useEffect, useRef, useState, type HTMLAttributes } from 'react'
import type { FileEntry } from '../../api/files'
import { formatDate, formatSize } from '../../lib/format'
import { FileIcon } from './FileIcon'

export type SortKey = 'name' | 'size' | 'mtime'
export interface Sort {
  key: SortKey
  dir: 1 | -1
}

export interface ViewProps {
  entries: FileEntry[]
  itemProps: (entry: FileEntry) => HTMLAttributes<HTMLElement> & { 'data-name': string }
  isSelected: (name: string) => boolean
  isCut: (name: string) => boolean
  dropTarget: string | null
  renaming: string | null
  onRename: (entry: FileEntry, newName: string | null) => void
}

function itemClass(selected: boolean, dropping: boolean, cut: boolean) {
  return [
    'transition-colors',
    dropping ? 'bg-sky-500/30 ring-2 ring-sky-400' : selected ? 'bg-sky-500/20 ring-1 ring-sky-400/50' : 'hover:bg-zinc-50/5',
    cut ? 'opacity-50' : '',
  ].join(' ')
}

export function IconsView({ entries, itemProps, isSelected, isCut, dropTarget, renaming, onRename }: ViewProps) {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(6.5rem,1fr))] gap-1 p-2">
      {entries.map((entry) => (
        <div
          key={entry.name}
          {...itemProps(entry)}
          className={`flex flex-col items-center gap-1 rounded-lg px-1 py-2 ${itemClass(isSelected(entry.name), dropTarget === entry.name, isCut(entry.name))}`}
          title={entry.name}
        >
          <FileIcon entry={entry} size={44} />
          {renaming === entry.name ? (
            <RenameInput entry={entry} onDone={(name) => onRename(entry, name)} className="w-full text-center" />
          ) : (
            <span className="line-clamp-2 w-full text-center text-xs break-all">{entry.name}</span>
          )}
        </div>
      ))}
    </div>
  )
}

export function DetailsView({
  entries,
  itemProps,
  isSelected,
  isCut,
  dropTarget,
  renaming,
  onRename,
  sort,
  onSort,
}: ViewProps & { sort: Sort; onSort: (key: SortKey) => void }) {
  const header = (key: SortKey, label: string, className = '') => (
    <button onClick={() => onSort(key)} className={`flex items-center gap-1 hover:text-zinc-200 ${className}`}>
      {label}
      {sort.key === key && (sort.dir === 1 ? <IconArrowUp size={12} /> : <IconArrowDown size={12} />)}
    </button>
  )
  return (
    <div className="text-sm">
      <div className="sticky top-0 z-10 grid grid-cols-[1fr_6rem] gap-3 border-b border-line-soft bg-frame px-3 py-1.5 text-xs text-muted sm:grid-cols-[1fr_6rem_11rem]">
        {header('name', 'name')}
        {header('size', 'size', 'justify-end')}
        {header('mtime', 'modified', 'hidden sm:flex')}
      </div>
      <div className="p-1">
        {entries.map((entry) => (
          <div
            key={entry.name}
            {...itemProps(entry)}
            className={`grid grid-cols-[1fr_6rem] items-center gap-3 rounded-md px-2 py-1 sm:grid-cols-[1fr_6rem_11rem] ${itemClass(isSelected(entry.name), dropTarget === entry.name, isCut(entry.name))}`}
          >
            <span className="flex min-w-0 items-center gap-2">
              <FileIcon entry={entry} size={18} />
              {renaming === entry.name ? (
                <RenameInput entry={entry} onDone={(name) => onRename(entry, name)} className="flex-1" />
              ) : (
                <span className="truncate">{entry.name}</span>
              )}
            </span>
            <span className="text-right text-xs text-muted">{entry.type === 'dir' ? '—' : formatSize(entry.size)}</span>
            <span className="hidden text-xs text-muted sm:block">{formatDate(entry.mtime)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function RenameInput({
  entry,
  onDone,
  className = '',
}: {
  entry: FileEntry
  onDone: (name: string | null) => void
  className?: string
}) {
  const ref = useRef<HTMLInputElement>(null)
  const [value, setValue] = useState(entry.name)
  const done = useRef(false)

  useEffect(() => {
    const input = ref.current!
    input.focus()
    // Like Dolphin: select the name without its extension.
    const dot = entry.type === 'dir' ? -1 : entry.name.lastIndexOf('.')
    input.setSelectionRange(0, dot > 0 ? dot : entry.name.length)
  }, [entry])

  const finish = (name: string | null) => {
    if (done.current) return
    done.current = true
    onDone(name && name !== entry.name ? name : null)
  }

  return (
    <input
      ref={ref}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => finish(value.trim())}
      onKeyDown={(e) => {
        e.stopPropagation() // Delete/Ctrl+A here edit the text, not the file list
        if (e.key === 'Enter') finish(value.trim())
        if (e.key === 'Escape') finish(null)
      }}
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
      className={`rounded border border-sky-400 bg-page px-1 text-xs outline-none ${className}`}
    />
  )
}
