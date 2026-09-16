import { FitAddon } from '@xterm/addon-fit'
import { Terminal, type ITheme } from '@xterm/xterm'
import '@xterm/xterm/css/xterm.css'
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import type { ConsoleSpec } from '../api/types'
import { createHighlighter } from '../lib/highlight'
import { useTheme, type Theme } from '../lib/theme'

const RECONNECT_MS = 2000
const HISTORY_SIZE = 50

type ServerMessage = { type: 'log' | 'error'; data: string }

// Background matches bg-frame around it. Colours are softer than xterm's defaults on dark, darker on light;
// white text from a game stays readable on the light one.
const TERMINAL_THEMES: Record<Theme, ITheme> = {
  dark: {
    background: '#101010',
    foreground: '#d4d4d8',
    selectionBackground: '#3f3f46',
    red: '#f87171',
    yellow: '#facc15',
    green: '#4ade80',
    blue: '#60a5fa',
    magenta: '#e879f9',
    cyan: '#22d3ee',
    brightBlack: '#71717a',
  },
  light: {
    background: '#f1f1f3',
    foreground: '#27272a',
    cursor: '#27272a',
    selectionBackground: '#d4d4d8',
    black: '#27272a',
    red: '#dc2626',
    yellow: '#a16207',
    green: '#15803d',
    blue: '#2563eb',
    magenta: '#c026d3',
    cyan: '#0e7490',
    white: '#52525b',
    brightBlack: '#71717a',
    brightWhite: '#18181b',
  },
}

interface Props {
  serverId: string
  running: boolean
  // The game template's colouring rules (WARN yellow, ERROR red...).
  consoleSpec?: ConsoleSpec
  // The user has the "console" permission.
  canSend: boolean
}

/**
 * Live server console: output rendered by xterm.js (keeps the game's colours),
 * commands typed into a separate input with ↑/↓ history.
 */
export function Console({ serverId, running, consoleSpec, canSend }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const terminalRef = useRef<Terminal | null>(null)
  const socketRef = useRef<WebSocket | null>(null)
  const [connected, setConnected] = useState(false)
  const theme = useTheme()
  const themeRef = useRef(theme)
  // Refs, so the socket effect doesn't reconnect when the template finishes loading.
  const specRef = useRef(consoleSpec)
  const highlightRef = useRef(createHighlighter(consoleSpec))

  useEffect(() => {
    specRef.current = consoleSpec
    highlightRef.current = createHighlighter(consoleSpec)
  }, [consoleSpec])

  // Terminal: created once per mount, resized with its container.
  useEffect(() => {
    const terminal = new Terminal({
      disableStdin: true, // typing happens in the input below
      convertEol: true,
      scrollback: 5000,
      fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
      fontSize: 13,
      cursorInactiveStyle: 'none',
      theme: TERMINAL_THEMES[themeRef.current],
    })
    const fit = new FitAddon()
    terminal.loadAddon(fit)
    terminal.open(containerRef.current!)
    fit.fit()
    terminalRef.current = terminal

    const observer = new ResizeObserver(() => fit.fit())
    observer.observe(containerRef.current!)
    return () => {
      observer.disconnect()
      terminal.dispose()
      terminalRef.current = null
    }
  }, [])

  // Switching the theme recolours the output already on screen too.
  useEffect(() => {
    themeRef.current = theme
    if (terminalRef.current) terminalRef.current.options.theme = TERMINAL_THEMES[theme]
  }, [theme])

  // WebSocket: reconnects on its own until the component unmounts.
  useEffect(() => {
    let closedByUs = false
    let retry: ReturnType<typeof setTimeout>

    const connect = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const socket = new WebSocket(`${protocol}://${window.location.host}/api/servers/${serverId}/console`)
      socketRef.current = socket

      socket.onopen = () => {
        setConnected(true)
        // The server replays recent history on every connect.
        terminalRef.current?.reset()
        highlightRef.current = createHighlighter(specRef.current)
      }
      socket.onmessage = (event) => {
        const message = JSON.parse(event.data) as ServerMessage
        if (message.type === 'log') terminalRef.current?.writeln(highlightRef.current(message.data))
        else terminalRef.current?.writeln(`\x1b[31m${message.data}\x1b[0m`)
      }
      socket.onclose = () => {
        setConnected(false)
        if (!closedByUs) retry = setTimeout(connect, RECONNECT_MS)
      }
    }
    connect()

    return () => {
      closedByUs = true
      clearTimeout(retry)
      socketRef.current?.close()
    }
  }, [serverId])

  const send = (command: string) => {
    socketRef.current?.send(JSON.stringify({ type: 'command', data: command }))
    // Echo it dimmed, so it's clear what was sent even if the game prints nothing.
    terminalRef.current?.writeln(`\x1b[90m> ${command}\x1b[0m`)
  }

  return (
    <div className="mb-6">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-medium">console</h2>
        <span className="flex items-center gap-1.5 text-xs text-muted">
          <span className={`size-1.5 rounded-full ${connected ? 'bg-emerald-400' : 'bg-zinc-600'}`} />
          {connected ? 'live' : 'connecting…'}
        </span>
      </div>
      <div className="overflow-hidden rounded-xl border border-line-soft bg-frame">
        {/* xterm needs a sized box; the padding lives on a wrapper so fit() measures correctly. */}
        <div className="p-3">
          <div ref={containerRef} className="h-80" />
        </div>
        {consoleSpec?.commands === false || !canSend ? (
          <p className="border-t border-line-soft px-3 py-2.5 text-sm text-muted">
            {canSend ? 'this game has no console commands' : "you can watch the console but not send commands"}
          </p>
        ) : (
          <CommandInput disabled={!connected || !running} running={running} onSend={send} />
        )}
      </div>
    </div>
  )
}

function CommandInput({
  disabled,
  running,
  onSend,
}: {
  disabled: boolean
  running: boolean
  onSend: (command: string) => void
}) {
  const [value, setValue] = useState('')
  const history = useRef<string[]>([])
  // Position while browsing history with ↑/↓; history.length means "the line being typed".
  const cursor = useRef(0)

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && value.trim()) {
      onSend(value)
      history.current = [...history.current.filter((c) => c !== value), value].slice(-HISTORY_SIZE)
      cursor.current = history.current.length
      setValue('')
    } else if (e.key === 'ArrowUp' && cursor.current > 0) {
      e.preventDefault()
      cursor.current -= 1
      setValue(history.current[cursor.current])
    } else if (e.key === 'ArrowDown' && cursor.current < history.current.length) {
      e.preventDefault()
      cursor.current += 1
      setValue(history.current[cursor.current] ?? '')
    }
  }

  return (
    <div className="flex items-center gap-2 border-t border-line-soft px-3">
      <span className="text-muted select-none">&gt;</span>
      <input
        className="w-full bg-transparent py-2.5 text-sm outline-none placeholder:text-muted disabled:opacity-50"
        placeholder={running ? 'type a command, ↑ for history' : 'start the server to send commands'}
        value={value}
        disabled={disabled}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        aria-label="console command"
      />
    </div>
  )
}
