import { IconChevronRight, IconPlus } from '@tabler/icons-react'
import { useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router'
import { useMe } from '../api/auth'
import { useServers, useTemplates } from '../api/queries'
import type { Server } from '../api/types'
import { CreateServerDialog } from '../components/CreateServerDialog'
import { GameIcon } from '../components/GameIcon'
import { Button, StatusBadge } from '../components/ui'

export function ServersPage() {
  const [creating, setCreating] = useState(false)
  const { data: servers, isPending, isError } = useServers()
  const { data: me } = useMe()
  const isAdmin = me?.is_admin ?? false
  const navigate = useNavigate()

  return (
    <div className="flex min-h-full flex-col px-4 py-5">
      {/* Only administrators create servers. */}
      {isAdmin && (
        <button
          onClick={() => setCreating(true)}
          className="group mx-auto flex items-center gap-2.5 rounded-full py-1 pr-3 pl-1 text-sm transition hover:bg-panel"
        >
          <span className="grid size-7 place-items-center rounded-full bg-raised transition group-hover:bg-raised-hover">
            <IconPlus size={16} />
          </span>
          new server
        </button>
      )}

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
          {isAdmin ? (
            <>
              <p className="text-zinc-200">no servers yet</p>
              <p className="mt-1 mb-6 text-sm text-muted">pick a game, set it up, press start.</p>
              <Button variant="primary" onClick={() => setCreating(true)}>
                <IconPlus size={16} /> create a server
              </Button>
            </>
          ) : (
            <>
              <p className="text-zinc-200">no servers shared with you</p>
              <p className="mt-1 text-sm text-muted">ask an administrator to give you access to one.</p>
            </>
          )}
        </Centered>
      ) : (
        <ul className="mx-auto mt-10 w-full max-w-2xl space-y-2">
          {servers.map((server) => (
            <ServerRow key={server.id} server={server} />
          ))}
        </ul>
      )}

      {creating && (
        <CreateServerDialog
          onClose={() => setCreating(false)}
          // Straight to the new server's page, where the install progress is shown.
          onCreated={(server) => navigate(`/servers/${server.id}`)}
        />
      )}
    </div>
  )
}

function Centered({ children }: { children: ReactNode }) {
  return <div className="flex flex-1 flex-col items-center justify-center pb-16 text-center">{children}</div>
}

function ServerRow({ server }: { server: Server }) {
  const { data: templates } = useTemplates()
  const template = templates?.find((t) => t.id === server.template_id)
  const game = template?.name ?? server.template_id

  return (
    <li>
      <Link
        to={`/servers/${server.id}`}
        className="group flex items-center gap-3 rounded-2xl bg-panel p-3 transition hover:bg-raised"
      >
        <GameIcon template={template} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{server.name}</div>
          <div className="truncate text-xs text-muted lowercase">{game}</div>
        </div>
        <StatusBadge status={server.status} />
        <IconChevronRight size={18} className="text-muted transition group-hover:translate-x-0.5" />
      </Link>
    </li>
  )
}
