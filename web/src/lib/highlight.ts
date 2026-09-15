import type { ConsoleSpec, HighlightColor } from '../api/types'

const ANSI: Record<HighlightColor, string> = {
  red: '31',
  yellow: '33',
  green: '32',
  blue: '34',
  magenta: '35',
  cyan: '36',
  gray: '90',
}

// eslint-disable-next-line no-control-regex
const HAS_ANSI = /\x1b\[/

function compile(pattern: string): RegExp | null {
  try {
    return new RegExp(pattern)
  } catch {
    return null // valid in Python (checked by the panel) but not in JavaScript: ignore the rule
  }
}

/**
 * Colours console lines by the game template's rules. Stateful: a stack-trace line takes the colour
 * of the record it continues, so create a new one whenever the output starts over.
 * Mirrored in panel/tests/test_runtime.py (colour_of) to check templates against real log lines.
 */
export function createHighlighter(spec: ConsoleSpec | undefined): (line: string) => string {
  const rules = (spec?.highlight ?? [])
    .map((rule) => ({ regex: compile(rule.pattern), code: ANSI[rule.color] }))
    .filter((rule): rule is { regex: RegExp; code: string } => rule.regex !== null)
  const continuation = spec?.continuation ? compile(spec.continuation) : null
  let previous: string | null = null

  return (line) => {
    if (HAS_ANSI.test(line)) {
      previous = null // the game colours this itself
      return line
    }
    const code = continuation?.test(line) ? previous : (rules.find((r) => r.regex.test(line))?.code ?? null)
    previous = code
    return code ? `\x1b[${code}m${line}\x1b[0m` : line
  }
}
