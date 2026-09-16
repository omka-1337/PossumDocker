import { useState, type FormEvent } from 'react'
import { ApiError } from '../api/client'
import { useCreateServer, useTemplate } from '../api/queries'
import type { FieldValues, Server, TemplateDetail } from '../api/types'
import { initialValues, isVisible, valuesToSubmit } from '../lib/fields'
import { FieldError, FieldInput, inputClass } from './FieldInput'
import { Button } from './ui'

interface Props {
  templateId: string
  onCreated: (server: Server) => void
}

export function TemplateForm({ templateId, onCreated }: Props) {
  const { data: template, isPending, isError } = useTemplate(templateId)

  if (isPending) return <p className="text-sm text-muted">loading…</p>
  if (isError) return <p className="text-sm text-red-400">could not load the game template</p>

  // `key` resets the form state if a different template is opened.
  return <Form key={template.id} template={template} onCreated={onCreated} />
}

function Form({ template, onCreated }: { template: TemplateDetail; onCreated: Props['onCreated'] }) {
  const [name, setName] = useState('')
  const [values, setValues] = useState<FieldValues>(() => initialValues(template.fields))
  // Empty: the game's default.
  const [disk, setDisk] = useState('')
  const createServer = useCreateServer()

  const fieldErrors = createServer.error instanceof ApiError ? createServer.error.fieldErrors : {}
  const visibleFields = template.fields.filter((f) => isVisible(f, values))
  // e.g. the EULA switch: don't let the user submit until it's on.
  const mustBeUnmet = visibleFields.some(
    (f) => f.type === 'boolean' && f.must_be !== null && values[f.id] !== f.must_be,
  )

  const setValue = (id: string, value: string | number | boolean) =>
    setValues((prev) => ({ ...prev, [id]: value }))

  const submit = (e: FormEvent) => {
    e.preventDefault()
    createServer.mutate(
      {
        template_id: template.id,
        name,
        values: valuesToSubmit(template.fields, values),
        ...(disk.trim() !== '' && { disk_limit_mb: Number(disk) }),
      },
      { onSuccess: onCreated },
    )
  }

  return (
    <form onSubmit={submit} className="space-y-5">
      <div>
        <label htmlFor="server-name" className="mb-1.5 block text-sm font-medium">
          server name
        </label>
        <input
          id="server-name"
          autoFocus
          placeholder="my awesome server"
          className={inputClass}
          value={name}
          maxLength={64}
          onChange={(e) => setName(e.target.value)}
        />
        <FieldError error={fieldErrors.name} />
      </div>

      {visibleFields.map((field) => (
        <FieldInput
          key={field.id}
          templateId={template.id}
          field={field}
          values={values}
          error={fieldErrors[field.id]}
          onChange={(value) => setValue(field.id, value)}
        />
      ))}

      <div>
        <label htmlFor="server-disk" className="mb-1.5 block text-sm font-medium">
          disk limit (MB)
        </label>
        <input
          id="server-disk"
          type="number"
          min={0}
          step={1024}
          placeholder="the game's default"
          className={inputClass}
          value={disk}
          onChange={(e) => setDisk(e.target.value)}
        />
        <p className="mt-1.5 text-xs text-muted">0 for no limit. can be raised later in settings.</p>
        <FieldError error={fieldErrors.disk_limit_mb} />
      </div>

      {template.ports.length > 0 && (
        <p className="text-xs text-muted">
          ports: {template.ports.map((p) => `${p.default_host}/${p.protocol}`).join(', ')}
        </p>
      )}

      {createServer.error && Object.keys(fieldErrors).length === 0 && (
        <p className="text-sm text-red-400">{createServer.error.message}</p>
      )}

      <Button
        type="submit"
        variant="primary"
        className="w-full"
        disabled={!name.trim() || mustBeUnmet || createServer.isPending}
      >
        {createServer.isPending ? 'creating…' : 'create server'}
      </Button>
    </form>
  )
}
