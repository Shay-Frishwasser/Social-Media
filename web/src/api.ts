export class ApiError extends Error {
  constructor(public status: number, public payload: unknown, message: string) {
    super(message)
  }
}

export const apiUrl = (path: string) => '/api' + path
export const brandPath = (id: string) => '/brands/' + encodeURIComponent(id)

async function request(path: string, init?: RequestInit) {
  const response = await fetch(apiUrl(path), init)
  if (response.ok) return response
  const payload = await response.json().catch(() => null)
  const detail = payload?.detail
  const issues = payload?.issues
  const message = typeof detail === 'string' ? detail
    : Array.isArray(detail) ? detail.map(row => row.msg).join(' · ')
    : Array.isArray(issues) ? issues.map(row => row.message).join(' · ')
    : `הבקשה נכשלה (${response.status})`
  throw new ApiError(response.status, payload, message)
}

export async function api<T>(path: string, signal?: AbortSignal): Promise<T> {
  return (await request(path, {signal})).json()
}

export async function write<T>(path: string, data: object = {}, method = 'POST', signal?: AbortSignal): Promise<T> {
  const init = data instanceof FormData ? {method, body: data}
    : {method, body: JSON.stringify(data), headers: {'Content-Type': 'application/json'}}
  return (await request(path, {...init, signal})).json()
}

export async function file(path: string): Promise<Blob> {
  return (await request(path)).blob()
}

export function saveFile(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const anchor = Object.assign(document.createElement('a'), {href: url, download: name})
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
