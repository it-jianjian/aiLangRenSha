import { describe, expect, it } from 'vitest'
import { mergePublicEvents } from './eventStream'

describe('mergePublicEvents', () => {
  it('deduplicates a snapshot event replayed by WebSocket and presents server order', () => {
    const snapshot = [
      { event_id: 'event-002', event_order: '2026-07-16T12:00:02:event-002', type: 'speech', data: {}, timestamp: '2026-07-16T12:00:02' },
    ]
    const realtime = [
      { event_id: 'event-003', event_order: '2026-07-16T12:00:03:event-003', type: 'speech', data: {}, timestamp: '2026-07-16T12:00:03' },
      { event_id: 'event-001', event_order: '2026-07-16T12:00:01:event-001', type: 'speech', data: {}, timestamp: '2026-07-16T12:00:01' },
      { event_id: 'event-002', event_order: '2026-07-16T12:00:02:event-002', type: 'speech', data: {}, timestamp: '2026-07-16T12:00:02' },
    ]

    expect(mergePublicEvents(snapshot, realtime).map(event => event.event_id)).toEqual([
      'event-001', 'event-002', 'event-003',
    ])
  })
})
