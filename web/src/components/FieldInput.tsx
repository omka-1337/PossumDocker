import { IconChevronDown, IconExternalLink } from '@tabler/icons-react'
import { useEffect } from 'react'
import { useFieldOptions } from '../api/queries'
import type { FieldValues, Option, SelectField, TemplateField } from '../api/types'
import { dependencyParams } from '../lib/fields'
import { Segmented, Switch } from './ui'

export const inputClass =
  'w-full rounded-xl border-[1.5px] border-line bg-page px-3 py-2.5 text-sm outline-none transition placeholder:text-muted focus:border-zinc-400 disabled:opacity-50'

// Up to this many static options are shown as joined buttons instead of a dropdown.
const SEGMENTED_MAX = 4

interface FieldInputProps {
  templateId: string
  field: TemplateField
  values: FieldValues
  error?: string
  onChange: (value: string | number | boolean) => void
}

export function FieldInput({ templateId, field, values, error, onChange }: FieldInputProps) {
  const value = values[field.id]

  if (field.type === 'boolean') {
    return (
      <div>
        <div className="flex items-center justify-between gap-4 rounded-xl bg-raised px-3 py-2.5">
          <label htmlFor={field.id} className="text-sm lowercase">
            {field.label}
          </label>
          <Switch id={field.id} checked={value === true} onChange={onChange} />
        </div>
        {field.help_url && (
          <a
            href={field.help_url}
            target="_blank"
            rel="noreferrer"
            className="mt-1.5 inline-flex items-center gap-1 text-xs text-muted underline underline-offset-2 hover:text-zinc-200"
          >
            read more <IconExternalLink size={12} />
          </a>
        )}
        <FieldError error={error} />
      </div>
    )
  }

  let input
  switch (field.type) {
    case 'string':
    case 'secret':
      input = (
        <input
          id={field.id}
          type={field.type === 'secret' ? 'password' : 'text'}
          className={inputClass}
          value={String(value ?? '')}
          maxLength={field.type === 'string' ? field.max_length : undefined}
          onChange={(e) => onChange(e.target.value)}
        />
      )
      break
    case 'number':
      input = (
        <input
          id={field.id}
          type="number"
          className={inputClass}
          value={value === '' ? '' : Number(value)}
          min={field.min ?? undefined}
          max={field.max ?? undefined}
          onChange={(e) => onChange(e.target.value === '' ? '' : e.target.valueAsNumber)}
        />
      )
      break
    case 'select':
      if (field.options_from) {
        input = <DynamicSelect templateId={templateId} field={field} values={values} onChange={onChange} />
      } else if (field.options.length <= SEGMENTED_MAX) {
        input = <Segmented options={field.options} value={String(value ?? '')} onChange={onChange} />
      } else {
        input = <Select id={field.id} options={field.options} value={String(value ?? '')} onChange={onChange} />
      }
      break
  }

  return (
    <div>
      <label htmlFor={field.id} className="mb-1.5 block text-sm font-medium lowercase">
        {field.label}
      </label>
      {input}
      {field.help && <p className="mt-1.5 text-xs text-muted">{field.help}</p>}
      <FieldError error={error} />
    </div>
  )
}

export function FieldError({ error }: { error?: string }) {
  return error ? <p className="mt-1.5 text-xs text-red-400">{error}</p> : null
}

export function Select({
  id,
  options,
  value,
  disabled,
  placeholder,
  onChange,
}: {
  id: string
  options: Option[]
  value: string
  disabled?: boolean
  placeholder?: string
  onChange: (value: string) => void
}) {
  return (
    <div className="relative">
      <select
        id={id}
        className={`${inputClass} appearance-none pr-9`}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      >
        {placeholder !== undefined && <option value="">{placeholder}</option>}
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <IconChevronDown size={16} className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-muted" />
    </div>
  )
}

/** A select whose options come from the panel and depend on other fields (e.g. versions per loader). */
function DynamicSelect({
  templateId,
  field,
  values,
  onChange,
}: {
  templateId: string
  field: SelectField
  values: FieldValues
  onChange: (value: string) => void
}) {
  const params = dependencyParams(field, values)
  const ready = Object.values(params).every((v) => v !== '')
  const { data: options, isFetching, isError } = useFieldOptions(templateId, field.id, params, ready)
  const value = String(values[field.id] ?? '')

  // When the list changes (another loader picked) and the current value isn't in it,
  // fall back to the default or the first option (the newest version).
  useEffect(() => {
    if (!options || options.some((o) => o.value === value)) return
    const fallback = options.find((o) => o.value === field.default)?.value ?? options[0]?.value ?? ''
    // Without this check an empty list would call onChange('') on every render, forever.
    if (fallback !== value) onChange(fallback)
  }, [options, value, field.default, onChange])

  if (isError) {
    return <p className="text-sm text-red-400">could not load options</p>
  }
  return (
    <Select
      id={field.id}
      options={options ?? []}
      value={value}
      disabled={!ready || isFetching}
      placeholder={isFetching ? 'loading…' : options?.length === 0 ? 'nothing available' : undefined}
      onChange={onChange}
    />
  )
}
