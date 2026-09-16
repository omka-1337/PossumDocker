import { IconDeviceDesktop, IconMoon, IconSun } from '@tabler/icons-react'
import { setThemeChoice, useThemeChoice, type ThemeChoice } from '../lib/theme'

const THEMES: { value: ThemeChoice; label: string; icon: typeof IconMoon }[] = [
  { value: 'dark', label: 'dark', icon: IconMoon },
  { value: 'system', label: 'system', icon: IconDeviceDesktop },
  { value: 'light', label: 'light', icon: IconSun },
]

/** Settings of the panel itself, for this browser. */
export function SettingsPage() {
  const choice = useThemeChoice()

  return (
    <div className="mx-auto w-full max-w-xl px-4 py-8">
      <h1 className="mb-8 text-lg font-semibold">settings</h1>

      <section>
        <h2 className="mb-1 font-medium">theme</h2>
        <p className="mb-3 text-sm text-muted">system follows your device. remembered in this browser.</p>
        <div role="radiogroup" aria-label="theme" className="grid grid-cols-3 gap-2">
          {THEMES.map(({ value, label, icon: Icon }) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={choice === value}
              onClick={() => setThemeChoice(value)}
              className={`flex flex-col items-center gap-2 rounded-xl px-3 py-4 text-sm transition ${
                choice === value ? 'bg-active text-on-active' : 'bg-panel text-zinc-300 hover:bg-raised'
              }`}
            >
              <Icon size={22} stroke={1.5} />
              {label}
            </button>
          ))}
        </div>
      </section>
    </div>
  )
}
