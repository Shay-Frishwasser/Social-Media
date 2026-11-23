import {useCallback, useState} from 'react'
import {ChevronLeft, ChevronRight, Lock} from 'lucide-react'
import {api, write} from '../api'
import {navigate} from '../App'
import type {ScreenProps} from '../App'
import {clockTime, dateLabel, dateShift, localDate, localInstants, monday} from '../dates'
import {depths, platformLabel, platforms} from '../models'
import type {Brief, Plan, Platform, Playbook, RuleOverrides, Slot, Strategy, Version} from '../models'
import {Busy, Confirm, Dialog, Empty, ErrorNote, Panel, PlatformSelect, Status, useAction, useQuery} from '../ui'

export function StrategyStudio(props: ScreenProps) {
  const load = useCallback(async (signal: AbortSignal) => {
    const versions = await api<Version[]>(props.base + '/strategies', signal)
    const selected = versions.find(row => row.id === props.selection) ?? versions[0]
    const row = selected && await api<Strategy>(props.base + '/strategies/' + selected.id, signal)
    return {versions, row: row && !row.legacy ? row : undefined}
  }, [props.base, props.selection])
  const view = useQuery(load)
  return <><ErrorNote error={view.error}/>{view.loading ? <Busy/> : view.data &&
    <StrategyEditor key={view.data.row?.id ?? 'new'} {...props} row={view.data.row} versions={view.data.versions} refresh={view.refresh}/>}</>
}

function StrategyEditor({base, row, versions, refresh, notify}: ScreenProps & {
  row?: Strategy; versions: Version[]; refresh: () => void
}) {
  const [channels, setChannels] = useState<Platform[]>(row?.document.platforms ?? ['instagram'])
  const [register, setRegister] = useState(row?.document.messaging_register ?? 'neutral')
  const [address, setAddress] = useState(row?.document.hebrew_address ?? 'neutral')
  const [platform, setPlatform] = useState<Platform>(channels[0])
  const [overrides, setOverrides] = useState(row?.document.pillar_weight_overrides ?? {})
  const [rule, setRule] = useState(false)
  const [approve, setApprove] = useState(false)
  const [dirty, setDirty] = useState(false)
  const action = useAction()
  const weights = overrides[platform] ?? row?.document.pillar_weights[platform] ?? {}
  const total = Object.values(weights).reduce((sum, count) => sum + count, 0)
  const valid = Object.entries(overrides).every(([key, values]) => !channels.includes(key as Platform) ||
    Object.values(values).reduce((sum, count) => sum + count, 0) === 100)
  return <>
    <ErrorNote error={action.error}/>
    <div className="two-col">
      <Panel title="עמודי תוכן" note="סך המשקלים בעמודי כל פלטפורמה צריך להיות 100%.">
        <fieldset className="choices"><legend>ערוצים באסטרטגיה</legend>{platforms.map(channel => <label key={channel}>
          <input type="checkbox" checked={channels.includes(channel)} onChange={event => {
            const next = event.target.checked ? [...channels, channel] : channels.filter(key => key !== channel)
            setChannels(next)
            if (!next.includes(platform) && next.length) setPlatform(next[0])
            setDirty(true)
          }}/>{platformLabel[channel]}</label>)}</fieldset>
        {row && <>
          <label>משקלים לערוץ<select value={platform} onChange={event => setPlatform(event.target.value as Platform)}>
            {channels.map(channel => <option key={channel} value={channel}>{platformLabel[channel]}</option>)}
          </select></label>
          {row.document.pillars.map(pillar => <label className="weight" key={pillar.id}>
            <span dir="auto">{pillar.title}</span><bdi>{weights[pillar.id] ?? 0}%</bdi>
            <input type="range" min="0" max="100" aria-label={pillar.title} value={weights[pillar.id] ?? 0} onChange={event => {
              const next = Object.fromEntries(row.document.pillars.map(item =>
                [item.id, item.id === pillar.id ? Number(event.target.value) : weights[item.id] ?? 0]))
              setOverrides({...overrides, [platform]: next})
              setDirty(true)
            }}/></label>)}
          <p className={total === 100 ? 'pill ok' : 'pill warning'}>סך המשקלים · {total}%</p>
        </>}
      </Panel>
      <div className="stack">
        <Panel title="כללי קול וכתיבה">
          <label>סגנון כתיבה<select value={register} onChange={event => {setRegister(event.target.value); setDirty(true)}}>
            <option value="neutral">ניטרלי</option><option value="professional">מקצועי</option>
            <option value="conversational">שיחתי</option><option value="formal">רשמי</option></select></label>
          <label>לשון פנייה<select value={address} onChange={event => {setAddress(event.target.value); setDirty(true)}}>
            <option value="neutral">ניטרלית</option><option value="inclusive">מכלילה</option>
            <option value="plural">רבים</option><option value="feminine">נקבה</option><option value="masculine">זכר</option></select></label>
        </Panel>
        <Panel title="כללי פלטפורמה ופתיחים">
          <button onClick={() => setRule(true)}>כללים לפי ערוץ</button>
          {row?.document.messaging_angles.length ? <ul className="rows">{row.document.messaging_angles.slice(0, 12).map((item, index) => <li key={index}>
            <div><b dir="auto">{item.hook}</b><small dir="auto">{item.evidence_text}</small></div>
            <button onClick={() => void action.run(async () => {await navigator.clipboard.writeText(item.hook); notify('הפתיח הועתק')})}>העתקה</button>
          </li>)}</ul> : <p className="muted">פתיחים יופיעו אחרי יצירת האסטרטגיה.</p>}
        </Panel>
      </div>
    </div>
    <footer className="page-actions">
      {row && <Status value={dirty ? 'DRAFT' : row.status}/>}
      {versions.length > 0 && <label>גרסאות<select value={row?.id ?? ''} onChange={event => navigate('strategy', event.target.value)}>
        {!row && <option value="">בחרו גרסה</option>}
        {versions.map(version => <option key={version.id} value={version.id}>גרסה {version.version} · {version.status}</option>)}
      </select></label>}
      <button className={row ? '' : 'primary'} disabled={!valid || !channels.length || action.pending} onClick={() => void action.run(async () => {
        const payload = {platforms: channels, messaging_register: register, hebrew_address: address,
          ...(row ? {pillars: row.document.pillars.map(({id, claim_ids, audience_claim_ids}) => ({id, claim_ids, audience_claim_ids})),
            pillar_weights: Object.fromEntries(Object.entries(overrides).filter(([key]) => channels.includes(key as Platform)))} : {})}
        const saved = await write<Strategy>(base + '/strategies' + (row ? '/' + row.id : ''), payload, row ? 'PUT' : 'POST')
        navigate('strategy', saved.id)
        notify('אסטרטגיה נשמרה כטיוטה')
      })}>{row ? 'שמירת גרסה חדשה' : 'יצירת אסטרטגיה'}</button>
      {row?.status === 'DRAFT' && <button className="primary" disabled={dirty} onClick={() => setApprove(true)}>אישור אסטרטגיה</button>}
      {row?.status === 'APPROVED' && <button className="primary" onClick={() => navigate('calendar')}>ללוח התוכן</button>}
    </footer>
    {rule && <Rules base={base} initialPlatform={platform} notify={notify} onClose={() => setRule(false)}/>}
    {approve && row && <Confirm title="אישור אסטרטגיה" onClose={() => setApprove(false)} action={async () => {
      await write(base + '/strategies/' + row.id + '/approve'); refresh(); notify('האסטרטגיה אושרה')
    }}>לוח התוכן ישתמש בגרסה שאושרה ובכללי הערוצים ששויכו אליה.</Confirm>}
  </>
}

function Rules({base, initialPlatform, onClose, notify}: {base: string; initialPlatform: Platform; onClose: () => void; notify: (text: string) => void}) {
  const [platform, setPlatform] = useState(initialPlatform)
  const load = useCallback((signal: AbortSignal) => api<Playbook>(base + '/playbooks/' + platform, signal), [base, platform])
  const data = useQuery(load)
  return <Dialog title="כללים לפי ערוץ" drawer onClose={onClose}>
    <PlatformSelect value={platform} onChange={value => {data.setData(undefined); setPlatform(value)}}/>
    <ErrorNote error={data.error}/>
    {data.loading ? <Busy/> : data.data && <RuleEditor key={platform + ':' + data.data.override_version} playbook={data.data}
      onSave={async payload => {data.setData(await write<Playbook>(base + '/playbooks/' + platform, payload, 'PUT')); notify('כללי הערוץ נשמרו')}}/>}
  </Dialog>
}

function RuleEditor({playbook, onSave}: {playbook: Playbook; onSave: (value: RuleOverrides) => Promise<void>}) {
  const action = useAction()
  const overrides = playbook.overrides
  return <form className="stack" onSubmit={event => {
    event.preventDefault()
    const values = new FormData(event.currentTarget)
    const read = (key: string) => String(values.get(key) ?? '').trim()
    const list = (key: string) => read(key).split(',').map(value => value.trim()).filter(Boolean)
    const link = read('link') as RuleOverrides['content_link_placement']
    const account = read('account') as RuleOverrides['account_type']
    void action.run(() => onSave({...overrides, posts_per_week: read('posts') ? Number(read('posts')) : null,
      content_link_placement: link || null, account_type: account || null,
      local_times: list('times').length ? list('times') : null,
      weekdays: list('days').length ? list('days').map(Number) : null, excluded_topics: list('topics')}))
  }}>
    <p><bdi>v{playbook.playbook_version}</bdi> · התאמה {playbook.override_version ?? 'ללא'} · {playbook.posts_per_week} פוסטים בשבוע</p>
    <label>פוסטים בשבוע<input name="posts" type="number" min={1} max={42} defaultValue={overrides.posts_per_week ?? ''} placeholder={String(playbook.posts_per_week)}/></label>
    <label>קישורים בכיתוב<select name="link" defaultValue={overrides.content_link_placement ?? ''}>
      <option value="">כלל ברירת מחדל</option><option value="body">בגוף בלבד</option><option value="forbidden">ללא קישורים</option>
    </select></label>
    <label>סוג חשבון<select name="account" defaultValue={overrides.account_type ?? ''}>
      <option value="">כלל ברירת מחדל</option><option value="company_page">דף חברה</option><option value="personal_profile">פרופיל אישי</option>
    </select></label>
    <label>ימים מועדפים · שני הוא 0<input dir="ltr" name="days" placeholder="0,2,4" defaultValue={overrides.weekdays?.join(',')}/></label>
    <label>שעות מועדפות בשעון ישראל<input dir="ltr" name="times" placeholder="09:00,15:30" defaultValue={overrides.local_times?.join(',')}/></label>
    <label>נושאים מוחרגים, מופרדים בפסיקים<input name="topics" defaultValue={overrides.excluded_topics?.join(', ')}/></label>
    <p className="muted" dir="auto">{playbook.link_policy}</p>
    <details><summary>כללים אפקטיביים</summary><pre dir="ltr">{JSON.stringify(playbook.rules, null, 2)}</pre></details>
    <ErrorNote error={action.error}/><button className="primary" disabled={action.pending}>שמירת כללי ערוץ</button>
  </form>
}

const dayLabels = ['שני', 'שלישי', 'רביעי', 'חמישי', 'שישי', 'שבת', 'ראשון']

export function Calendar({base, selection, notify}: ScreenProps) {
  const load = useCallback(async (signal: AbortSignal) => {
    const [versions, briefs] = await Promise.all([api<Version[]>(base + '/cadence-plans', signal),
      api<Brief[]>(base + '/on-demand-photo-briefs', signal)])
    const id = versions.find(row => row.id === selection)?.id ?? versions[0]?.id
    const plan = id ? await api<Plan>(base + '/cadence-plans/' + id, signal) : null
    return {versions, briefs, plan: plan?.legacy ? null : plan}
  }, [base, selection])
  const view = useQuery(load)
  const [range, setRange] = useState<'weekly' | 'monthly'>('weekly')
  const [shown, setShown] = useState<string>()
  const [start, setStart] = useState('')
  const [filter, setFilter] = useState<Platform | ''>('')
  const [slot, setSlot] = useState<Slot>()
  const [dialog, setDialog] = useState<'approve' | 'event' | null>(null)
  const action = useAction()
  const plan = view.data?.plan
  const anchor = shown ?? plan?.document.request.start_date ?? localDate()
  const gridStart = range === 'weekly' ? monday(anchor) : monday(anchor.slice(0, 7) + '-01')
  const days = Array.from({length: range === 'weekly' ? 7 : 42}, (_, index) => dateShift(gridStart, index))
  const matching = plan?.slots.filter(item => !filter || filter === item.platform) ?? []
  const moveMonth = (delta: number) => {
    const date = new Date(anchor.slice(0, 7) + '-01T12:00:00Z')
    date.setUTCMonth(date.getUTCMonth() + delta)
    setShown(date.toISOString().slice(0, 10))
  }
  return <>
    <ErrorNote error={action.error ?? view.error}/>
    {view.loading ? <Busy/> : <Panel title="תכנון תוכן" note="שעון ישראל · משבצות בתוכנית מאושרת נשמרות לפרסום.">
      <div className="calendar-tools">
        <div className="segmented"><button aria-pressed={range === 'weekly'} onClick={() => setRange('weekly')}>שבוע</button>
          <button aria-pressed={range === 'monthly'} onClick={() => setRange('monthly')}>חודש</button></div>
        <div className="actions"><button className="icon-button" aria-label="תקופה קודמת" onClick={() =>
          range === 'weekly' ? setShown(dateShift(anchor, -7)) : moveMonth(-1)}><ChevronRight/></button>
          <bdi>{range === 'weekly' ? gridStart : anchor.slice(0, 7)}</bdi>
          <button className="icon-button" aria-label="תקופה הבאה" onClick={() =>
            range === 'weekly' ? setShown(dateShift(anchor, 7)) : moveMonth(1)}><ChevronLeft/></button></div>
        <label>ערוץ<select value={filter} onChange={event => setFilter(event.target.value as Platform | '')}>
          <option value="">כל הערוצים</option>{platforms.map(platform => <option value={platform} key={platform}>{platformLabel[platform]}</option>)}
        </select></label>
      </div>
      {!plan && <Empty title="אין תוכנית לתצוגה" action={<button onClick={() => navigate('strategy')}>לאסטרטגיה</button>}>אשרו אסטרטגיה לפני יצירת משבצות.</Empty>}
      {plan && !matching.some(item => days.includes(localDate(new Date(item.scheduled_at)))) && <Empty title="אין משבצות בתקופה הזו"/>}
      {plan && <div className="calendar-grid">{days.map((day, index) => {
        const slots = matching.filter(item => localDate(new Date(item.scheduled_at)) === day)
        return <section key={day} className={slots.length ? 'day has-slots' : 'day'}>
          <h3>{dayLabels[index % 7]} <bdi>{day.slice(8)}</bdi></h3>
          {slots.map(item => <button className="slot" key={item.id} onClick={() => setSlot(item)}>
            <bdi>{platformLabel[item.platform]} · {clockTime(item.scheduled_at)}</bdi>
            <small>{item.depth ? depths[item.depth] : 'אירוע צילום'} {plan.status === 'APPROVED' && <Lock size={12}/>}</small>
          </button>)}
        </section>
      })}</div>}
    </Panel>}
    <footer className="page-actions">
      {plan && <Status value={plan.status}/>}
      {!!view.data?.versions.length && <label>גרסת תוכנית<select value={plan?.id ?? ''} onChange={event => {
        setShown(undefined); setStart(''); navigate('calendar', event.target.value)
      }}>
        {!plan && <option value="">בחרו תוכנית</option>}{view.data.versions.map(version =>
          <option value={version.id} key={version.id}>גרסה {version.version} · {version.status}</option>)}
      </select></label>}
      <label>תחילת תוכנית<input type="date" dir="ltr" value={start || plan?.document.request.start_date || gridStart}
        onChange={event => setStart(event.target.value)}/></label>
      <button disabled={action.pending} onClick={() => void action.run(async () => {
        const next = await write<Plan>(base + '/cadence-plans', {start_date: start || gridStart, period: range})
        setShown(undefined); setStart(''); navigate('calendar', next.id); notify('תוכנית תוכן נוצרה')
      })}>יצירת תוכנית חדשה</button>
      {plan && <button disabled={action.pending} onClick={() => void action.run(async () => {
        const next = await write<Plan>(base + '/cadence-plans/' + plan.id,
          {...plan.document.request, start_date: start || plan.document.request.start_date, period: range}, 'PUT')
        setShown(undefined); setStart(''); navigate('calendar', next.id); notify('נשמרה תוכנית מתוקנת')
      })}>יצירת גרסה מתוקנת</button>}
      {plan && <button onClick={() => setDialog('event')}>הוספת אירוע חד־פעמי</button>}
      {plan?.status === 'DRAFT' && <button className="primary" onClick={() => setDialog('approve')}>אישור ושריון משבצות</button>}
    </footer>
    {slot && <Dialog title="פרטי המשבצת" drawer onClose={() => setSlot(undefined)}>
      <p><bdi>{platformLabel[slot.platform]}</bdi> · {dateLabel(slot.scheduled_at)} · שעון ישראל</p>
      <p>{slot.depth ? depths[slot.depth] : 'אירוע צילום'}</p>
      {plan?.status === 'APPROVED' ? <button className="primary" onClick={() => navigate('content', 'slot:' + slot.id)}>יצירת תוכן מהמשבצת</button>
        : <p className="muted">שריינו את המשבצת באמצעות אישור התוכנית.</p>}
    </Dialog>}
    {dialog === 'approve' && plan && <Confirm title="אישור ושריון משבצות" onClose={() => setDialog(null)} action={async () => {
      await write(base + '/cadence-plans/' + plan.id + '/approve'); view.refresh(); notify('המועדים שוריינו')
    }}>אישור התוכנית שומר את המועדים המוצגים. לוח תוכן אינו מתזמן מסירה לערוץ באופן אוטומטי.</Confirm>}
    {dialog === 'event' && plan && <Dialog title="הוספת אירוע חד־פעמי" onClose={() => setDialog(null)}>
      {view.data?.briefs.length ? <EventForm briefs={view.data.briefs} save={async (id, instant) => {
        const result = await write<Plan>(base + '/cadence-plans/' + plan.id + '/on-demand-slots', {photo_brief_id: id, scheduled_at: instant})
        setDialog(null); navigate('calendar', result.id); notify('נוצרה טיוטה עם האירוע')
      }}/> : <Empty title="אין בריפים לצילום" action={<button onClick={() => navigate('photo')}>לסטודיו צילום</button>}/>}
    </Dialog>}
  </>
}

function EventForm({briefs, save}: {briefs: Brief[]; save: (id: string, instant: string) => Promise<void>}) {
  const [when, setWhen] = useState('')
  const [offset, setOffset] = useState(0)
  const choices = localInstants(when)
  const action = useAction()
  return <form className="stack" onSubmit={event => {
    event.preventDefault()
    const id = String(new FormData(event.currentTarget).get('brief'))
    if (choices[offset]) void action.run(() => save(id, choices[offset]))
  }}>
    <label>בריף צילום<select required name="brief">{briefs.map(brief =>
      <option key={brief.id} value={brief.id}>{brief.prompt} · {platformLabel[brief.platform]}</option>)}</select></label>
    <label>מועד בשעון ישראל<input required type="datetime-local" dir="ltr" value={when} onChange={event => {setWhen(event.target.value); setOffset(0)}}/></label>
    {when && !choices.length && <ErrorNote error="השעה אינה קיימת בשעון ישראל. בחרו שעה סמוכה."/>}
    {choices.length > 1 && <label>השעה מופיעה פעמיים. בחרו היסט UTC
      <select value={offset} onChange={event => setOffset(Number(event.target.value))}>
        <option value={0}>UTC+03:00</option><option value={1}>UTC+02:00</option>
      </select></label>}
    <ErrorNote error={action.error}/><button className="primary" disabled={action.pending || !choices.length}>הוספה לטיוטה</button>
  </form>
}
