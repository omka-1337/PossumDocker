import { IconDeviceGamepad2, IconPlus, IconTrash } from '@tabler/icons-react'
import { useState, type ReactNode } from 'react'
import { useDeleteServer, useServers, useTemplates } from '../api/queries'
import type { Server } from '../api/types'
import { CreateServerDialog } from '../components/CreateServerDialog'
import { Button, IconButton, Modal, StatusBadge } from '../components/ui'

export function ServersPage() {
  const [creating, setCreating] = useState(false)
  const { data: servers, isPending, isError } = useServers()

  return (
    <div className="flex min-h-full flex-col px-4 py-5">
      <button
        onClick={() => setCreating(true)}
        className="group mx-auto flex items-center gap-2.5 rounded-full py-1 pr-3 pl-1 text-sm transition hover:bg-panel"
      >
        <span className="grid size-7 place-items-center rounded-full bg-raised transition group-hover:bg-raised-hover">
          <IconPlus size={16} />
        </span>
        new server
      </button>

      {isPending ? (
        <Centered>
          <p className="text-muted">loading…</p>
        </Centered>
      ) : isError ? (
        <Centered>
          <p className="text-red-400">could not reach the panel api</p>
        </Centered>
      ) : servers.length === 0 ? (
        <Centered>
          <div className="mb-6 text-7xl font-semibold tracking-tighter text-zinc-700 select-none" aria-hidden>
            &gt;_
          </div>
          <p className="text-zinc-200">no servers yet</p>
          <p className="mt-1 mb-6 text-sm text-muted">pick a game, set it up, press start.</p>
          <Button variant="primary" onClick={() => setCreating(true)}>
            <IconPlus size={16} /> create a server
          </Button>
        </Centered>
      ) : (
        <ul className="mx-auto mt-10 w-full max-w-2xl space-y-2">
          {servers.map((server) => (
            <ServerRow key={server.id} server={server} />
          ))}
        </ul>
      )}

      {creating && <CreateServerDialog onClose={() => setCreating(false)} onCreated={() => setCreating(false)} />}
    </div>
  )
}

function Centered({ children }: { children: ReactNode }) {
  return <div className="flex flex-1 flex-col items-center justify-center pb-16 text-center">{children}</div>
}

function ServerRow({ server }: { server: Server }) {
  const [confirming, setConfirming] = useState(false)
  const { data: templates } = useTemplates()
  const deleteServer = useDeleteServer()
  const game = templates?.find((t) => t.id === server.template_id)?.name ?? server.template_id

  return (
    <li className="flex items-center gap-3 rounded-2xl bg-panel p-3">
      <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-page">
        <IconDeviceGamepad2 size={22} stroke={1.5} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{server.name}</div>
        <div className="truncate text-xs text-muted lowercase">{game}</div>
      </div>
      <StatusBadge status={server.status} />
      <IconButton onClick={() => setConfirming(true)} aria-label={`delete ${server.name}`}>
        <IconTrash size={18} />
      </IconButton>

      {confirming && (
        <Modal title="delete server?" onClose={() => setConfirming(false)}>
          <p className="mb-5 text-sm text-muted">
            <span className="text-zinc-100">{server.name}</span> will be removed from the panel.
          </p>
          <div className="flex gap-2">
            <Button className="flex-1" onClick={() => setConfirming(false)}>
              cancel
            </Button>
            <Button
              variant="danger"
              className="flex-1"
              disabled={deleteServer.isPending}
              onClick={() => deleteServer.mutate(server.id, { onSuccess: () => setConfirming(false) })}
            >
              delete
            </Button>
          </div>
        </Modal>
      )}
    </li>
  )
}
