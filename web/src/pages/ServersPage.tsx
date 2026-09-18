import { IconChevronRight, IconLayoutGrid, IconList, IconPlus } from '@tabler/icons-react'
import { useMemo, useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router'
import { useMe } from '../api/auth'
import { useServers, useServerStats, useTemplates } from '../api/queries'
import type { Server, ServerStats, ServerStatus } from '../api/types'
import { CreateServerDialog } from '../components/CreateServerDialog'
import { GameCover, GameIcon } from '../components/GameIcon'
import { Button, IconButton, StatusBadge } from '../components/ui'
import { formatSize } from '../lib/format'
import { useStoredState } from '../lib/storage'

// Running servers first, then the ones on their way somewhere, then the rest; the panel's own order
// (oldest first) decides within a group.
const STATUS_ORDER: Record<ServerStatus, number> = {
  running: 0,
  starting: 0,
  stopping: 1,
  restoring: 1,
  installing: 1,
  pending: 1,
  crashed: 2,
  stopped: 3,
  install_failed: 4,
  unknown: 4,
}

export function ServersPage() {
  const [creating, setCreating] = useState(false)
  const { data: all, isPending, isError } = useServers()
  const servers = useMemo(
    () => (all ?? []).toSorted((a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status]),
    [all],
  )
  const { data: me } = useMe()
  const isAdmin = me?.is_admin ?? false
  const navigate = useNavigate()
  const [view, setView] = useStoredState<'list' | 'tiles'>('possum.servers.view', 'list')
  const hasServers = servers.length > 0
  // Only the list shows what each server uses.
  const { data: stats } = useServerStats(hasServers && view === 'list')
  const statsOf = (id: string) => stats?.find((s) => s.id === id)

  return (
    <div className="flex min-h-full flex-col px-2 py-3">
      <div className="mb-3 flex items-center gap-2">
        {/* Only administrators create servers. */}
        {isAdmin && (
          <button
            onClick={() => setCreating(true)}
            className="group flex items-center gap-2.5 rounded-full py-1 pr-3 pl-1 text-sm transition hover:bg-panel"
          >
            <span className="grid size-7 place-items-center rounded-full bg-raised transition group-hover:bg-raised-hover">
              <IconPlus size={16} />
            </span>
            new server
          </button>
        )}
        <div className="flex-1" />
        {hasServers && (
          <IconButton
            onClick={() => setView(view === 'list' ? 'tiles' : 'list')}
            aria-label="switch view"
            title={view === 'list' ? 'tiles' : 'list'}
          >
            {view === 'list' ? <IconLayoutGrid size={18} /> : <IconList size={18} />}
          </IconButton>
        )}
      </div>

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
          {/* Mirrored so the possum walks into the page. */}
          <img
            src="/no-servers.webp"
            alt=""
            draggable={false}
            className="mx-auto mb-6 w-full max-w-sm -scale-x-100 select-none"
          />
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
      ) : view === 'tiles' ? (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(14rem,1fr))] gap-2">
          {servers.map((server) => (
            <ServerTile key={server.id} server={server} />
          ))}
        </ul>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead className="text-left text-xs text-muted">
              <tr>
                <th className="px-3 py-2 font-medium">name</th>
                <th className="px-3 py-2 font-medium">type</th>
                <th className="px-3 py-2 font-medium">status</th>
                <th className="hidden px-3 py-2 font-medium lg:table-cell">id</th>
                <th className="hidden px-3 py-2 text-right font-medium sm:table-cell">size</th>
                <th className="hidden px-3 py-2 text-right font-medium md:table-cell">cpu / ram</th>
                <th className="w-8" />
              </tr>
            </thead>
            <tbody>
              {servers.map((server) => (
                <ServerRow key={server.id} server={server} stats={statsOf(server.id)} />
              ))}
            </tbody>
          </table>
        </div>
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

/** The game a server runs, for its row or tile. */
function useGame(server: Server) {
  const { data: templates } = useTemplates()
  const template = templates?.find((t) => t.id === server.template_id)
  return { template, game: template?.name ?? server.template_id }
}

function ServerRow({ server, stats }: { server: Server; stats?: ServerStats }) {
  const { template, game } = useGame(server)
  const navigate = useNavigate()
  // A running server reports what it uses; a stopped one only its size.
  const load =
    stats?.cpus != null && stats.memory_bytes != null
      ? `${Math.round(stats.cpus * 100)}% · ${formatSize(stats.memory_bytes)}${
          stats.memory_limit ? ` / ${formatSize(stats.memory_limit)}` : ''
        }`
      : '—'

  return (
    <tr
      onClick={() => navigate(`/servers/${server.id}`)}
      className="group cursor-pointer border-t border-line-soft transition hover:bg-panel"
    >
      <td className="px-3 py-2">
        <div className="flex items-center gap-3">
          <GameIcon template={template} />
          <Link
            to={`/servers/${server.id}`}
            onClick={(e) => e.stopPropagation()}
            className="min-w-0 truncate font-medium outline-none"
          >
            {server.name}
          </Link>
        </div>
      </td>
      <td className="px-3 py-2 text-muted lowercase">{game}</td>
      <td className="px-3 py-2">
        <StatusBadge status={server.status} />
      </td>
      <td className="hidden px-3 py-2 font-mono text-xs text-muted lg:table-cell" title={server.id}>
        {server.id.slice(0, 8)}
      </td>
      <td className="hidden px-3 py-2 text-right text-muted tabular-nums sm:table-cell">
        {stats?.disk_used != null ? formatSize(stats.disk_used) : '—'}
      </td>
      <td className="hidden px-3 py-2 text-right text-muted tabular-nums md:table-cell">{load}</td>
      <td className="w-8 pr-2">
        <IconChevronRight size={18} className="text-muted transition group-hover:translate-x-0.5" />
      </td>
    </tr>
  )
}

function ServerTile({ server }: { server: Server }) {
  const { template, game } = useGame(server)

  return (
    <li>
      <Link
        to={`/servers/${server.id}`}
        className="group block overflow-hidden rounded-2xl bg-panel transition hover:bg-raised"
      >
        {template ? <GameCover template={template} /> : <div className="aspect-[460/215] w-full bg-page" />}
        <div className="flex items-center gap-2 p-3">
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium">{server.name}</div>
            <div className="truncate text-xs text-muted lowercase">{game}</div>
          </div>
          <StatusBadge status={server.status} />
        </div>
      </Link>
    </li>
  )
}
