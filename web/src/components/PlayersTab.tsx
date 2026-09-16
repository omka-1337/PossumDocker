import { IconBan, IconDoorExit, IconRefresh, IconUserCircle } from '@tabler/icons-react'
import { useState, type FormEvent } from 'react'
import {
  usePlayerActions,
  usePlayers,
  type BanInfo,
  type BanKind,
  type Effective,
  type PlayerAbilities,
  type PlayerInfo,
} from '../api/players'
import type { Server } from '../api/types'
import { formatAgo, formatDate } from '../lib/format'
import { FieldError, inputClass } from './FieldInput'
import { Button, IconButton, Modal, Segmented } from './ui'

const EFFECTIVE_NOTE: Record<Effective, string | null> = {
  now: null,
  'after restart': 'saved: takes effect when the server restarts.',
  'on join': 'saved: the panel kicks them whenever they join.',
}

const displayName = (player: PlayerInfo) => player.name ?? player.game_id ?? player.key.replace(/^\w+:/, '')

export function PlayersTab({ server }: { server: Server }) {
  const { data, isPending, isError, error } = usePlayers(server.id)
  const actions = usePlayerActions(server.id)
  const [banning, setBanning] = useState<PlayerInfo | 'manual' | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const running = server.status === 'running' || server.status === 'starting'

  if (isPending) return <p className="text-sm text-muted">loading…</p>
  if (isError) return <p className="text-sm text-red-400">{error.message}</p>

  const { players, bans, bans_error: bansError, abilities } = data
  const online = players.filter((p) => p.online)
  const offline = players.filter((p) => !p.online)
  const canBan = abilities.ban_by !== null || abilities.ip_bans
  const actionError = actions.refresh.error ?? actions.kick.error ?? actions.unban.error

  if (!abilities.tracked) {
    return <p className="text-sm text-muted">this game doesn&apos;t report its players to the panel.</p>
  }

  const unban = (ban: BanInfo) =>
    actions.unban.mutate(
      { kind: ban.kind, value: ban.value },
      { onSuccess: ({ effective }) => setNote(EFFECTIVE_NOTE[effective]) },
    )

  return (
    <div className="space-y-8 pb-10">
      <div className="flex flex-wrap items-center gap-2">
        <p className="flex-1 text-sm text-muted">
          {online.length} online · {players.length} seen on this server
        </p>
        {abilities.refresh && (
          <Button
            onClick={() => actions.refresh.mutate()}
            disabled={!running || actions.refresh.isPending}
            title={running ? 'ask the game who is online' : 'the server is not running'}
          >
            <IconRefresh size={16} className={actions.refresh.isPending ? 'animate-spin' : ''} /> check with the game
          </Button>
        )}
        {canBan && (
          <Button onClick={() => setBanning('manual')}>
            <IconBan size={16} /> ban…
          </Button>
        )}
      </div>

      {abilities.bans_by_panel && (
        <p className="rounded-xl bg-sky-950/40 px-3 py-2.5 text-sm text-sky-200">
          this game has no bans of its own: the panel kicks banned players as soon as they join, while it runs.
        </p>
      )}
      {note && <p className="rounded-xl bg-sky-950/40 px-3 py-2.5 text-sm text-sky-200">{note}</p>}
      {actionError && <p className="text-sm text-red-400">{actionError.message}</p>}

      <Section title="online" empty={running ? 'nobody is playing right now.' : 'the server is not running.'}>
        {online.map((player) => (
          <PlayerRow key={player.key} player={player} when={player.online_since && `online ${formatAgo(player.online_since)}`}>
            {abilities.kick && (
              <IconButton
                onClick={() => actions.kick.mutate({ key: player.key })}
                disabled={!running || actions.kick.isPending}
                aria-label="kick"
                title="kick"
              >
                <IconDoorExit size={18} />
              </IconButton>
            )}
            {canBan && (
              <IconButton onClick={() => setBanning(player)} aria-label="ban" title="ban">
                <IconBan size={18} />
              </IconButton>
            )}
          </PlayerRow>
        ))}
      </Section>

      <Section title="seen before" empty="nobody else yet.">
        {offline.map((player) => (
          <PlayerRow key={player.key} player={player} when={`last seen ${formatAgo(player.last_seen)}`}>
            {canBan && (
              <IconButton onClick={() => setBanning(player)} aria-label="ban" title="ban">
                <IconBan size={18} />
              </IconButton>
            )}
          </PlayerRow>
        ))}
      </Section>

      {canBan && (
        <Section title="banned" empty={bansError ? `couldn't read the ban list: ${bansError}` : 'nobody is banned.'}>
          {bans.map((ban) => (
            <li key={`${ban.kind}:${ban.value}`} className="flex items-center gap-3 rounded-2xl bg-panel p-3">
              <IconBan size={20} stroke={1.5} className="shrink-0 text-red-400" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">
                  {ban.name ?? ban.value.replace(/^\w+:/, '')}
                  {ban.kind === 'ip' && <span className="ml-2 text-xs font-normal text-muted">address</span>}
                </div>
                <div className="truncate text-xs text-muted">
                  {ban.name ? ban.value : null}
                  {ban.reason && `${ban.name ? ' · ' : ''}${ban.reason}`}
                </div>
              </div>
              <Button onClick={() => unban(ban)} disabled={actions.unban.isPending}>
                unban
              </Button>
            </li>
          ))}
        </Section>
      )}

      {banning && (
        <BanDialog
          serverId={server.id}
          player={banning === 'manual' ? null : banning}
          abilities={abilities}
          onClose={() => setBanning(null)}
          onDone={(effective) => {
            setBanning(null)
            setNote(EFFECTIVE_NOTE[effective])
          }}
        />
      )}
    </div>
  )
}

function Section({ title, empty, children }: { title: string; empty: string; children: React.ReactNode[] }) {
  return (
    <section>
      <h2 className="mb-3 text-sm font-medium">
        {title} <span className="text-muted">{children.length}</span>
      </h2>
      {children.length === 0 ? <p className="text-sm text-muted">{empty}</p> : <ul className="space-y-2">{children}</ul>}
    </section>
  )
}

function PlayerRow({
  player,
  when,
  children,
}: {
  player: PlayerInfo
  when: string | null
  children: React.ReactNode
}) {
  return (
    <li className="flex items-center gap-3 rounded-2xl bg-panel p-3">
      <span className="relative shrink-0 text-zinc-400">
        <IconUserCircle size={28} stroke={1.3} />
        {player.online && (
          <span className="absolute right-0 bottom-0 size-2.5 rounded-full border-2 border-panel bg-emerald-400" />
        )}
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{displayName(player)}</div>
        <div className="truncate text-xs text-muted" title={`first seen ${formatDate(Date.parse(player.first_seen) / 1000)}`}>
          {[when, player.ip, player.name && player.game_id].filter(Boolean).join(' · ')}
        </div>
      </div>
      <div className="flex shrink-0 gap-1">{children}</div>
    </li>
  )
}

function BanDialog({
  serverId,
  player,
  abilities,
  onClose,
  onDone,
}: {
  serverId: string
  player: PlayerInfo | null
  abilities: PlayerAbilities
  onClose: () => void
  onDone: (effective: Effective) => void
}) {
  const actions = usePlayerActions(serverId)
  const kinds = [
    ...(abilities.ban_by ? [{ value: 'player', label: abilities.ban_by === 'id' ? 'player id' : 'player name' }] : []),
    ...(abilities.ip_bans ? [{ value: 'ip', label: 'ip address' }] : []),
  ]
  const [kind, setKind] = useState<BanKind>(kinds[0].value as BanKind)
  const [value, setValue] = useState('')
  const [reason, setReason] = useState('')
  const [alsoIp, setAlsoIp] = useState(false)
  const [failed, setFailed] = useState<string | null>(null)
  const title = player ? `ban ${displayName(player)}?` : 'ban'

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setFailed(null)
    const extra = reason.trim() ? { reason: reason.trim() } : {}
    try {
      let result
      if (player) {
        if (abilities.ban_by) {
          result = await actions.ban.mutateAsync({ kind: 'player', key: player.key, ...extra })
        }
        if ((alsoIp || !abilities.ban_by) && player.ip && abilities.ip_bans) {
          result = await actions.ban.mutateAsync({ kind: 'ip', key: player.key, ...extra })
        }
      } else {
        result = await actions.ban.mutateAsync({ kind, value: value.trim(), ...extra })
      }
      if (result) onDone(result.effective)
    } catch (err) {
      setFailed((err as Error).message)
    }
  }

  return (
    <Modal title={title} onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        {!player && (
          <>
            {kinds.length > 1 && (
              <Segmented options={kinds} value={kind} onChange={(v) => setKind(v as BanKind)} />
            )}
            <input
              autoFocus
              aria-label="who to ban"
              placeholder={kind === 'ip' ? '203.0.113.9' : abilities.ban_by === 'id' ? 'STEAM_0:1:12345' : 'player name'}
              className={inputClass}
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
          </>
        )}
        <input
          aria-label="reason"
          placeholder="reason (optional)"
          maxLength={200}
          className={inputClass}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        {player?.ip && abilities.ip_bans && abilities.ban_by && (
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={alsoIp} onChange={(e) => setAlsoIp(e.target.checked)} />
            also ban their address {player.ip}
          </label>
        )}
        {abilities.bans_need_restart && (
          <p className="text-xs text-muted">the game reads its ban list when it starts: a ban takes effect after a restart.</p>
        )}
        <FieldError error={failed ?? undefined} />
        <div className="flex gap-2">
          <Button type="button" className="flex-1" onClick={onClose}>
            cancel
          </Button>
          <Button
            type="submit"
            variant="danger"
            className="flex-1"
            disabled={actions.ban.isPending || (!player && !value.trim())}
          >
            ban
          </Button>
        </div>
      </form>
    </Modal>
  )
}
