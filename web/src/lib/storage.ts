import { useState } from 'react'

/** useState remembered in localStorage (view mode and similar per-browser preferences). */
export function useStoredState<T>(key: string, initial: T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const stored = localStorage.getItem(key)
      return stored === null ? initial : (JSON.parse(stored) as T)
    } catch {
      return initial // private mode, blocked storage, bad JSON
    }
  })
  const set = (next: T) => {
    setValue(next)
    try {
      localStorage.setItem(key, JSON.stringify(next))
    } catch {
      // not persisted, still works for this session
    }
  }
  return [value, set]
}
