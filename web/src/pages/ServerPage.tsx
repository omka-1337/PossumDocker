import {
  IconArrowLeft,
  IconCopy,
  IconPlayerPlay,
  IconPlayerStop,
  IconRefresh,
  IconTrash,
} from '@tabler/icons-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import {
  useDeleteServer,
  useInstallLog,
  useServer,
  useServerAction,
  useTemplates,
  type ServerAction,
} from '../api/queries'
import type { Server } from '../api/types'
import { Button, Modal, StatusBadge } from '../components/ui'
import { stripAnsi } from '../lib/ansi'

export function ServerPage() {
  const { serverId } = useParams() as { serverId: string }
  const { data: server, isPending, isError, error } = useServer(serverId)

  if (isPending) return <p className="p-8 text-muted">loading…</p>
  if (isError) {
    return (
      <div className="p-8">
        <p className="mb-4 text-red-400">{error.message}</p>
        <Link to="/" className="text-sm text-muted underline">
          back to servers
        </Link>
      </div>
    )
  }
  return <ServerView server={server} />
}

function ServerView({ server }: { server: Server }) {
  const { data: templates } = useTemplates()
  const game = templates?.find((t) => t.id === server.template_id)?.name ?? server.template_id
  const installing = server.status === 'installing' || server.status === 'install_failed'

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-5">
      <Link to="/" className="mb-6 inline-flex items-center gap-1.5 text-sm text-muted hover:text-zinc-200">
        <IconArrowLeft size={16} /> servers
      </Link>

      <div className="mb-6 flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-xl font-semibold">{server.name}</h1>
          <p className="text-sm text-muted lowercase">{game}</p>
        </div>
        <StatusBadge status={server.status} />
      </div>

      <Actions server={server} />
      <Address server={server} />

      {server.status_message && (
        <p className="mb-4 rounded-xl bg-red-950/40 px-3 py-2.5 text-sm text-red-300">{server.status_message}</p>
      )}
      {installing && <InstallLog serverId={server.id} live={server.status === 'installing'} />}

      <DangerZone server={server} />
    </div>
  )
}

function Actions({ server }: { server: Server }) {
  const action = useServerAction(server.id)
  const run = (a: ServerAction) => action.mutate(a)
  const { status } = server
  const busy = action.isPending || status === 'starting' || status === 'stopping' || status === 'installing'

  return (
    <div className="mb-4">
      <div className="flex flex-wrap gap-2">
        {status === 'running' || status === 'starting' || status === 'stopping' ? (
          <>
            <Button onClick={() => run('stop')} disabled={busy && status !== 'starting'}>
              <IconPlayerStop size={16} /> stop
            </Button>
            <Button onClick={() => run('restart')} disabled={busy}>
              <IconRefresh size={16} /> restart
            </Button>
          </>
        ) : status === 'install_failed' ? (
          <Button variant="primary" onClick={() => run('reinstall')} disabled={busy}>
            <IconRefresh size={16} /> reinstall
          </Button>
        ) : (
          <Button variant="primary" onClick={() => run('start')} disabled={busy || status !== 'stopped'}>
            <IconPlayerPlay size={16} /> start
          </Button>
        )}
      </div>
      {action.error && <p className="mt-2 text-sm text-red-400">{action.error.message}</p>}
    </div>
  )
}

function Address({ server }: { server: Server }) {
  const [copied, setCopied] = useState<string | null>(null)
  const entries = Object.entries(server.ports)
  if (entries.length === 0) return null

  // The panel's own hostname is the best guess for where players connect.
  const host = window.location.hostname
  const copy = async (text: string) => {
    await navigator.clipboard?.writeText(text)
    setCopied(text)
    setTimeout(() => setCopied(null), 1500)
  }

  return (
    <div className="mb-6 flex flex-wrap gap-2">
      {entries.map(([name, port]) => {
        const address = `${host}:${port}`
        return (
          <button
            key={name}
            onClick={() => copy(address)}
            className="flex items-center gap-2 rounded-xl bg-panel px-3 py-2 text-sm transition hover:bg-raised"
            title="copy address"
          >
            <span className="text-muted">{name}</span>
            {address}
            <span className="text-muted">{copied === address ? 'copied' : <IconCopy size={14} />}</span>
          </button>
        )
      })}
    </div>
  )
}

function InstallLog({ serverId, live }: { serverId: string; live: boolean }) {
  const { data: lines = [] } = useInstallLog(serverId, live)
  const ref = useRef<HTMLPreElement>(null)

  // Follow the end of the log as new lines arrive.
  useEffect(() => {
    ref.current?.scrollTo({ top: ref.current.scrollHeight })
  }, [lines.length])

  return (
    <div className="mb-6">
      <h2 className="mb-2 text-sm font-medium">install log</h2>
      <pre
        ref={ref}
        className="h-72 overflow-auto rounded-xl border border-line-soft bg-frame p-3 text-xs leading-relaxed whitespace-pre-wrap text-zinc-300"
      >
        {lines.length ? lines.map(stripAnsi).join('\n') : live ? 'waiting for output…' : 'no log for this install'}
      </pre>
    </div>
  )
}

function DangerZone({ server }: { server: Server }) {
  const [confirming, setConfirming] = useState(false)
  const deleteServer = useDeleteServer()
  const navigate = useNavigate()

  return (
    <div className="mt-10 border-t border-line-soft pt-6">
      <Button variant="danger" onClick={() => setConfirming(true)}>
        <IconTrash size={16} /> delete server
      </Button>

      {confirming && (
        <Modal title="delete server?" onClose={() => setConfirming(false)}>
          <p className="mb-5 text-sm text-muted">
            <span className="text-zinc-100">{server.name}</span> will be stopped and removed together with all its
            files and worlds. this can't be undone.
          </p>
          {deleteServer.error && <p className="mb-3 text-sm text-red-400">{deleteServer.error.message}</p>}
          <div className="flex gap-2">
            <Button className="flex-1" onClick={() => setConfirming(false)}>
              cancel
            </Button>
            <Button
              variant="danger"
              className="flex-1"
              disabled={deleteServer.isPending}
              onClick={() => deleteServer.mutate(server.id, { onSuccess: () => navigate('/') })}
            >
              {deleteServer.isPending ? 'deleting…' : 'delete'}
            </Button>
          </div>
        </Modal>
      )}
    </div>
  )
}
