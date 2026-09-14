// Game servers colour their logs with ANSI escape codes like "\x1b[0;39m".
// eslint-disable-next-line no-control-regex
const ANSI = /\x1b\[[0-9;?]*[A-Za-z]/g

export function stripAnsi(text: string): string {
  return text.replace(ANSI, '')
}
