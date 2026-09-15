import {
  IconArchive,
  IconCalendarTime,
  IconDownload,
  IconHistory,
  IconLoader2,
  IconPlus,
  IconTrash,
} from '@tabler/icons-react'
import { useState } from 'react'
import { useBackupActions, useBackups } from '../api/queries'
import type { Backup, Server } from '../api/types'
import { formatDate, formatSize } from '../lib/format'
import { inputClass } from './FieldInput'
import { Button, IconButton, Modal } from './ui'

export function BackupsTab({ server }: { server: Server }) {
  const { data, isPending, isError, error } = useBackups(server.id, server.status === 'restoring')
  const actions = useBackupActions(server.id)
  const [creating, setCreating] = useState(false)
  const [restoring, setRestoring] = useState<Backup | null>(null)
  const [deleting, setDeleting] = useState<Backup | null>(null)

  if (isPending) return <p className="text-sm text-muted">loading…</p>
  if (isError) return <p className="text-sm text-red-400">{error.message}</p>

  const { backups, total_size: totalSize, restore_error: restoreError } = data
  const actionError = actions.create.error ?? actions.restore.error ?? actions.remove.error

  return (
    <div className="pb-10">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <p className="flex-1 text-sm text-muted">
          {backups.length} {backups.length === 1 ? 'backup' : 'backups'} · {formatSize(totalSize)}
        </p>
        <Button variant="primary" onClick={() => setCreating(true)}>
          <IconPlus size={16} /> back up now
        </Button>
      </div>

      {server.status === 'restoring' && (
        <p className="mb-4 flex items-center gap-2 rounded-xl bg-sky-950/40 px-3 py-2.5 text-sm text-sky-200">
          <IconLoader2 size={16} className="animate-spin" /> restoring… the server can be started once this is done.
        </p>
      )}
      {restoreError && server.status !== 'restoring' && (
        <p className="mb-4 rounded-xl bg-red-950/40 px-3 py-2.5 text-sm text-red-300">
          the last restore failed: {restoreError}
        </p>
      )}
      {actionError && <p className="mb-4 text-sm text-red-400">{actionError.message}</p>}

      {backups.length === 0 ? (
        <div className="rounded-xl border border-dashed border-line p-10 text-center text-sm text-muted">
          <IconArchive size={32} stroke={1.3} className="mx-auto mb-3 text-zinc-600" />
          <p className="text-zinc-200">no backups yet</p>
          <p className="mt-1">make one now, or add a schedule to back up automatically.</p>
        </div>
      ) : (
        <ul className="space-y-2">
          {backups.map((backup) => (
            <BackupRow
              key={backup.id}
              serverId={server.id}
              backup={backup}
              onRestore={() => setRestoring(backup)}
              onDelete={() => setDeleting(backup)}
            />
          ))}
        </ul>
      )}

      {creating && (
        <CreateBackupDialog
          running={server.status === 'running'}
          busy={actions.create.isPending}
          onClose={() => setCreating(false)}
          onCreate={(note) => actions.create.mutate(note, { onSuccess: () => setCreating(false) })}
        />
      )}

      {restoring && (
        <Modal title="restore this backup?" onClose={() => setRestoring(null)}>
          <p className="mb-3 text-sm text-muted">
            from <span className="text-zinc-100">{formatDate(Date.parse(restoring.created_at) / 1000)}</span>
            {restoring.note && <> · {restoring.note}</>}
          </p>
          <p className="mb-5 text-sm text-muted">
            {restoring.paths
              ? `these files and folders are replaced with the ones in the backup: ${restoring.paths.join(', ')}.`
              : "the server's files are replaced with the backup. anything added since then is lost."}
          </p>
          {server.status !== 'stopped' && (
            <p className="mb-4 rounded-xl bg-amber-950/40 px-3 py-2 text-sm text-amber-200">stop the server first.</p>
          )}
          <div className="flex gap-2">
            <Button className="flex-1" onClick={() => setRestoring(null)}>
              cancel
            </Button>
            <Button
              variant="danger"
              className="flex-1"
              disabled={server.status !== 'stopped' || actions.restore.isPending}
              onClick={() => actions.restore.mutate(restoring.id, { onSuccess: () => setRestoring(null) })}
            >
              <IconHistory size={16} /> restore
            </Button>
          </div>
        </Modal>
      )}

      {deleting && (
        <Modal title="delete this backup?" onClose={() => setDeleting(null)}>
          <p className="mb-5 text-sm text-muted">
            {formatDate(Date.parse(deleting.created_at) / 1000)} · {formatSize(deleting.size)}. this can't be undone.
          </p>
          <div className="flex gap-2">
            <Button className="flex-1" onClick={() => setDeleting(null)}>
              cancel
            </Button>
            <Button
              variant="danger"
              className="flex-1"
              disabled={actions.remove.isPending}
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

function BackupRow({
  serverId,
  backup,
  onRestore,
  onDelete,
}: {
  serverId: string
  backup: Backup
  onRestore: () => void
  onDelete: () => void
}) {
  const title = backup.note ?? (backup.schedule_id ? 'scheduled backup' : 'manual backup')
  const contents = backup.paths ? `${backup.paths.length} ${backup.paths.length === 1 ? 'path' : 'paths'}` : 'whole server'

  return (
    <li className="flex items-center gap-3 rounded-2xl bg-panel p-3">
      <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-page text-zinc-400">
        {backup.status === 'creating' ? (
          <IconLoader2 size={20} className="animate-spin text-sky-400" />
        ) : backup.schedule_id ? (
          <IconCalendarTime size={20} stroke={1.5} />
        ) : (
          <IconArchive size={20} stroke={1.5} />
        )}
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{title}</div>
        <div className="truncate text-xs text-muted" title={backup.paths?.join('\n')}>
          {formatDate(Date.parse(backup.created_at) / 1000)}
          {backup.status === 'ready' && ` · ${formatSize(backup.size)} · ${contents}`}
          {backup.status === 'creating' && ' · backing up…'}
        </div>
        {backup.status === 'failed' && <div className="text-xs text-red-400">failed: {backup.message}</div>}
      </div>
      {backup.status === 'ready' && (
        <>
          <a
            href={`/api/servers/${serverId}/backups/${backup.id}/download`}
            className="grid size-8 place-items-center rounded-full text-zinc-400 transition hover:bg-raised hover:text-zinc-100"
            aria-label="download"
            title="download"
          >
            <IconDownload size={18} />
          </a>
          <IconButton onClick={onRestore} aria-label="restore" title="restore">
            <IconHistory size={18} />
          </IconButton>
        </>
      )}
      {backup.status !== 'creating' && (
        <IconButton onClick={onDelete} aria-label="delete" title="delete">
          <IconTrash size={18} />
        </IconButton>
      )}
    </li>
  )
}

function CreateBackupDialog({
  running,
  busy,
  onClose,
  onCreate,
}: {
  running: boolean
  busy: boolean
  onClose: () => void
  onCreate: (note: string) => void
}) {
  const [note, setNote] = useState('')
  return (
    <Modal title="back up now" onClose={onClose}>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          onCreate(note)
        }}
      >
        <label htmlFor="backup-note" className="mb-1.5 block text-sm font-medium">
          note <span className="font-normal text-muted">(optional)</span>
        </label>
        <input
          id="backup-note"
          autoFocus
          maxLength={200}
          placeholder="before installing plugins"
          className={`${inputClass} mb-3`}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        {running && (
          <p className="mb-4 text-xs text-muted">the server keeps running; saving is paused while the backup is made.</p>
        )}
        <Button type="submit" variant="primary" className="w-full" disabled={busy}>
          {busy ? 'starting…' : 'back up'}
        </Button>
      </form>
    </Modal>
  )
}
