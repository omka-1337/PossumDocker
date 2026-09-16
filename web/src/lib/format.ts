const UNITS = ['B', 'KiB', 'MiB', 'GiB', 'TiB']

export function formatSize(bytes: number): string {
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024
    unit += 1
  }
  return unit === 0 ? `${value} B` : `${value.toFixed(value < 10 ? 1 : 0)} ${UNITS[unit]}`
}

const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

export function formatDate(unixSeconds: number): string {
  return dateFormat.format(unixSeconds * 1000)
}

const relative = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })

/** "5 minutes ago", "yesterday": for times that matter by how long ago they were. */
export function formatAgo(iso: string): string {
  const seconds = (Date.parse(iso) - Date.now()) / 1000
  const steps: [Intl.RelativeTimeFormatUnit, number][] = [
    ['second', 60],
    ['minute', 60],
    ['hour', 24],
    ['day', 30],
    ['month', 12],
    ['year', Infinity],
  ]
  let value = seconds
  for (const [unit, size] of steps) {
    if (Math.abs(value) < size) return relative.format(Math.round(value), unit)
    value /= size
  }
  return relative.format(Math.round(value), 'year')
}
