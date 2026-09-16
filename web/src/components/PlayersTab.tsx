import { IconBan, IconChevronDown, IconDoorExit, IconRefresh, IconUserCircle } from '@tabler/icons-react'
import { useEffect, useState, type FormEvent } from 'react'
import {
  avatarUrl,
  usePlayerActions,
  usePlayers,
  type BanInfo,
  type BanKind,
  type Effective,
  type PlayerAbilities,
  type PlayerInfo,
} from '../api/players'
import type { Server } from '../api/types'
import { formatAgo, formatDate, formatMoment } from '../lib/format'
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
          <PlayerRow
            key={player.key}
            serverId={server.id}
            avatars={abilities.avatars}
            player={player}
            when={player.online_since && `joined ${formatMoment(player.online_since)}`}
          >
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
          <PlayerRow
            key={player.key}
            serverId={server.id}
            avatars={abilities.avatars}
            player={player}
            when={`last seen ${formatAgo(player.last_seen)}`}
          >
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
            <BanRow
              key={`${ban.kind}:${ban.value}`}
              ban={ban}
              onUnban={() => unban(ban)}
              unbanning={actions.unban.isPending}
            />
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
  serverId,
  avatars,
  player,
  when,
  children,
}: {
  serverId: string
  avatars: PlayerAbilities['avatars']
  player: PlayerInfo
  when: string | null
  children: React.ReactNode
}) {
  return (
    <li className="flex items-center gap-3 rounded-2xl bg-panel p-3">
      <span className="relative shrink-0 text-zinc-400">
        <Avatar serverId={serverId} avatars={player.game_id ? avatars : null} player={player} />
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

const AVATAR_PX = 32

/**
 * The player's Steam avatar or Minecraft face, fetched through the panel; a plain icon until it loads
 * or when there is none. A Minecraft skin is the whole texture: the face is its 8×8 square at (8, 8),
 * with the hat layer at (40, 8) on top.
 */
function Avatar({
  serverId,
  avatars,
  player,
}: {
  serverId: string
  avatars: PlayerAbilities['avatars']
  player: PlayerInfo
}) {
  const url = avatars ? avatarUrl(serverId, player.key) : null
  const [loaded, setLoaded] = useState<{ url: string; hat: boolean } | null>(null)

  useEffect(() => {
    if (!url) return
    const image = new Image()
    image.onload = () => setLoaded({ url, hat: avatars !== 'minecraft' || hatIsDrawn(image) })
    image.src = url
    return () => {
      image.onload = null
    }
  }, [url, avatars])

  if (!url || loaded?.url !== url) return <IconUserCircle size={AVATAR_PX} stroke={1.3} />
  if (avatars === 'steam') {
    return <img src={url} alt="" className="size-8 rounded-full object-cover" draggable={false} />
  }
  const scale = AVATAR_PX / 8
  const layer = (x: number) => `${-x * scale}px ${-8 * scale}px`
  return (
    <span
      aria-hidden
      className="block size-8 rounded-md"
      style={{
        backgroundImage: loaded.hat ? `url("${url}"), url("${url}")` : `url("${url}")`,
        backgroundPosition: loaded.hat ? `${layer(40)}, ${layer(8)}` : layer(8),
        backgroundSize: `${64 * scale}px auto`,
        backgroundRepeat: 'no-repeat',
        imageRendering: 'pixelated',
      }}
    />
  )
}

/**
 * Old 64×32 skins often fill the hat square with a solid colour; Minecraft draws the hat only when some
 * of it is see-through, and so do we.
 */
function hatIsDrawn(image: HTMLImageElement): boolean {
  try {
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 8
    const context = canvas.getContext('2d')
    if (!context) return false
    context.drawImage(image, 40, 8, 8, 8, 0, 0, 8, 8)
    const alpha = context.getImageData(0, 0, 8, 8).data.filter((_, i) => i % 4 === 3)
    return alpha.some((a) => a < 255) && alpha.some((a) => a > 0)
  } catch {
    return false
  }
}

/** A banned player or address; a click shows why, since when and until when. */
function BanRow({ ban, onUnban, unbanning }: { ban: BanInfo; onUnban: () => void; unbanning: boolean }) {
  const [open, setOpen] = useState(false)
  const title = ban.name ?? ban.value.replace(/^\w+:/, '')
  return (
    <li className="rounded-2xl bg-panel">
      <div className="flex items-center gap-3 p-3">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-3 text-left"
        >
          <IconBan size={20} stroke={1.5} className="shrink-0 text-red-400" />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium">
              {title}
              {ban.kind === 'ip' && <span className="ml-2 text-xs font-normal text-muted">address</span>}
            </span>
            <span className="block truncate text-xs text-muted">
              {ban.expires_at ? `until ${formatMoment(ban.expires_at)}` : 'permanent'}
              {ban.reason && ` · ${ban.reason}`}
            </span>
          </span>
          <IconChevronDown size={18} className={`shrink-0 text-muted transition ${open ? 'rotate-180' : ''}`} />
        </button>
        <Button onClick={onUnban} disabled={unbanning}>
          unban
        </Button>
      </div>
      {open && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 border-t border-line-soft px-4 py-3 text-sm">
          {ban.name && (
            <>
              <dt className="text-muted">{ban.kind === 'ip' ? 'address' : 'id'}</dt>
              <dd className="break-all">{ban.value}</dd>
            </>
          )}
          <dt className="text-muted">reason</dt>
          <dd className={ban.reason ? '' : 'text-muted'}>{ban.reason ?? 'not given'}</dd>
          <dt className="text-muted">banned</dt>
          <dd className={ban.banned_at ? '' : 'text-muted'}>
            {ban.banned_at ? formatDate(Date.parse(ban.banned_at) / 1000) : 'unknown: not through the panel'}
            {ban.banned_by && ` by ${ban.banned_by}`}
          </dd>
          <dt className="text-muted">until</dt>
          <dd>
            {ban.expires_at
              ? `${formatDate(Date.parse(ban.expires_at) / 1000)} (${formatAgo(ban.expires_at)})`
              : 'never: permanent'}
          </dd>
        </dl>
      )}
    </li>
  )
}

// Minutes; null: permanent.
const DURATIONS: { value: string; label: string; minutes: number | null }[] = [
  { value: 'permanent', label: 'permanent', minutes: null },
  { value: '1h', label: '1 hour', minutes: 60 },
  { value: '1d', label: '1 day', minutes: 60 * 24 },
  { value: '7d', label: '7 days', minutes: 60 * 24 * 7 },
  { value: '30d', label: '30 days', minutes: 60 * 24 * 30 },
  { value: 'custom', label: 'custom', minutes: null },
]

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
  const [duration, setDuration] = useState('permanent')
  const [customAmount, setCustomAmount] = useState('3')
  const [customUnit, setCustomUnit] = useState<'hours' | 'days'>('days')
  const [failed, setFailed] = useState<string | null>(null)
  const title = player ? `ban ${displayName(player)}?` : 'ban'

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setFailed(null)
    const preset = DURATIONS.find((d) => d.value === duration)!
    const minutes =
      duration === 'custom'
        ? Math.round(Number(customAmount) * (customUnit === 'days' ? 60 * 24 : 60))
        : preset.minutes
    const extra = { ...(reason.trim() ? { reason: reason.trim() } : {}), ...(minutes ? { minutes } : {}) }
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
        <div>
          <label htmlFor="ban-duration" className="mb-1.5 block text-sm font-medium">
            for how long
          </label>
          <select
            id="ban-duration"
            className={inputClass}
            value={duration}
            onChange={(e) => setDuration(e.target.value)}
          >
            {DURATIONS.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </select>
          {duration === 'custom' && (
            <div className="mt-2 flex gap-2">
              <input
                type="number"
                min={1}
                aria-label="how many"
                className={inputClass}
                value={customAmount}
                onChange={(e) => setCustomAmount(e.target.value)}
              />
              <select
                aria-label="unit"
                className={inputClass}
                value={customUnit}
                onChange={(e) => setCustomUnit(e.target.value as 'hours' | 'days')}
              >
                <option value="hours">hours</option>
                <option value="days">days</option>
              </select>
            </div>
          )}
          {duration !== 'permanent' && (
            <p className="mt-1.5 text-xs text-muted">the panel lifts the ban when the time is up.</p>
          )}
        </div>
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
            disabled={
              actions.ban.isPending ||
              (!player && !value.trim()) ||
              (duration === 'custom' && !(Number(customAmount) > 0))
            }
          >
            ban
          </Button>
        </div>
      </form>
    </Modal>
  )
}
