import {useCallback, useEffect, useRef, useState} from 'react'
import type {ReactNode} from 'react'
import {AlertCircle, Check, ChevronLeft, LoaderCircle, X} from 'lucide-react'
import {platforms, platformLabel} from './models'
import type {Platform} from './models'

export const statusText: Record<string, string> = {
  ACTIVE: 'פעיל', DRAFT: 'טיוטה', PENDING_REVIEW: 'ממתין לבדיקה', READY_FOR_REVIEW: 'מוכן לבדיקה',
  APPROVED: 'אושר', VALIDATION_FAILED: 'נדרש תיקון', SUPERSEDED: 'הוחלף', QUEUED: 'בתור',
  RUNNING: 'בעיבוד', RETRY_WAIT: 'ממתין לניסיון חוזר', SUCCEEDED: 'הושלם',
  DEAD_LETTER: 'נכשל', FAILED: 'נכשל', SCHEDULED: 'מתוזמן', PUBLISHED: 'פורסם',
  BLOCKED: 'חסום', DISPATCHING: 'במסירה', DELIVERED: 'נמסר', NEEDS_RECONCILIATION: 'נדרשת בדיקה בחשבון הערוץ',
}

export function Status({value}: {value: string}) {
  const tone = ['APPROVED', 'SUCCEEDED', 'DELIVERED', 'PUBLISHED'].includes(value) ? 'ok'
    : ['VALIDATION_FAILED', 'DEAD_LETTER', 'FAILED', 'BLOCKED', 'NEEDS_RECONCILIATION'].includes(value) ? 'warning' : ''
  return <span className={`pill ${tone}`}><span className="status-dot"/>{statusText[value] ?? value}</span>
}

export function Panel({title, note, children, className = ''}: {
  title: string; note?: string; children: ReactNode; className?: string
}) {
  return <section className={`panel ${className}`}><h2>{title}</h2>{note && <p className="muted">{note}</p>}{children}</section>
}

export function Empty({title, children, action}: {title: string; children?: ReactNode; action?: ReactNode}) {
  return <div className="empty"><ChevronLeft size={20} aria-hidden/><h3>{title}</h3>{children && <p>{children}</p>}{action}</div>
}

export function ErrorNote({error}: {error: string | null}) {
  return error ? <p className="notice error" role="alert"><AlertCircle size={18}/><bdi>{error}</bdi></p> : null
}

export function Busy({label = 'טוען…'}: {label?: string}) {
  return <p className="busy muted" role="status"><LoaderCircle aria-hidden size={16}/>{label}</p>
}

export function useAction() {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const active = useRef(false)
  const run = async (action: () => Promise<void>) => {
    if (active.current) return
    active.current = true
    setPending(true)
    setError(null)
    try { await action() }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'לא ניתן להשלים את הפעולה') }
    finally { active.current = false; setPending(false) }
  }
  return {run, pending, error, setError}
}

export function useQuery<T>(loader: (signal: AbortSignal) => Promise<T>, polling = 0) {
  const [data, setData] = useState<T>()
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const refresh = useCallback(() => setRevision(value => value + 1), [])
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    const load = async () => {
      try {
        const value = await loader(controller.signal)
        if (!controller.signal.aborted) { setData(value); setError(null) }
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason))
      } finally {
        if (polling && !controller.signal.aborted) timer = setTimeout(load, polling)
      }
    }
    void load()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [loader, revision, polling])
  return {data, setData, error, refresh, loading: data === undefined && error === null}
}

export function Dialog({title, children, onClose, drawer = false}: {
  title: string; children: ReactNode; onClose: () => void; drawer?: boolean
}) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const element = ref.current
    const trigger = document.activeElement
    element?.showModal()
    return () => {
      element?.close()
      if (trigger instanceof HTMLElement) trigger.focus()
    }
  }, [])
  return <dialog ref={ref} className={drawer ? 'drawer' : ''} aria-label={title}
    onCancel={event => {event.preventDefault(); onClose()}}
    onClick={event => {if (event.target === event.currentTarget) onClose()}}>
    <div className="dialog-inner">
      <header><h2>{title}</h2><button className="icon-button" aria-label="סגירה" onClick={onClose}><X size={20}/></button></header>
      {children}
    </div>
  </dialog>
}

export function Confirm({title, children, action, onClose}: {
  title: string; children: ReactNode; action: () => Promise<void>; onClose: () => void
}) {
  const {run, pending, error} = useAction()
  return <Dialog title={title} onClose={() => {if (!pending) onClose()}}>
    <p>{children}</p><ErrorNote error={error}/>
    <div className="actions"><button className="primary" disabled={pending} onClick={() => void run(async () => {
      await action()
      onClose()
    })}><Check size={16}/> אישור</button><button disabled={pending} onClick={onClose}>ביטול</button></div>
  </Dialog>
}

export function PlatformSelect({value, onChange, label = 'פלטפורמה'}: {
  value: Platform; onChange: (value: Platform) => void; label?: string
}) {
  return <label>{label}<select value={value} onChange={event => onChange(event.target.value as Platform)}>
    {platforms.map(platform => <option key={platform} value={platform}>{platformLabel[platform]}</option>)}
  </select></label>
}

export function SourceLink({url, children}: {url: string; children: ReactNode}) {
  try {
    if (['https:', 'http:'].includes(new URL(url).protocol)) {
      return <a href={url} target="_blank" rel="noopener noreferrer"><bdi>{children}</bdi></a>
    }
  } catch { /* Keep malformed historical URLs readable. */ }
  return <bdi>{children}</bdi>
}

export const display = (value: unknown): string => typeof value === 'string' ? value
  : value == null ? '' : JSON.stringify(value)

export const isolate = (value: string) => '\u2068' + value + '\u2069'
