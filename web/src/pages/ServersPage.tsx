import { IconChevronRight, IconChevronUp, IconPlus } from '@tabler/icons-react'
import { useMemo, useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router'
import { useMe } from '../api/auth'
import { useServers, useServerStats, useTemplates } from '../api/queries'
import type { Server, ServerStats, ServerStatus } from '../api/types'
import { CreateServerDialog } from '../components/CreateServerDialog'
import { GameIcon } from '../components/GameIcon'
import { Button, StatusBadge } from '../components/ui'
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

type SortKey = 'name' | 'type' | 'status' | 'port' | 'id' | 'size' | 'load'
type Sort = { key: SortKey; desc: boolean }

/** What a column sorts by; numbers compare as numbers, and a server with no numbers yet goes last. */
function sortValue(key: SortKey, server: Server, game: string, stats?: ServerStats): string | number {
  switch (key) {
    case 'name':
      return server.name.toLowerCase()
    case 'type':
      return game.toLowerCase()
    case 'status':
      return STATUS_ORDER[server.status]
    case 'port':
      return Object.values(server.ports)[0] ?? -1
    case 'id':
      return server.id
    case 'size':
      return stats?.disk_used ?? -1
    case 'load':
      return stats?.cpus ?? -1
  }
}

export function ServersPage() {
  const [creating, setCreating] = useState(false)
  const { data: all, isPending, isError } = useServers()
  const { data: me } = useMe()
  const { data: templates } = useTemplates()
  const isAdmin = me?.is_admin ?? false
  const navigate = useNavigate()
  // null: the default order below. A column sorts one way, then the other, then back to it.
  const [sort, setSort] = useStoredState<Sort | null>('possum.servers.sort', null)
  const hasServers = (all?.length ?? 0) > 0
  const { data: stats } = useServerStats(hasServers)
  const statsOf = (id: string) => stats?.find((s) => s.id === id)

  const servers = useMemo(() => {
    const list = all ?? []
    if (!sort) return list.toSorted((a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status])
    const gameOf = (server: Server) =>
      templates?.find((t) => t.id === server.template_id)?.name ?? server.template_id
    const statsFor = (id: string) => stats?.find((entry) => entry.id === id)
    return list.toSorted((a, b) => {
      const left = sortValue(sort.key, a, gameOf(a), statsFor(a.id))
      const right = sortValue(sort.key, b, gameOf(b), statsFor(b.id))
      const order =
        typeof left === 'string' && typeof right === 'string'
          ? left.localeCompare(right)
          : Number(left) - Number(right)
      // Same value: keep it readable by name instead of leaving it to chance.
      return (sort.desc ? -order : order) || a.name.localeCompare(b.name)
    })
  }, [all, sort, stats, templates])

  const toggleSort = (key: SortKey) =>
    setSort(sort?.key !== key ? { key, desc: false } : sort.desc ? null : { key, desc: true })

  return (
    <div className="flex min-h-full flex-col px-3 py-3">
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
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead className="text-left text-xs text-muted">
              <tr>
                <SortHeader column="name" label="name" sort={sort} onSort={toggleSort} />
                <SortHeader column="type" label="type" sort={sort} onSort={toggleSort} />
                <SortHeader column="status" label="status" sort={sort} onSort={toggleSort} />
                <SortHeader
                  column="port"
                  label="port"
                  sort={sort}
                  onSort={toggleSort}
                  className="hidden sm:table-cell"
                />
                <SortHeader column="id" label="id" sort={sort} onSort={toggleSort} className="hidden lg:table-cell" />
                <SortHeader
                  column="size"
                  label="size"
                  sort={sort}
                  onSort={toggleSort}
                  className="hidden text-right sm:table-cell"
                />
                <SortHeader
                  column="load"
                  label="cpu / ram"
                  sort={sort}
                  onSort={toggleSort}
                  className="hidden text-right md:table-cell"
                />
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

/** A column header that sorts the list by its column. */
function SortHeader({
  column,
  label,
  sort,
  onSort,
  className = '',
}: {
  column: SortKey
  label: string
  sort: Sort | null
  onSort: (key: SortKey) => void
  className?: string
}) {
  const active = sort?.key === column
  return (
    <th
      className={`px-3 py-2 font-medium ${className}`}
      aria-sort={active ? (sort.desc ? 'descending' : 'ascending') : 'none'}
    >
      <button
        type="button"
        onClick={() => onSort(column)}
        className={`inline-flex items-center gap-1 transition hover:text-zinc-200 ${active ? 'text-zinc-200' : ''}`}
      >
        {label}
        <IconChevronUp
          size={12}
          className={`transition ${active ? (sort.desc ? 'rotate-180' : '') : 'opacity-0'}`}
        />
      </button>
    </th>
  )
}

function Centered({ children }: { children: ReactNode }) {
  return <div className="flex flex-1 flex-col items-center justify-center pb-16 text-center">{children}</div>
}

/** The game a server runs, for its row. */
function useGame(server: Server) {
  const { data: templates } = useTemplates()
  const template = templates?.find((t) => t.id === server.template_id)
  return { template, game: template?.name ?? server.template_id }
}

function ServerRow({ server, stats }: { server: Server; stats?: ServerStats }) {
  const { template, game } = useGame(server)
  const navigate = useNavigate()
  // The first port is the one players use; the rest (rcon, query) are only counted here.
  const ports = Object.entries(server.ports)
  const [, port] = ports[0] ?? []
  const extra = ports.length - 1
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
      <td
        className="hidden px-3 py-2 text-muted tabular-nums sm:table-cell"
        title={ports.map(([name, value]) => `${name} ${value}`).join(' · ')}
      >
        {port ?? '—'}
        {extra > 0 && <span className="ml-1 text-xs">+{extra}</span>}
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
