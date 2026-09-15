import { useState } from 'react'
import { useTemplates } from '../api/queries'
import type { Server } from '../api/types'
import { GameCover } from './GameIcon'
import { TemplateForm } from './TemplateForm'
import { Modal } from './ui'

interface Props {
  onClose: () => void
  onCreated: (server: Server) => void
}

/** Step 1: pick a game. Step 2: fill in the form generated from its template. */
export function CreateServerDialog({ onClose, onCreated }: Props) {
  const [templateId, setTemplateId] = useState<string | null>(null)
  const { data: templates, isPending, isError } = useTemplates()
  const selected = templates?.find((t) => t.id === templateId)

  return (
    <Modal
      title={selected ? selected.name : 'choose a game'}
      onClose={onClose}
      onBack={templateId ? () => setTemplateId(null) : undefined}
    >
      {templateId ? (
        <TemplateForm templateId={templateId} onCreated={onCreated} />
      ) : isPending ? (
        <p className="text-sm text-muted">loading…</p>
      ) : isError ? (
        <p className="text-sm text-red-400">could not load the list of games</p>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {templates.map((t) => (
            <li key={t.id}>
              <button
                onClick={() => setTemplateId(t.id)}
                className="group w-full overflow-hidden rounded-xl bg-raised text-left ring-sky-400/0 transition hover:bg-raised-hover hover:ring-2 hover:ring-zinc-500 active:scale-[0.99]"
              >
                <GameCover template={t} />
                <span className="block px-3 py-2">
                  <span className="block truncate text-sm font-medium lowercase">{t.name}</span>
                  {t.description && <span className="block truncate text-xs text-muted">{t.description}</span>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  )
}
