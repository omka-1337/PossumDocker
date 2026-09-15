// Mirrors the panel's Pydantic models (panel/app/games/schema.py, panel/app/api/*.py).
// TODO: generate from /openapi.json once openapi-typescript supports TypeScript 6.

export interface Option {
  value: string
  label: string
}

interface BaseField {
  id: string
  label: string
  help: string | null
  help_url: string | null
  required: boolean
  visible_if: Record<string, unknown[]> | null
  editable: boolean
  on_change: 'none' | 'restart' | 'reinstall'
}

export interface StringField extends BaseField {
  type: 'string'
  default: string | null
  min_length: number
  max_length: number
  pattern: string | null
}

export interface NumberField extends BaseField {
  type: 'number'
  default: number | null
  min: number | null
  max: number | null
}

export interface BooleanField extends BaseField {
  type: 'boolean'
  default: boolean
  must_be: boolean | null
}

export interface SelectField extends BaseField {
  type: 'select'
  default: string | null
  options: Option[]
  options_from: string | null
  depends_on: string[]
}

export interface SecretField extends BaseField {
  type: 'secret'
  generate: boolean
  length: number
  hidden: boolean
}

// A "discriminated union": checking `field.type` tells TypeScript which fields exist.
export type TemplateField = StringField | NumberField | BooleanField | SelectField | SecretField

export interface Port {
  name: string
  container: number
  protocol: 'tcp' | 'udp'
  default_host: number
}

export interface TemplateSummary {
  id: string
  name: string
  description: string | null
  // Square icon from Steam (may fail to load → fall back to icon_url)
  steam_icon_url: string | null
  // Icon bundled with the template; null: the UI shows a generic one
  icon_url: string | null
  // Wide cover art, 460×215
  cover_url: string | null
  // Icon tile background, e.g. "#c9862e"
  color: string | null
}

export type HighlightColor = 'red' | 'yellow' | 'green' | 'blue' | 'magenta' | 'cyan' | 'gray'

export interface ConsoleSpec {
  // First matching rule colours the whole line
  highlight: { pattern: string; color: HighlightColor }[]
  // Lines continuing the previous record (stack traces) keep its colour
  continuation: string | null
}

export interface TemplateDetail extends TemplateSummary {
  fields: TemplateField[]
  ports: Port[]
  console: ConsoleSpec
}

export type ServerStatus =
  | 'pending'
  | 'installing'
  | 'install_failed'
  | 'stopped'
  | 'starting'
  | 'running'
  | 'stopping'
  | 'unknown' // Docker is unreachable

/** Statuses that change on their own, so the UI keeps polling while in them. */
export const TRANSITIONAL: ServerStatus[] = ['installing', 'starting', 'stopping']

export type FieldValues = Record<string, string | number | boolean>

export interface Server {
  id: string
  name: string
  template_id: string
  values: FieldValues
  // Host port per template port name: { game: 25565 }
  ports: Record<string, number>
  status: ServerStatus
  // Why the last install failed.
  status_message: string | null
  created_at: string
}

export interface ServerCreate {
  template_id: string
  name: string
  values: FieldValues
}

// Config files (panel/app/api/configs.py)

export interface ConfigSummary {
  id: string
  label: string
  path: string
}

export interface ConfigHint {
  label: string
  type: 'string' | 'number' | 'boolean' | 'select'
  help: string | null
  options: string[]
  min: number | null
  max: number | null
  true_value: string
  false_value: string
}

export interface ConfigEntry {
  key: string
  value: string
  hint: ConfigHint | null
  // Set by the panel (ports, RCON): read-only.
  managed: boolean
}

export interface ConfigRead extends ConfigSummary {
  // False until the game writes the file, usually on its first start.
  exists: boolean
  entries: ConfigEntry[]
}
