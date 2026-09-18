import { IconArrowLeft, IconCopy, IconPlayerPlay, IconPlayerStop, IconRefresh } from '@tabler/icons-react'
import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router'
import {
  useInstallLog,
  useServer,
  useServerAction,
  useTemplate,
  useTemplates,
  type ServerAction,
} from '../api/queries'
import { useMe, useServerPermissions, type Permission } from '../api/auth'
import type { Server } from '../api/types'
import { AccessTab } from '../components/AccessTab'
import { BackupsTab } from '../components/BackupsTab'
import { DeleteServer } from '../components/DeleteServer'
import { FileBrowser } from '../components/files/FileBrowser'
import { PlayersTab } from '../components/PlayersTab'
import { GameIcon } from '../components/GameIcon'
import { SchedulesTab } from '../components/SchedulesTab'
import { SettingsTab } from '../components/SettingsTab'
import { Button, StatusBadge, Tabs } from '../components/ui'
import { stripAnsi } from '../lib/ansi'

// xterm.js is big: load it only when a console is actually shown.
const Console = lazy(() => import('../components/Console').then((m) => ({ default: m.Console })))

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
  const { data: me } = useMe()
  const { data: permissions = [] } = useServerPermissions(server.id)
  const isAdmin = me?.is_admin ?? false
  const { data: templates } = useTemplates()
  const template = templates?.find((t) => t.id === server.template_id)
  const game = template?.name ?? server.template_id
  const installing = server.status === 'installing' || server.status === 'install_failed'

  return (
    <div className="w-full px-3 py-3">
      <Link to="/" className="mb-6 inline-flex items-center gap-1.5 text-sm text-muted hover:text-zinc-200">
        <IconArrowLeft size={16} /> servers
      </Link>

      <div className="mb-6 flex flex-wrap items-center gap-3">
        <GameIcon template={template} size="lg" />
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-xl font-semibold">{server.name}</h1>
          <p className="text-sm text-muted lowercase">{game}</p>
        </div>
        <StatusBadge status={server.status} />
      </div>

      <Actions server={server} canControl={permissions.includes('control')} isAdmin={isAdmin} />
      <Address server={server} />

      {server.status_message && (
        <p className="mb-4 rounded-xl bg-red-950/40 px-3 py-2.5 text-sm text-red-300">{server.status_message}</p>
      )}
      {installing ? (
        <InstallLog serverId={server.id} live={server.status === 'installing'} />
      ) : (
        server.status !== 'pending' && <InstalledTabs server={server} permissions={permissions} isAdmin={isAdmin} />
      )}

      {/* Deleting lives on the settings tab; a server that never got one is deleted from here. */}
      {isAdmin && (installing || server.status === 'pending') && (
        <div className="mt-10 flex justify-end border-t border-line-soft pt-6">
          <DeleteServer server={server} />
        </div>
      )}
    </div>
  )
}

type TabId = 'console' | 'players' | 'files' | 'backups' | 'schedules' | 'settings' | 'access'

function InstalledTabs({ server, permissions, isAdmin }: { server: Server; permissions: Permission[]; isAdmin: boolean }) {
  const { data: template } = useTemplate(server.template_id)
  const [chosen, setTab] = useState<TabId>('console')
  const can = (p: Permission) => permissions.includes(p)

  // Only the parts this user may use.
  const tabs = (
    [
      { value: 'console', label: 'console', shown: true },
      { value: 'players', label: 'players', shown: can('players') && Boolean(template?.players) },
      { value: 'files', label: 'files', shown: can('files') },
      { value: 'backups', label: 'backups', shown: can('backups') || can('restore') },
      { value: 'schedules', label: 'schedules', shown: can('schedules') },
      { value: 'settings', label: 'settings', shown: can('settings') },
      { value: 'access', label: 'access', shown: isAdmin },
    ] as const
  ).filter((t) => t.shown)
  const tab = tabs.some((t) => t.value === chosen) ? chosen : 'console'

  return (
    <>
      <Tabs tabs={tabs.map(({ value, label }) => ({ value, label }))} value={tab} onChange={setTab} />
      {tab === 'console' ? (
        <Suspense fallback={<p className="mb-6 text-sm text-muted">loading console…</p>}>
          <Console
            serverId={server.id}
            running={server.status === 'running' || server.status === 'starting'}
            consoleSpec={template?.console}
            canSend={can('console')}
          />
        </Suspense>
      ) : tab === 'players' ? (
        <PlayersTab server={server} />
      ) : tab === 'files' ? (
        <FileBrowser serverId={server.id} />
      ) : tab === 'backups' ? (
        <BackupsTab server={server} canBackup={can('backups')} canRestore={can('restore')} />
      ) : tab === 'schedules' ? (
        <SchedulesTab server={server} permissions={permissions} />
      ) : tab === 'access' ? (
        <AccessTab serverId={server.id} />
      ) : (
        <SettingsTab server={server} isAdmin={isAdmin} />
      )}
    </>
  )
}

function Actions({ server, canControl, isAdmin }: { server: Server; canControl: boolean; isAdmin: boolean }) {
  const action = useServerAction(server.id)
  const run = (a: ServerAction) => action.mutate(a)
  const { status } = server
  const busy =
    action.isPending || status === 'starting' || status === 'stopping' || status === 'installing' || status === 'restoring'

  // Nothing to press: a failed install can only be retried by an administrator.
  if (!canControl && !(isAdmin && status === 'install_failed')) return null

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
          isAdmin && (
          <Button variant="primary" onClick={() => run('reinstall')} disabled={busy}>
            <IconRefresh size={16} /> reinstall
          </Button>
          )
        ) : (
          <Button
            variant="primary"
            onClick={() => run('start')}
            disabled={busy || (status !== 'stopped' && status !== 'crashed')}
          >
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
