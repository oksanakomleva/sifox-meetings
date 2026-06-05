// Demo mode: a presentation layer on top of the admin's "view as user" preview.
// When ON we show REAL meetings, but only those tagged "демо", with the "демо"
// tag itself hidden; the "Расширение" nav is hidden; the AI chat starts empty and
// is not persisted (backend skips saving for demo). The flag lives in localStorage
// and is only meaningful inside preview (cleared when leaving preview — App.tsx).
import type { Meeting } from '../types'

const KEY = 'demo'
export const DEMO_TAG = 'демо'

export function isDemoOn(): boolean {
  try {
    return localStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

export function setDemo(on: boolean): void {
  try {
    if (on) localStorage.setItem(KEY, '1')
    else localStorage.removeItem(KEY)
  } catch {
    /* ignore */
  }
}

export function clearDemo(): void {
  try {
    localStorage.removeItem(KEY)
  } catch {
    /* ignore */
  }
}

// ── Tag helpers ───────────────────────────────────────────────────────────────

export function hasDemoTag(m: Meeting): boolean {
  return (m.tags ?? []).some(t => t.toLowerCase() === DEMO_TAG)
}

/** Drop the "демо" tag from a meeting so it is never shown in the demo UI. */
export function stripDemoTag<T extends { tags?: string[] | null }>(m: T): T {
  return { ...m, tags: (m.tags ?? []).filter(t => t.toLowerCase() !== DEMO_TAG) }
}

/** Keep only "демо"-tagged meetings and hide the "демо" tag on each. */
export function filterDemoMeetings(meetings: Meeting[]): Meeting[] {
  return meetings.filter(hasDemoTag).map(stripDemoTag)
}

/** Remove the "демо" tag from a known-tags vocabulary list. */
export function stripDemoFromTags(tags: string[]): string[] {
  return tags.filter(t => t.toLowerCase() !== DEMO_TAG)
}
