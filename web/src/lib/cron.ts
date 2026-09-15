// The schedule editor offers presets; anything else is kept as raw cron ("custom").

export type Preset =
  | { kind: 'hourly'; every: number }
  | { kind: 'daily'; time: string }
  | { kind: 'weekly'; day: number; time: string }
  | { kind: 'custom'; cron: string }

export const HOUR_STEPS = [1, 2, 3, 4, 6, 12]
export const DAYS = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday']

const pad = (n: number) => String(n).padStart(2, '0')

export function toCron(preset: Preset): string {
  const [hour, minute] = 'time' in preset ? preset.time.split(':').map(Number) : [0, 0]
  switch (preset.kind) {
    case 'hourly':
      return preset.every === 1 ? '0 * * * *' : `0 */${preset.every} * * *`
    case 'daily':
      return `${minute} ${hour} * * *`
    case 'weekly':
      return `${minute} ${hour} * * ${preset.day}`
    case 'custom':
      return preset.cron.trim()
  }
}

export function fromCron(cron: string): Preset {
  const fields = cron.trim().split(/\s+/)
  if (fields.length === 5) {
    const [minute, hour, dom, month, dow] = fields
    const isNum = (v: string, max: number) => /^\d+$/.test(v) && Number(v) <= max
    const hourly = hour === '*' ? 1 : hour.match(/^\*\/(\d+)$/)?.[1]
    if (minute === '0' && dom === '*' && month === '*' && dow === '*' && hourly && HOUR_STEPS.includes(Number(hourly))) {
      return { kind: 'hourly', every: Number(hourly) }
    }
    if (isNum(minute, 59) && isNum(hour, 23) && dom === '*' && month === '*') {
      const time = `${pad(Number(hour))}:${pad(Number(minute))}`
      if (dow === '*') return { kind: 'daily', time }
      if (isNum(dow, 6)) return { kind: 'weekly', day: Number(dow), time }
    }
  }
  return { kind: 'custom', cron }
}
