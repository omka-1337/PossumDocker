import { IconDeviceGamepad2 } from '@tabler/icons-react'
import { useState } from 'react'
import type { TemplateSummary } from '../api/types'

const sizes = {
  sm: { tile: 'size-10 rounded-lg', image: 'size-7', fallback: 22 },
  lg: { tile: 'size-14 rounded-xl', image: 'size-10', fallback: 30 },
}

/**
 * The game's icon: Steam's (fills the tile), else the template's own on its colour tile,
 * else a gamepad. Each step is tried when the previous one fails to load.
 */
export function GameIcon({ template, size = 'sm' }: { template?: TemplateSummary; size?: keyof typeof sizes }) {
  const [failed, setFailed] = useState<string[]>([])
  const s = sizes[size]
  const steam = template?.steam_icon_url
  const local = template?.icon_url
  const fail = (url: string) => setFailed((f) => [...f, url])

  if (steam && !failed.includes(steam)) {
    return (
      <img
        src={steam}
        alt=""
        draggable={false}
        onError={() => fail(steam)}
        className={`${s.tile} shrink-0 bg-panel object-cover`}
      />
    )
  }
  return (
    <span
      className={`grid shrink-0 place-items-center ${s.tile} ${template?.color ? '' : 'bg-page'}`}
      style={template?.color ? { backgroundColor: template.color } : undefined}
    >
      {local && !failed.includes(local) ? (
        <img src={local} alt="" draggable={false} onError={() => fail(local)} className={`${s.image} object-contain`} />
      ) : (
        <IconDeviceGamepad2 size={s.fallback} stroke={1.5} />
      )}
    </span>
  )
}

/** Wide cover for the game picker: Steam art, or the icon large on the game's colour. */
export function GameCover({ template }: { template: TemplateSummary }) {
  const [failed, setFailed] = useState(false)
  const cover = template.cover_url

  if (cover && !failed) {
    return (
      <img
        src={cover}
        alt=""
        draggable={false}
        onError={() => setFailed(true)}
        className="aspect-[460/215] w-full bg-page object-cover"
      />
    )
  }
  return (
    <span
      className="grid aspect-[460/215] w-full place-items-center bg-page"
      style={template.color ? { backgroundColor: template.color } : undefined}
    >
      {template.icon_url ? (
        <img src={template.icon_url} alt="" draggable={false} className="h-3/5 object-contain" />
      ) : (
        <IconDeviceGamepad2 size={40} stroke={1.5} />
      )}
    </span>
  )
}
