export const jerusalem = 'Asia/Jerusalem'

const isoParts = new Intl.DateTimeFormat('sv-SE', {
  timeZone: jerusalem, year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})
export const localDate = (value = new Date()) => isoParts.format(value).slice(0, 10)
export const clockTime = (value: string) => isoParts.format(new Date(value)).slice(11, 16)
export const dateLabel = (value: string) => new Date(value).toLocaleString('he-IL', {
  timeZone: jerusalem, dateStyle: 'medium', timeStyle: 'short',
})
export function dateShift(day: string, count: number) {
  const date = new Date(day + 'T12:00:00Z')
  date.setUTCDate(date.getUTCDate() + count)
  return date.toISOString().slice(0, 10)
}
export function monday(day: string) {
  const weekday = new Date(day + 'T12:00:00Z').getUTCDay()
  return dateShift(day, -((weekday + 6) % 7))
}

// Israel wall times can have no match during a DST jump or two during a fold.
export function localInstants(value: string) {
  if (!value) return []
  const expected = value.replace('T', ' ')
  const naive = Date.parse(value + 'Z')
  if (!Number.isFinite(naive)) return []
  return [3, 2].map(offset => new Date(naive - offset * 3600000))
    .filter(candidate => isoParts.format(candidate) === expected)
    .map(candidate => candidate.toISOString())
}
