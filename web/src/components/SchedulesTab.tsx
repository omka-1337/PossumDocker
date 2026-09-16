import {
  IconArchive,
  IconCalendarPlus,
  IconPencil,
  IconPlayerPlay,
  IconPlayerStop,
  IconRefresh,
  IconTerminal2,
  IconTrash,
  type Icon,
} from '@tabler/icons-react'
import { useState, type FormEvent } from 'react'
import { ApiError } from '../api/client'
import { useMeta, useScheduleActions, useSchedules } from '../api/queries'
import type { Permission } from '../api/auth'
import type { Schedule, ScheduleAction, ScheduleWrite, Server } from '../api/types'
import { DAYS, fromCron, HOUR_STEPS, toCron, type Preset } from '../lib/cron'
import { formatDate } from '../lib/format'
import { FieldError, inputClass, Select } from './FieldInput'
import { Button, IconButton, Modal, Segmented, Switch } from './ui'

const ACTIONS: { value: ScheduleAction; label: string; icon: Icon }[] = [
  { value: 'backup', label: 'backup', icon: IconArchive },
  { value: 'restart', label: 'restart', icon: IconRefresh },
  { value: 'command', label: 'command', icon: IconTerminal2 },
  { value: 'start', label: 'start', icon: IconPlayerPlay },
  { value: 'stop', label: 'stop', icon: IconPlayerStop },
]

// A schedule acts with the panel's rights, so setting one up needs the right for the action itself.
const ACTION_PERMISSION: Record<ScheduleAction, Permission> = {
  backup: 'backups',
  restart: 'control',
  start: 'control',
  stop: 'control',
  command: 'console',
}

const lastStatusStyle = { ok: 'bg-emerald-400', skipped: 'bg-zinc-500', failed: 'bg-red-500' }

const when = (iso: string | null) => (iso ? formatDate(Date.parse(iso) / 1000) : '—')

export function SchedulesTab({ server, permissions }: { server: Server; permissions: Permission[] }) {
  const allowed = ACTIONS.filter((a) => permissions.includes(ACTION_PERMISSION[a.value]))
  const canManage = (action: ScheduleAction) => permissions.includes(ACTION_PERMISSION[action])
  const { data: schedules, isPending, isError, error } = useSchedules(server.id)
  const { data: meta } = useMeta()
  const actions = useScheduleActions(server.id)
  // null: closed; "new": creating; a schedule: editing it.
  const [editing, setEditing] = useState<Schedule | 'new' | null>(null)
  const [deleting, setDeleting] = useState<Schedule | null>(null)

  if (isPending) return <p className="text-sm text-muted">loading…</p>
  if (isError) return <p className="text-sm text-red-400">{error.message}</p>

  const toggle = (schedule: Schedule) => {
    const { id, ...rest } = schedule
    actions.save.mutate({ id, body: { ...rest, enabled: !schedule.enabled } })
  }
  const listError = actions.remove.error ?? actions.runNow.error ?? (editing ? null : actions.save.error)

  return (
    <div className="pb-10">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <p className="flex-1 text-sm text-muted">times are in the panel's time zone{meta && `: ${meta.timezone}`}</p>
        <Button variant="primary" onClick={() => setEditing('new')} disabled={allowed.length === 0}>
          <IconCalendarPlus size={16} /> new schedule
        </Button>
      </div>
      {listError && <p className="mb-4 text-sm text-red-400">{listError.message}</p>}

      {schedules.length === 0 ? (
        <div className="rounded-xl border border-dashed border-line p-10 text-center text-sm text-muted">
          <IconCalendarPlus size={32} stroke={1.3} className="mx-auto mb-3 text-zinc-600" />
          <p className="text-zinc-200">nothing scheduled</p>
          <p className="mt-1">back up every night, restart every morning, or send a command on a timer.</p>
        </div>
      ) : (
        <ul className="space-y-2">
          {schedules.map((schedule) => {
            const action = ACTIONS.find((a) => a.value === schedule.action)!
            return (
              <li key={schedule.id} className={`flex items-center gap-3 rounded-2xl bg-panel p-3 ${schedule.enabled ? '' : 'opacity-60'}`}>
                <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-page text-zinc-400">
                  <action.icon size={20} stroke={1.5} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{schedule.name}</div>
                  <div className="truncate text-xs text-muted" title={schedule.cron}>
                    {schedule.description.toLowerCase()}
                    {schedule.enabled && ` · next ${when(schedule.next_run_at)}`}
                    {schedule.action === 'command' && ` · ${schedule.command}`}
                    {schedule.action === 'backup' && schedule.keep && ` · keeps ${schedule.keep}`}
                  </div>
                  {schedule.last_status && (
                    <div className="flex items-center gap-1.5 truncate text-xs text-muted">
                      <span className={`size-1.5 shrink-0 rounded-full ${lastStatusStyle[schedule.last_status]}`} />
                      last {when(schedule.last_run_at)}: {schedule.last_status}
                      {schedule.last_message && ` · ${schedule.last_message}`}
                    </div>
                  )}
                </div>
                {canManage(schedule.action) && (
                  <>
                    <Switch checked={schedule.enabled} onChange={() => toggle(schedule)} />
                    <IconButton onClick={() => actions.runNow.mutate(schedule.id)} aria-label="run now" title="run now">
                      <IconPlayerPlay size={18} />
                    </IconButton>
                    <IconButton onClick={() => setEditing(schedule)} aria-label="edit" title="edit">
                      <IconPencil size={18} />
                    </IconButton>
                  </>
                )}
                <IconButton onClick={() => setDeleting(schedule)} aria-label="delete" title="delete">
                  <IconTrash size={18} />
                </IconButton>
              </li>
            )
          })}
        </ul>
      )}

      {editing && (
        <ScheduleEditor
          schedule={editing === 'new' ? null : editing}
          actions={allowed}
          saving={actions.save.isPending}
          error={actions.save.error}
          onClose={() => {
            setEditing(null)
            actions.save.reset()
          }}
          onSave={(body) =>
            actions.save.mutate(
              { id: editing === 'new' ? null : editing.id, body },
              { onSuccess: () => setEditing(null) },
            )
          }
        />
      )}

      {deleting && (
        <Modal title={`delete "${deleting.name}"?`} onClose={() => setDeleting(null)}>
          <p className="mb-5 text-sm text-muted">backups it already made are kept.</p>
          <div className="flex gap-2">
            <Button className="flex-1" onClick={() => setDeleting(null)}>
              cancel
            </Button>
            <Button
              variant="danger"
              className="flex-1"
              onClick={() => actions.remove.mutate(deleting.id, { onSuccess: () => setDeleting(null) })}
            >
              delete
            </Button>
          </div>
        </Modal>
      )}
    </div>
  )
}

const PRESET_KINDS = [
  { value: 'hourly', label: 'hourly' },
  { value: 'daily', label: 'daily' },
  { value: 'weekly', label: 'weekly' },
  { value: 'custom', label: 'cron' },
]

function defaultPreset(kind: Preset['kind'], current: Preset): Preset {
  const time = 'time' in current ? current.time : '04:00'
  switch (kind) {
    case 'hourly':
      return { kind, every: 6 }
    case 'daily':
      return { kind, time }
    case 'weekly':
      return { kind, day: 1, time }
    case 'custom':
      return { kind, cron: toCron(current) }
  }
}

function ScheduleEditor({
  schedule,
  actions,
  saving,
  error,
  onClose,
  onSave,
}: {
  schedule: Schedule | null
  actions: typeof ACTIONS
  saving: boolean
  error: Error | null
  onClose: () => void
  onSave: (body: ScheduleWrite) => void
}) {
  const [name, setName] = useState(schedule?.name ?? 'nightly backup')
  const [action, setAction] = useState<ScheduleAction>(schedule?.action ?? actions[0].value)
  const [command, setCommand] = useState(schedule?.command ?? '')
  const [keep, setKeep] = useState(schedule?.keep ?? 7)
  const [preset, setPreset] = useState<Preset>(schedule ? fromCron(schedule.cron) : { kind: 'daily', time: '04:00' })
  const fieldErrors = error instanceof ApiError ? error.fieldErrors : {}

  const submit = (e: FormEvent) => {
    e.preventDefault()
    onSave({
      name: name.trim(),
      cron: toCron(preset),
      action,
      command: action === 'command' ? command : null,
      keep: action === 'backup' ? keep : null,
      enabled: schedule?.enabled ?? true,
    })
  }

  return (
    <Modal title={schedule ? 'edit schedule' : 'new schedule'} onClose={onClose}>
      <form onSubmit={submit} className="space-y-5">
        <div>
          <label htmlFor="schedule-name" className="mb-1.5 block text-sm font-medium">
            name
          </label>
          <input id="schedule-name" className={inputClass} maxLength={64} value={name} onChange={(e) => setName(e.target.value)} />
          <FieldError error={fieldErrors.name} />
        </div>

        <div>
          <span className="mb-1.5 block text-sm font-medium">what</span>
          <Segmented options={actions} value={action} onChange={(v) => setAction(v as ScheduleAction)} />
          {action === 'command' && (
            <>
              <input
                aria-label="console command"
                placeholder="say the server restarts in 5 minutes"
                className={`${inputClass} mt-2`}
                value={command}
                onChange={(e) => setCommand(e.target.value)}
              />
              <FieldError error={fieldErrors.command} />
            </>
          )}
          {action === 'backup' && (
            <label className="mt-2 flex items-center gap-2 text-sm text-muted">
              keep the newest
              <input
                type="number"
                min={1}
                max={100}
                className={`${inputClass} w-20`}
                value={keep}
                onChange={(e) => setKeep(Math.max(1, Math.min(100, e.target.valueAsNumber || 1)))}
              />
              backups from this schedule
            </label>
          )}
          {(action === 'restart' || action === 'command' || action === 'stop') && (
            <p className="mt-2 text-xs text-muted">skipped when the server isn't running.</p>
          )}
        </div>

        <div>
          <span className="mb-1.5 block text-sm font-medium">when</span>
          <Segmented
            options={PRESET_KINDS}
            value={preset.kind}
            onChange={(kind) => setPreset(defaultPreset(kind as Preset['kind'], preset))}
          />
          <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-muted">
            {preset.kind === 'hourly' && (
              <>
                every
                <Select
                  id="schedule-every"
                  options={HOUR_STEPS.map((n) => ({ value: String(n), label: n === 1 ? 'hour' : `${n} hours` }))}
                  value={String(preset.every)}
                  onChange={(v) => setPreset({ kind: 'hourly', every: Number(v) })}
                />
              </>
            )}
            {preset.kind === 'weekly' && (
              <>
                on
                <Select
                  id="schedule-day"
                  options={DAYS.map((d, i) => ({ value: String(i), label: d }))}
                  value={String(preset.day)}
                  onChange={(v) => setPreset({ ...preset, day: Number(v) })}
                />
              </>
            )}
            {(preset.kind === 'daily' || preset.kind === 'weekly') && (
              <>
                at
                <input
                  type="time"
                  aria-label="time"
                  className={`${inputClass} w-32`}
                  value={preset.time}
                  onChange={(e) => e.target.value && setPreset({ ...preset, time: e.target.value })}
                />
              </>
            )}
            {preset.kind === 'custom' && (
              <input
                aria-label="cron expression"
                placeholder="minute hour day month weekday"
                className={`${inputClass} font-mono`}
                value={preset.cron}
                onChange={(e) => setPreset({ kind: 'custom', cron: e.target.value })}
              />
            )}
          </div>
          <FieldError error={fieldErrors.cron} />
        </div>

        {error && Object.keys(fieldErrors).length === 0 && <p className="text-sm text-red-400">{error.message}</p>}

        <Button type="submit" variant="primary" className="w-full" disabled={!name.trim() || saving}>
          {saving ? 'saving…' : 'save'}
        </Button>
      </form>
    </Modal>
  )
}
