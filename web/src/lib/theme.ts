import { useSyncExternalStore } from 'react'

export type ThemeChoice = 'dark' | 'system' | 'light'
export type Theme = 'dark' | 'light'

// Same key and JSON format as useStoredState; index.html reads it too, before the first paint.
const KEY = 'possum.theme'
const lightQuery = window.matchMedia('(prefers-color-scheme: light)')
const listeners = new Set<() => void>()

function storedChoice(): ThemeChoice {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) ?? 'null')
    return value === 'dark' || value === 'light' ? value : 'system'
  } catch {
    return 'system'
  }
}

let choice = storedChoice()

function apply() {
  const theme: Theme = choice === 'system' ? (lightQuery.matches ? 'light' : 'dark') : choice
  document.documentElement.dataset.theme = theme
  listeners.forEach((listener) => listener())
}

// Follow the system while "system" is chosen.
lightQuery.addEventListener('change', apply)

export function setThemeChoice(next: ThemeChoice) {
  choice = next
  try {
    localStorage.setItem(KEY, JSON.stringify(next))
  } catch {
    // not remembered, still applied for this visit
  }
  apply()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** What the user picked in settings. */
export function useThemeChoice(): ThemeChoice {
  return useSyncExternalStore(subscribe, () => choice)
}

/** The theme actually shown: "system" resolved. */
export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, () => (document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'))
}
