import type { FieldValues, SelectField, TemplateField } from '../api/types'

// Same rules as panel/app/games/validation.py: the backend re-checks everything anyway.

export function isVisible(field: TemplateField, values: FieldValues): boolean {
  if (!field.visible_if) return true
  return Object.entries(field.visible_if).every(([ref, allowed]) => allowed.includes(values[ref]))
}

export function initialValues(fields: TemplateField[]): FieldValues {
  const values: FieldValues = {}
  for (const field of fields) {
    if (field.type === 'secret') continue
    values[field.id] = field.default ?? ''
  }
  return values
}

export function dependencyParams(field: SelectField, values: FieldValues): Record<string, string> {
  return Object.fromEntries(field.depends_on.map((ref) => [ref, String(values[ref] ?? '')]))
}

/** Only what the user can actually see, without empty inputs (the backend applies defaults). */
export function valuesToSubmit(fields: TemplateField[], values: FieldValues): FieldValues {
  const result: FieldValues = {}
  for (const field of fields) {
    const value = values[field.id]
    if (isVisible(field, values) && value !== '' && value !== undefined) {
      result[field.id] = value
    }
  }
  return result
}
