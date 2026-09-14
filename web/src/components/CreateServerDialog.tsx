import { IconChevronRight, IconDeviceGamepad2 } from '@tabler/icons-react'
import { useState } from 'react'
import { useTemplates } from '../api/queries'
import type { Server } from '../api/types'
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
        <ul className="space-y-2">
          {templates.map((t) => (
            <li key={t.id}>
              <button
                onClick={() => setTemplateId(t.id)}
                className="group flex w-full items-center gap-3 rounded-xl bg-raised p-3 text-left transition hover:bg-raised-hover active:scale-[0.99]"
              >
                <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-page">
                  <IconDeviceGamepad2 size={22} stroke={1.5} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium lowercase">{t.name}</span>
                  {t.description && <span className="block truncate text-xs text-muted">{t.description}</span>}
                </span>
                <IconChevronRight size={18} className="text-muted transition group-hover:translate-x-0.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  )
}
