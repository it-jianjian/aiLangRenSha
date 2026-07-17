import type { WSMessage } from '../types'

type OrderedEvent = Pick<WSMessage, 'event_id' | 'event_order' | 'timestamp'>

/** Merge REST compensation and WebSocket events without duplicating or reordering public history. */
export function mergePublicEvents<T extends OrderedEvent>(existing: T[], incoming: T[]): T[] {
  const seenIds = new Set<string>()
  const merged: T[] = []

  for (const event of [...existing, ...incoming]) {
    if (event.event_id) {
      if (seenIds.has(event.event_id)) continue
      seenIds.add(event.event_id)
    }
    merged.push(event)
  }

  return merged
    .map((event, index) => ({ event, index }))
    .sort((left, right) => {
      const leftOrder = left.event.event_order || `${left.event.timestamp}:${left.event.event_id || ''}`
      const rightOrder = right.event.event_order || `${right.event.timestamp}:${right.event.event_id || ''}`
      return leftOrder.localeCompare(rightOrder) || left.index - right.index
    })
    .map(({ event }) => event)
}
