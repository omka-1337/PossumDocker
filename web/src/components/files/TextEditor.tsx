import { IconDeviceFloppy, IconX } from '@tabler/icons-react'
import { useEffect, useState } from 'react'
import { readText, writeText } from '../../api/files'
import { Button, IconButton } from '../ui'

interface Props {
  serverId: string
  path: string
  onClose: () => void
}

export function TextEditor({ serverId, path, onClose }: Props) {
  const [original, setOriginal] = useState<string | null>(null)
  const [content, setContent] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const dirty = original !== null && content !== original

  useEffect(() => {
    readText(serverId, path)
      .then((file) => {
        setOriginal(file.content)
        setContent(file.content)
      })
      .catch((e: Error) => setError(e.message))
  }, [serverId, path])

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      await writeText(serverId, path, content)
      setOriginal(content)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const close = () => {
    if (!dirty || confirm('discard unsaved changes?')) onClose()
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-2 backdrop-blur-sm sm:p-6">
      <div
        role="dialog"
        aria-modal="true"
        className="flex h-full max-h-[52rem] w-full max-w-5xl flex-col rounded-2xl border border-line-soft bg-panel shadow-2xl"
        onKeyDown={(e) => {
          if ((e.ctrlKey || e.metaKey) && e.key === 's') {
            e.preventDefault()
            if (dirty && !saving) save()
          }
          if (e.key === 'Escape') close()
        }}
      >
        <div className="flex items-center gap-3 border-b border-line-soft px-4 py-2.5">
          <span className="min-w-0 flex-1 truncate text-sm">
            {path}
            {dirty && <span className="ml-2 text-sky-400">●</span>}
          </span>
          {error && original !== null && <span className="text-xs text-red-400">{error}</span>}
          <Button variant="primary" onClick={save} disabled={!dirty || saving}>
            <IconDeviceFloppy size={16} /> {saving ? 'saving…' : 'save'}
          </Button>
          <IconButton onClick={close} aria-label="close">
            <IconX size={18} />
          </IconButton>
        </div>
        {original === null ? (
          <p className={`p-4 text-sm ${error ? 'text-red-400' : 'text-muted'}`}>{error ?? 'loading…'}</p>
        ) : (
          <textarea
            autoFocus
            spellCheck={false}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            className="flex-1 resize-none rounded-b-2xl bg-frame p-4 font-mono text-[13px] leading-relaxed text-zinc-200 outline-none"
          />
        )}
      </div>
    </div>
  )
}
