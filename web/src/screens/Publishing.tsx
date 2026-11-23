import {useCallback, useState} from 'react'
import {CheckCircle2, CircleAlert, Download} from 'lucide-react'
import {api, file, saveFile, write} from '../api'
import {navigate} from '../App'
import type {ScreenProps} from '../App'
import {dateLabel, localInstants} from '../dates'
import {platformLabel} from '../models'
import type {Connection, Content, ContentSummary, Gate, Receipt, Visual} from '../models'
import {Busy, Dialog, Empty, ErrorNote, Panel, Status, isolate, useAction, useQuery} from '../ui'

const gateChecks = [
  {title: 'אישור פרופיל ושפה חזותית', codes: ['profile_approval_stale', 'design_approval_stale']},
  {title: 'אישור הגרסה הנוכחית', codes: ['revision_not_approved']},
  {title: 'אימות עובדות וכללי תוכן', codes: ['copy_validation_failed', 'validation_context_stale']},
  {title: 'תוצר חזותי תואם', codes: ['visual_required', 'visual_revision_mismatch', 'visual_design_mismatch']},
  {title: 'תקינות קבצים ומרווחים', codes: ['visual_integrity_failed']},
]
const gateFixes: Record<string, [string, 'identity' | 'design' | 'content']> = {
  profile_approval_stale: ['נדרש פרופיל מאושר ועדכני. לאחר מכן צרו תוכן חדש מולו.', 'identity'],
  design_approval_stale: ['נדרש אישור עדכני לשפה החזותית.', 'design'],
  revision_not_approved: ['הגרסה הנוכחית טרם אושרה.', 'content'],
  copy_validation_failed: ['יש לתקן את הטקסט או לעדכן את מקורותיו.', 'content'],
  validation_context_stale: ['מקורות התוכן או התוכנית השתנו. בדקו את פרטי הגרסה.', 'content'],
  visual_required: ['נדרש תוצר חזותי לגרסה הזו.', 'content'],
  visual_revision_mismatch: ['בחרו תוצר התואם לגרסת התוכן שאושרה.', 'content'],
  visual_design_mismatch: ['הפיקו מחדש לפי השפה החזותית המאושרת.', 'content'],
  visual_integrity_failed: ['בדיקת התקינות נכשלה. הפיקו את התוצר מחדש.', 'content'],
}
type Channel = 'email' | 'meta' | 'linkedin'

export function Publishing(props: ScreenProps) {
  const {base, selection, capabilities, notify} = props
  const load = useCallback(async (signal: AbortSignal) => {
    const [items, connections, visuals] = await Promise.all([
      api<ContentSummary[]>(base + '/content', signal), api<Connection[]>(base + '/publishing/connections', signal),
      api<Visual[]>(base + '/visual-assets', signal),
    ])
    const chosen = items.find(item => item.id === selection) ?? items[0]
    const item = chosen ? await api<Content>(base + '/content/' + chosen.id, signal) : null
    return {items, item, connections, visuals}
  }, [base, selection])
  const view = useQuery(load)
  const [accounts, setAccounts] = useState(false)
  return <>
    <ErrorNote error={view.error}/>
    <div className="selection-bar">
      <label>פריט למסירה<select value={view.data?.item?.id ?? ''} onChange={event => navigate('publishing', event.target.value)}>
        {!view.data?.item && <option value="">בחרו פריט</option>}
        {view.data?.items.map(item => <option key={item.id} value={item.id}>{isolate(platformLabel[item.platform])} · {isolate(dateLabel(item.created_at))}</option>)}
      </select></label>
      <button onClick={() => setAccounts(true)}>חשבונות ערוצים</button>
    </div>
    {view.loading ? <Busy/> : view.data?.item ? <Delivery key={view.data.item.revision.id} item={view.data.item}
      visuals={view.data.visuals.filter(row => row.content_revision_id === view.data?.item?.revision.id)}
      connections={view.data.connections} {...props}/> : <Empty title="אין פריטי תוכן למסירה"
        action={<button className="primary" onClick={() => navigate('content')}>לסטודיו התוכן</button>}/>}
    {accounts && <Dialog title="חשבונות ערוצים" drawer onClose={() => setAccounts(false)}>
      <p>חשבונות שמוגדרים בשרת מופיעים כאן עם מצב ההרשאה שלהם.</p>
      {view.data?.connections.length ? <ul className="rows">{view.data.connections.map(account => <li key={account.id}>
        <div><bdi>{account.label}</bdi><small>{platformLabel[account.platform]}</small>
          {account.expires_at && <small>תוקף: {dateLabel(account.expires_at)}</small>}</div>
        <span className="pill">{account.has_token ? 'נשמרה הרשאה' : 'חסרה הרשאה'}</span>
      </li>)}</ul> : <Empty title="אין חשבונות מחוברים"/>}
      <p className="muted">חיבור OAuth חדש דורש אינטגרציה מוגדרת. אפשר להוריד חבילת ZIP גם ללא חיבור לערוץ.</p>
      {!!capabilities?.social.length && <small>מחברים מותקנים: <bdi>{capabilities.social.join(', ')}</bdi></small>}
      <button onClick={() => {view.refresh(); notify('רשימת החשבונות מתעדכנת')}}>רענון חשבונות</button>
    </Dialog>}
  </>
}

function Delivery({item, visuals, connections, base, capabilities, notify}: ScreenProps & {
  item: Content; visuals: Visual[]; connections: Connection[]
}) {
  const [renderId, setRenderId] = useState('')
  const [send, setSend] = useState<Channel>()
  const [receiptId, setReceiptId] = useState(() => sessionStorage.getItem('brandai.receipt.' + item.id))
  const selected = visuals.find(row => row.id === renderId) ?? visuals[0]
  const loadGate = useCallback((signal: AbortSignal) => api<Gate>(base + '/publishing/gate/' + item.id +
    (selected ? '?visual_asset_id=' + selected.id : ''), signal), [base, item.id, selected?.id])
  const loadReceipt = useCallback((signal: AbortSignal) => receiptId
    ? api<Receipt>(base + '/publishing/records/' + receiptId, signal) : Promise.resolve(null), [base, receiptId])
  const gate = useQuery(loadGate)
  const receipt = useQuery(loadReceipt, receiptId ? 2000 : 0)
  const action = useAction()
  const report = gate.data
  const allowed = !gate.error && report?.allowed === true && !!selected && report.visual_asset_id === selected.id
  const eligibleConnections = connections.filter(account => account.platform === item.platform && account.has_token
    && (!account.expires_at || new Date(account.expires_at).getTime() > Date.now())
    && capabilities?.social.includes(account.provider))
  const sendAccounts = eligibleConnections.filter(account => account.provider === send)
  function zip() {
    if (!selected) return
    void action.run(async () => {
      try {
        const result = await write<Receipt>(base + '/publishing/export/' + item.id, {visual_asset_id: selected.id})
        const bundle = await file(base + '/publishing/export/' + item.id + '?export_id=' + result.id)
        saveFile(bundle, 'brandai-' + item.id + '.zip')
        notify('חבילת ה־ZIP מוכנה להורדה')
      } finally {gate.refresh()}
    })
  }

  return <>
    <ErrorNote error={gate.error ?? action.error}/>
    <div className="two-col">
      <Panel title="בדיקות לפני פרסום" note={`גרסה ${item.revision.version} · ${platformLabel[item.platform]}`}>
        <label>תוצר חזותי<select dir="ltr" value={selected?.id ?? ''} onChange={event => {
          gate.setData(undefined); setRenderId(event.target.value)
        }}>
          {!visuals.length && <option value="">אין תוצר תואם</option>}
          {visuals.map(visual => <option key={visual.id} value={visual.id}>{visual.aspect} · {visual.width}×{visual.height} · {visual.id.slice(0, 6)}</option>)}
        </select></label>
        {gate.loading ? <Busy label="בודק גרסה וקבצים…"/> : report && <>
          <Status value={allowed ? 'מוכן למסירה' : 'BLOCKED'}/>
          <ul className="rows">{gateChecks.map((check, index) => {
            const issues = report.issues.filter(issue => check.codes.includes(issue.code))
            return <li key={check.title}><div><b>{check.title}</b>
              {issues.map(issue => {
                const fix = gateFixes[issue.code]
                return <small key={issue.code}>{fix?.[0] ?? issue.message} {fix && <a
                  href={fix[1] === 'content' ? '#content/' + item.id : '#' + fix[1]}>לטיפול</a>}</small>
              })}</div>{issues.length ? <span className="pill warning"><CircleAlert size={15}/> חסום</span>
                : !allowed && index === 2 ? <span className="pill">ממתין</span>
                : !allowed ? <span className="pill">ללא ממצא</span>
                : <span className="pill ok"><CheckCircle2 size={15}/> תקין</span>}</li>
          })}</ul>
          {report.issues.filter(issue => !gateFixes[issue.code]).map(issue => <ErrorNote error={issue.message} key={issue.code}/>)}
        </>}
        <button onClick={() => {gate.setData(undefined); gate.refresh()}}>בדיקה מחדש</button>
      </Panel>
      <div className="stack">
        <Panel title="אפשרויות מסירה" note="השרת בודק את הגרסה והקבצים שוב בכל מסירה.">
          <div className="stack">
            <button className="primary" disabled={!allowed || action.pending} onClick={zip}><Download size={17}/> הורדת חבילת ZIP</button>
            <button disabled={!allowed || !capabilities?.email} onClick={() => setSend('email')}>שליחת חבילה במייל</button>
            {!capabilities?.email && <small>שליחה במייל דורשת שרת SMTP מוגדר.</small>}
            <button disabled={!allowed || !eligibleConnections.some(account => account.provider === 'meta')} onClick={() => setSend('meta')}>מסירה דרך Meta</button>
            <button disabled={!allowed || !eligibleConnections.some(account => account.provider === 'linkedin')} onClick={() => setSend('linkedin')}>מסירה דרך LinkedIn</button>
            {!eligibleConnections.length && <small>מסירה לערוץ דורשת חשבון פעיל שתואם לפלטפורמה.</small>}
          </div>
        </Panel>
        <Panel title="מסירה אחרונה">
          <ErrorNote error={receipt.error}/>
          {receipt.data ? <><Status value={receipt.data.status}/>
            {receipt.data.scheduled_at && <p>{dateLabel(receipt.data.scheduled_at)} · שעון ישראל</p>}
            {receipt.data.status === 'NEEDS_RECONCILIATION' && <p>מצב הפרסום אינו ודאי. בדקו בחשבון הערוץ לפני ניסיון נוסף.</p>}
            {Object.keys(receipt.data.detail).length > 0 && <details><summary>פרטי מסירה</summary>
              <pre dir="ltr">{JSON.stringify(receipt.data.detail, null, 2)}</pre></details>}
          </> : <p className="muted">כאן יופיע סטטוס המסירה לאחר שליחה או תזמון.</p>}
        </Panel>
      </div>
    </div>
    {send && allowed && selected && <DispatchForm channel={send} accounts={sendAccounts} onClose={() => setSend(undefined)}
      onSend={async (recipient, connectionId, timestamp) => {
        const intent = JSON.stringify([item.revision.id, selected.id, send, recipient, connectionId, timestamp])
        const intentKey = 'brandai.delivery.' + intent
        const idempotency = sessionStorage.getItem(intentKey) ?? crypto.randomUUID()
        sessionStorage.setItem(intentKey, idempotency)
        const result = await write<Receipt>(base + '/publishing/dispatch/' + item.id, {
          channel: send, visual_asset_id: selected.id, recipient, connection_id: connectionId,
          scheduled_at: timestamp, idempotency_key: idempotency,
        })
        receipt.setData(result)
        setReceiptId(result.id)
        sessionStorage.setItem('brandai.receipt.' + item.id, result.id)
        setSend(undefined)
        notify('המסירה נוספה לתור המקומי')
        gate.refresh()
      }}/>}
  </>
}

function DispatchForm({channel, accounts, onClose, onSend}: {
  channel: Channel; accounts: Connection[]; onClose: () => void
  onSend: (recipient: string | null, connectionId: string | null, timestamp: string | null) => Promise<void>
}) {
  const [when, setWhen] = useState('')
  const [offset, setOffset] = useState(0)
  const options = localInstants(when)
  const action = useAction()
  return <Dialog title={channel === 'email' ? 'אישור שליחה במייל' : 'אישור מסירה לערוץ'} onClose={() => {if (!action.pending) onClose()}}>
    <form className="stack" onSubmit={event => {
      event.preventDefault()
      const form = new FormData(event.currentTarget)
      const rawEmail = form.get('recipient')
      const connection = form.get('connection')
      if (when && !options[offset]) return
      void action.run(() => onSend(typeof rawEmail === 'string' ? rawEmail.trim() : null,
        typeof connection === 'string' ? connection : null, options[offset] ?? null))
    }}>
      {channel === 'email' ? <label>כתובת הנמען<input type="email" name="recipient" dir="ltr" autoFocus required/></label>
        : <label>חשבון יעד<select name="connection" required>{accounts.map(account => <option value={account.id} key={account.id}>{account.label}</option>)}</select></label>}
      <label>מועד בשעון ישראל · השאירו ריק לשליחה עכשיו<input type="datetime-local" dir="ltr" value={when} onChange={event => {
        setWhen(event.target.value); setOffset(0)
      }}/></label>
      {options.length > 1 && <label>השעה מופיעה פעמיים. בחרו היסט UTC<select value={offset} onChange={event => setOffset(Number(event.target.value))}>
        <option value={0}>UTC+03:00</option><option value={1}>UTC+02:00</option>
      </select></label>}
      {when && !options.length && <ErrorNote error="השעה אינה קיימת בשעון ישראל."/>}
      {options[offset] && <p>המסירה תתוזמן ל־{dateLabel(options[offset])}.</p>}
      <ErrorNote error={action.error}/>
      <button className="primary" disabled={action.pending || !!when && !options.length}>{when ? 'אישור תזמון' : 'אישור שליחה'}</button>
    </form>
  </Dialog>
}
