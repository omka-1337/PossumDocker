import { IconDeviceGamepad2 } from '@tabler/icons-react'
import { useState } from 'react'
import type { TemplateSummary } from '../api/types'

const sizes = {
  sm: { tile: 'size-10 rounded-lg', image: 'size-7', fallback: 22 },
  lg: { tile: 'size-14 rounded-xl', image: 'size-10', fallback: 30 },
}

/** The game's icon on its colour tile; a gamepad when the template has no icon (or it fails to load). */
export function GameIcon({ template, size = 'sm' }: { template?: TemplateSummary; size?: keyof typeof sizes }) {
  const [broken, setBroken] = useState(false)
  const s = sizes[size]
  const src = !broken ? template?.icon_url : null

  return (
    <span
      className={`grid shrink-0 place-items-center ${s.tile} ${template?.color ? '' : 'bg-page'}`}
      style={template?.color ? { backgroundColor: template.color } : undefined}
    >
      {src ? (
        <img src={src} alt="" className={`${s.image} object-contain`} onError={() => setBroken(true)} draggable={false} />
      ) : (
        <IconDeviceGamepad2 size={s.fallback} stroke={1.5} />
      )}
    </span>
  )
}
