import { IconArrowLeft, IconX } from '@tabler/icons-react'
import { useEffect, type ButtonHTMLAttributes, type ReactNode } from 'react'
import type { Option, ServerStatus } from '../api/types'

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'danger'
}

const buttonVariants = {
  primary: 'bg-active text-black hover:bg-white',
  secondary: 'bg-raised text-zinc-100 hover:bg-raised-hover',
  danger: 'bg-red-950/60 text-red-300 hover:bg-red-950',
}

export function Button({ variant = 'secondary', className = '', ...props }: ButtonProps) {
  return (
    <button
      className={`flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium transition active:scale-[0.98] disabled:pointer-events-none disabled:opacity-40 ${buttonVariants[variant]} ${className}`}
      {...props}
    />
  )
}

export function IconButton({ className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={`grid size-8 place-items-center rounded-full text-zinc-400 transition hover:bg-raised hover:text-zinc-100 ${className}`}
      {...props}
    />
  )
}

export function Modal({
  title,
  onClose,
  onBack,
  children,
}: {
  title: ReactNode
  onClose: () => void
  onBack?: () => void
  children: ReactNode
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center overflow-y-auto bg-black/60 backdrop-blur-sm sm:items-center sm:p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        className="w-full max-w-md rounded-t-3xl border border-line-soft bg-panel p-5 shadow-2xl sm:rounded-3xl"
        // Clicks inside the dialog must not reach the backdrop's onClick.
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-5 flex items-center gap-2">
          {onBack && (
            <IconButton onClick={onBack} aria-label="back">
              <IconArrowLeft size={18} />
            </IconButton>
          )}
          <h2 className="flex-1 font-semibold lowercase">{title}</h2>
          <IconButton onClick={onClose} aria-label="close">
            <IconX size={18} />
          </IconButton>
        </div>
        {children}
      </div>
    </div>
  )
}

/** A row of joined buttons, like cobalt's "auto / audio / mute". */
export function Segmented({
  options,
  value,
  onChange,
}: {
  options: Option[]
  value: string
  onChange: (value: string) => void
}) {
  return (
    <div role="radiogroup" className="flex overflow-hidden rounded-xl bg-raised">
      {options.map((o, i) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          onClick={() => onChange(o.value)}
          className={`flex-1 px-3 py-2.5 text-sm lowercase transition ${i > 0 ? 'border-l border-line-soft' : ''} ${
            o.value === value ? 'bg-active text-black' : 'hover:bg-raised-hover'
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { value: T; label: string }[]
  value: T
  onChange: (value: T) => void
}) {
  return (
    <div role="tablist" className="mb-4 flex gap-1 overflow-x-auto">
      {tabs.map((tab) => (
        <button
          key={tab.value}
          role="tab"
          aria-selected={tab.value === value}
          onClick={() => onChange(tab.value)}
          className={`shrink-0 rounded-xl px-3 py-2 text-sm lowercase transition ${
            tab.value === value ? 'bg-active text-black' : 'text-zinc-300 hover:bg-panel'
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}

export function Switch({
  id,
  checked,
  onChange,
}: {
  id?: string
  checked: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`relative h-6 w-11 shrink-0 rounded-full transition ${checked ? 'bg-active' : 'bg-raised-hover'}`}
    >
      <span
        className={`absolute top-1 left-1 size-4 rounded-full transition ${
          checked ? 'translate-x-5 bg-black' : 'bg-zinc-400'
        }`}
      />
    </button>
  )
}

const statusDot: Record<ServerStatus, string> = {
  pending: 'bg-zinc-500',
  installing: 'bg-sky-400 animate-pulse',
  install_failed: 'bg-red-500',
  stopped: 'bg-zinc-500',
  starting: 'bg-amber-400 animate-pulse',
  running: 'bg-emerald-400',
  stopping: 'bg-amber-400 animate-pulse',
  restoring: 'bg-sky-400 animate-pulse',
  unknown: 'bg-zinc-700',
}

export function StatusBadge({ status }: { status: ServerStatus }) {
  return (
    <span className="flex items-center gap-2 rounded-full bg-raised px-2.5 py-1 text-xs text-zinc-300">
      <span className={`size-1.5 rounded-full ${statusDot[status]}`} />
      {status.replace('_', ' ')}
    </span>
  )
}
