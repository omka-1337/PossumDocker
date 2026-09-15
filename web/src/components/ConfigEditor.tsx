import { IconLock, IconSearch } from '@tabler/icons-react'
import { useState } from 'react'
import { ApiError } from '../api/client'
import { useConfig, useUpdateConfig } from '../api/queries'
import type { ConfigEntry, Server } from '../api/types'
import { FieldError, inputClass, Select } from './FieldInput'
import { Button, Segmented, Switch } from './ui'

const SEGMENTED_MAX = 4

interface Props {
  server: Server
  configId: string
  onRestartNeeded: () => void
}

export function ConfigEditor({ server, configId, onRestartNeeded }: Props) {
  const { data: config, isPending, isError, error } = useConfig(server.id, configId)
  // Only the keys the user changed; sent as a partial update.
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [search, setSearch] = useState('')
  const update = useUpdateConfig(server.id, configId)

  if (isPending) return <p className="text-sm text-muted">loading…</p>
  if (isError) return <p className="text-sm text-red-400">{error.message}</p>

  if (!config.exists) {
    return (
      <div className="rounded-xl border border-dashed border-line p-6 text-center text-sm text-muted">
        <p className="text-zinc-200">{config.path} doesn't exist yet</p>
        <p className="mt-1">the game creates it on its first start. start the server once, then edit it here.</p>
      </div>
    )
  }

  const fieldErrors = update.error instanceof ApiError ? update.error.fieldErrors : {}
  const changed = Object.keys(draft).length
  const running = server.status === 'running' || server.status === 'starting'

  const query = search.trim().toLowerCase()
  const matches = (e: ConfigEntry) =>
    !query || e.key.toLowerCase().includes(query) || e.hint?.label.toLowerCase().includes(query)
  const known = config.entries.filter((e) => e.hint && !e.managed && matches(e))
  const other = config.entries.filter((e) => (!e.hint || e.managed) && matches(e))

  const setValue = (entry: ConfigEntry, value: string) =>
    setDraft((prev) => {
      const next = { ...prev }
      // Back to the saved value → not a change any more.
      if (value === entry.value) delete next[entry.key]
      else next[entry.key] = value
      return next
    })

  const save = () =>
    update.mutate(draft, {
      onSuccess: () => {
        setDraft({})
        // The game reads its config on start: a running server needs a restart to see it.
        if (running) onRestartNeeded()
      },
    })

  const row = (entry: ConfigEntry) => (
    <EntryRow
      key={entry.key}
      entry={entry}
      value={draft[entry.key] ?? entry.value}
      changed={entry.key in draft}
      error={fieldErrors[entry.key]}
      onChange={(value) => setValue(entry, value)}
    />
  )

  return (
    <div className="pb-24">
      <div className="relative mb-5">
        <IconSearch size={16} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted" />
        <input
          className={`${inputClass} pl-9`}
          placeholder={`search ${config.entries.length} settings`}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {known.length > 0 && <div className="space-y-4">{known.map(row)}</div>}

      {other.length > 0 && (
        <details className="group mt-8" open={Boolean(query)}>
          <summary className="cursor-pointer text-sm font-medium text-muted select-none hover:text-zinc-200">
            all other settings ({other.length})
          </summary>
          <div className="mt-4 space-y-3">{other.map(row)}</div>
        </details>
      )}

      {known.length === 0 && other.length === 0 && <p className="text-sm text-muted">nothing matches "{search}"</p>}

      {changed > 0 && (
        <div className="fixed inset-x-0 bottom-20 z-40 flex justify-center px-4 sm:bottom-6">
          <div className="flex w-full max-w-xl items-center gap-3 rounded-2xl border border-line-soft bg-panel p-2 pl-4 shadow-2xl">
            <span className="flex-1 text-sm">
              {changed} unsaved {changed === 1 ? 'change' : 'changes'}
              {update.error && !Object.keys(fieldErrors).length && (
                <span className="block text-xs text-red-400">{update.error.message}</span>
              )}
            </span>
            <Button onClick={() => setDraft({})} disabled={update.isPending}>
              discard
            </Button>
            <Button variant="primary" onClick={save} disabled={update.isPending}>
              {update.isPending ? 'saving…' : 'save'}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

interface EntryRowProps {
  entry: ConfigEntry
  value: string
  changed: boolean
  error?: string
  onChange: (value: string) => void
}

function EntryRow({ entry, value, changed, error, onChange }: EntryRowProps) {
  const { hint } = entry
  const id = `cfg-${entry.key}`
  // A dot next to the label marks unsaved values.
  const label = (
    <span className="flex items-center gap-2">
      {hint?.label.toLowerCase() ?? entry.key}
      {changed && <span className="size-1.5 rounded-full bg-sky-400" aria-label="changed" />}
    </span>
  )

  if (entry.managed) {
    return (
      <div className="flex items-center gap-3 text-sm">
        <span className="min-w-0 flex-1 truncate text-muted">{entry.key}</span>
        <span className="flex items-center gap-1.5 text-xs text-muted" title="set by the panel">
          <IconLock size={14} /> managed by the panel
        </span>
      </div>
    )
  }

  if (hint?.type === 'boolean') {
    return (
      <div>
        <div className="flex items-center justify-between gap-4 rounded-xl bg-panel px-3 py-2.5">
          <label htmlFor={id} className="text-sm">
            {label}
            {hint.help && <span className="mt-0.5 block text-xs text-muted">{hint.help}</span>}
          </label>
          <Switch
            id={id}
            checked={value === hint.true_value}
            onChange={(on) => onChange(on ? hint.true_value : hint.false_value)}
          />
        </div>
        <FieldError error={error} />
      </div>
    )
  }

  let input
  if (hint?.type === 'select') {
    const options = hint.options.map((o) => ({ value: o, label: o }))
    input =
      options.length <= SEGMENTED_MAX ? (
        <Segmented options={options} value={value} onChange={onChange} />
      ) : (
        <Select id={id} options={options} value={value} onChange={onChange} />
      )
  } else {
    input = (
      <input
        id={id}
        type={hint?.type === 'number' ? 'number' : 'text'}
        min={hint?.min ?? undefined}
        max={hint?.max ?? undefined}
        className={inputClass}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    )
  }

  // Unhinted keys: compact key/value rows; hinted keys: label above the control.
  if (!hint) {
    return (
      <div>
        <div className="grid items-center gap-1 sm:grid-cols-[minmax(0,14rem)_1fr] sm:gap-3">
          <label htmlFor={id} className="truncate text-sm text-muted" title={entry.key}>
            {label}
          </label>
          {input}
        </div>
        <FieldError error={error} />
      </div>
    )
  }
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-sm font-medium">
        {label}
      </label>
      {input}
      {hint.help && <p className="mt-1.5 text-xs text-muted">{hint.help}</p>}
      <FieldError error={error} />
    </div>
  )
}
