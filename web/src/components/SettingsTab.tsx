import { IconRefresh } from '@tabler/icons-react'
import { useState, type FormEvent, type ReactNode } from 'react'
import { ApiError } from '../api/client'
import { useConfigs, useServerAction, useTemplate, useUpdateServer } from '../api/queries'
import type { FieldValues, Server, TemplateDetail, TemplateField } from '../api/types'
import { initialValues, isVisible } from '../lib/fields'
import { ConfigEditor } from './ConfigEditor'
import { FieldError, FieldInput, inputClass } from './FieldInput'
import { Button } from './ui'

/** Everything that can be changed after creation: the game's own fields, then its config files. */
export function SettingsTab({ server }: { server: Server }) {
  const { data: template } = useTemplate(server.template_id)
  const { data: configs = [] } = useConfigs(server.id)
  const [restartNeeded, setRestartNeeded] = useState(false)
  const restart = useServerAction(server.id)
  const running = server.status === 'running' || server.status === 'starting'

  return (
    <div className="space-y-10 pb-24">
      {restartNeeded && running && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-sky-950/40 px-3 py-2.5 text-sm text-sky-200">
          saved. the server uses the new settings after a restart.
          <Button
            onClick={() => restart.mutate('restart', { onSuccess: () => setRestartNeeded(false) })}
            disabled={restart.isPending}
          >
            <IconRefresh size={16} /> restart now
          </Button>
        </div>
      )}

      <Section title="game">
        {template ? (
          // Remount after a save so the form starts from the saved values.
          <GameSettings
            key={`${server.name}|${JSON.stringify(server.values)}`}
            server={server}
            template={template}
            onRestartNeeded={() => setRestartNeeded(true)}
          />
        ) : (
          <p className="text-sm text-muted">loading…</p>
        )}
      </Section>

      {configs.map((config) => (
        <Section key={config.id} title={config.label} subtitle={config.path}>
          <ConfigEditor server={server} configId={config.id} onRestartNeeded={() => setRestartNeeded(true)} />
        </Section>
      ))}
    </div>
  )
}

function Section({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  return (
    <section>
      <h2 className="mb-4 flex items-baseline gap-2 border-b border-line-soft pb-2 font-semibold lowercase">
        {title}
        {subtitle && subtitle.toLowerCase() !== title.toLowerCase() && (
          <span className="text-xs font-normal text-muted">{subtitle}</span>
        )}
      </h2>
      {children}
    </section>
  )
}

const effectNote: Record<TemplateField['on_change'], string | null> = {
  none: null,
  restart: 'applies after a restart',
  reinstall: 'reinstalls the game files',
}

function GameSettings({
  server,
  template,
  onRestartNeeded,
}: {
  server: Server
  template: TemplateDetail
  onRestartNeeded: () => void
}) {
  // The saved values, with defaults for fields added to the template after this server was created.
  const saved: FieldValues = { ...initialValues(template.fields), ...server.values }
  const [name, setName] = useState(server.name)
  const [values, setValues] = useState<FieldValues>(saved)
  const update = useUpdateServer(server.id)

  const editable = template.fields.filter((f) => f.editable && isVisible(f, values))
  const changedValues = Object.fromEntries(editable.filter((f) => values[f.id] !== saved[f.id]).map((f) => [f.id, values[f.id]]))
  const dirty = name.trim() !== server.name || Object.keys(changedValues).length > 0
  const fieldErrors = update.error instanceof ApiError ? update.error.fieldErrors : {}

  const submit = (e: FormEvent) => {
    e.preventDefault()
    update.mutate(
      { name: name.trim(), values: changedValues },
      { onSuccess: (result) => result.restart_required && onRestartNeeded() },
    )
  }

  return (
    <form onSubmit={submit} className="space-y-5">
      <div>
        <label htmlFor="settings-name" className="mb-1.5 block text-sm font-medium">
          server name
        </label>
        <input
          id="settings-name"
          className={inputClass}
          value={name}
          maxLength={64}
          onChange={(e) => setName(e.target.value)}
        />
        <FieldError error={fieldErrors.name} />
      </div>

      {editable.map((field) => (
        <div key={field.id}>
          <FieldInput
            templateId={template.id}
            field={field}
            values={values}
            error={fieldErrors[field.id]}
            onChange={(value) => setValues((prev) => ({ ...prev, [field.id]: value }))}
          />
          {effectNote[field.on_change] && values[field.id] !== saved[field.id] && (
            <p className="mt-1.5 text-xs text-sky-300">{effectNote[field.on_change]}</p>
          )}
        </div>
      ))}

      {update.error && Object.keys(fieldErrors).length === 0 && (
        <p className="text-sm text-red-400">{update.error.message}</p>
      )}

      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={!dirty || !name.trim() || update.isPending}>
          {update.isPending ? 'saving…' : 'save'}
        </Button>
        {dirty && (
          <Button
            type="button"
            onClick={() => {
              setName(server.name)
              setValues(saved)
              update.reset()
            }}
          >
            discard
          </Button>
        )}
      </div>
    </form>
  )
}
