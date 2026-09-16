import { useState } from 'react'
import { Link } from 'react-router'
import { useTemplates } from '../api/queries'
import type { Server, TemplateSummary } from '../api/types'
import { GameCover } from './GameIcon'
import { TemplateForm } from './TemplateForm'
import { HELP_GAME_TEMPLATES } from '../pages/HelpRoute'
import { Modal, Segmented } from './ui'

interface Props {
  onClose: () => void
  onCreated: (server: Server) => void
}

/** One tile in the picker: a game, or the editions of one (Minecraft Java and Bedrock). */
interface Game {
  key: string
  name: string
  description: string | null
  // Sorted by group order; the first is picked by default and gives the tile its art.
  templates: TemplateSummary[]
}

function games(templates: TemplateSummary[]): Game[] {
  const result: Game[] = []
  for (const t of templates) {
    const existing = t.group && result.find((g) => g.key === `group:${t.group!.id}`)
    if (existing) {
      existing.templates.push(t)
      existing.templates.sort((a, b) => a.group!.order - b.group!.order)
      existing.description = existing.templates.map((e) => e.group!.variant).join(' · ')
    } else if (t.group) {
      result.push({ key: `group:${t.group.id}`, name: t.group.name, description: t.group.variant, templates: [t] })
    } else {
      result.push({ key: t.id, name: t.name, description: t.description, templates: [t] })
    }
  }
  return result
}

/** Step 1: pick a game. Step 2: fill in the form generated from its template. */
export function CreateServerDialog({ onClose, onCreated }: Props) {
  const [gameKey, setGameKey] = useState<string | null>(null)
  const [templateId, setTemplateId] = useState<string | null>(null)
  const { data: templates, isPending, isError } = useTemplates()
  const list = templates ? games(templates) : []
  const game = list.find((g) => g.key === gameKey)
  const selected = game?.templates.find((t) => t.id === templateId) ?? game?.templates[0]

  const back = () => {
    setGameKey(null)
    setTemplateId(null)
  }

  return (
    <Modal title={game ? game.name : 'choose a game'} onClose={onClose} onBack={game ? back : undefined}>
      {game && selected ? (
        <>
          {game.templates.length > 1 && (
            <div className="mb-5">
              <Segmented
                options={game.templates.map((t) => ({ value: t.id, label: t.group!.variant }))}
                value={selected.id}
                onChange={setTemplateId}
              />
              {selected.description && <p className="mt-2 text-xs text-muted">{selected.description}</p>}
            </div>
          )}
          {/* A different edition is a different form: start it from scratch. */}
          <TemplateForm key={selected.id} templateId={selected.id} onCreated={onCreated} />
        </>
      ) : isPending ? (
        <p className="text-sm text-muted">loading…</p>
      ) : isError ? (
        <p className="text-sm text-red-400">could not load the list of games</p>
      ) : (
        <>
          <ul className="grid gap-3 sm:grid-cols-2">
            {list.map((g) => (
              <li key={g.key}>
                <button
                  onClick={() => setGameKey(g.key)}
                  className="group w-full overflow-hidden rounded-xl bg-raised text-left ring-sky-400/0 transition hover:bg-raised-hover hover:ring-2 hover:ring-zinc-500 active:scale-[0.99]"
                >
                  <GameCover template={g.templates[0]} />
                  <span className="block px-3 py-2">
                    <span className="block truncate text-sm font-medium lowercase">{g.name}</span>
                    {g.description && <span className="block truncate text-xs text-muted">{g.description}</span>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-5 text-center text-xs text-muted">
            don&apos;t see the game you need?{' '}
            <Link
              to={HELP_GAME_TEMPLATES}
              onClick={onClose}
              className="text-zinc-300 underline underline-offset-2 hover:text-white"
            >
              read how to add it
            </Link>
          </p>
        </>
      )}
    </Modal>
  )
}
