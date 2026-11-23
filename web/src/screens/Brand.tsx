import {useCallback, useState} from 'react'
import {api, apiUrl, write} from '../api'
import {navigate} from '../App'
import type {ScreenProps} from '../App'
import {fonts, fontStyle} from '../fonts'
import type {Asset, Claim, Design, DesignFields, Job, Profile} from '../models'
import {Busy, Confirm, Dialog, Empty, ErrorNote, Panel, SourceLink, Status, display, useAction, useQuery} from '../ui'

export function Onboarding({base, brand, capabilities, notify}: ScreenProps) {
  const load = useCallback(async (signal: AbortSignal) => {
    const [profile, jobs] = await Promise.all([api<Profile | null>(base + '/profile?allow_empty=true', signal), api<Job[]>(base + '/jobs', signal)])
    return {profile, recent: jobs.find(job => job.kind === 'onboarding_research')}
  }, [base])
  const view = useQuery(load, 1500)
  const [website, setWebsite] = useState('')
  const [handles, setHandles] = useState('')
  const [manual, setManual] = useState(false)
  const action = useAction()
  const job = view.data?.recent
  const running = job && ['QUEUED', 'RUNNING', 'RETRY_WAIT'].includes(job.status)
  const ready = view.data?.profile && view.data.profile.status !== 'DRAFT'
  const phases = ['סריקת האתר', 'עיבוד והטמעת מקורות', 'חילוץ עובדות לבדיקה']
  const step = !job || job.progress < 20 ? -1 : job.progress < 45 ? 0 : job.progress < 75 ? 1 : 2
  return <>
    <ErrorNote error={view.error ?? action.error}/>
    <div className="two-col">
      <Panel title="פרטי המותג" note="המחקר משתמש במקורות ציבוריים.">
        <form className="stack" onSubmit={event => {
          event.preventDefault()
          void action.run(async () => {
            const social_handles = handles.split(/[\s,]+/).filter(Boolean).map(value => value.startsWith('@') ? value : '@' + value)
            const queued = await write<Job>(base + '/jobs', {kind: 'onboarding_research', idempotency_key: crypto.randomUUID(),
              payload: {website_url: website, company_name: brand.name, social_handles}})
            view.setData({profile: view.data?.profile ?? null, recent: queued})
            view.refresh()
          })
        }}>
          <label>שם המותג<input readOnly value={brand.name} dir="auto"/></label>
          <label>אתר ציבורי<input type="url" dir="ltr" required placeholder="https://example.com" value={website}
            onChange={event => setWebsite(event.target.value)}/></label>
          <label>שמות משתמש ברשתות (אופציונלי)<input dir="ltr" placeholder="@brand, @brandstudio"
            maxLength={396} value={handles} onChange={event => setHandles(event.target.value)}/></label>
          <div className="actions">
            <button className="primary" disabled={!capabilities?.onboarding_research || !!running || !!ready || action.pending}>
              {job?.status === 'DEAD_LETTER' ? 'ניסיון מחקר חוזר' : 'התחלת מחקר'}
            </button>
            <button type="button" disabled={!!running || !!ready} onClick={() => setManual(true)}>הזנת עובדות ידנית</button>
          </div>
          {capabilities && !capabilities.onboarding_research && <small>מחקר אוטומטי כבוי בהרצה הזו. אפשר להתחיל בהזנה ידנית.</small>}
        </form>
        {capabilities?.onboarding_research && capabilities.provider === 'mock' &&
          <small>במצב מקומי קל נאספים ציטוטים מדויקים מהאתר לבדיקה ידנית.</small>}
      </Panel>
      <Panel title="פעילות המחקר" note="אפשר להמשיך לעבוד בזמן שהמשימה פועלת.">
        {view.loading ? <Busy/> : job ? <>
          <div className="split"><Status value={job.status}/><bdi>{job.progress}%</bdi></div>
          <progress aria-label="התקדמות מחקר המותג" value={job.progress} max={100}/>
          <ol className="steps">{phases.map((phase, index) => <li className={index <= step ? 'reached' : ''} key={phase}>
            {phase} <small>{job.status === 'SUCCEEDED' || index < step ? 'הושלם' : index === step && running ? 'בעבודה' : 'ממתין'}</small>
          </li>)}</ol>
          {job.error_code && <ErrorNote error={job.error_code}/>}
        </> : <Empty title="אין מחקר פעיל"/>}
        {ready && <button className="primary" onClick={() => navigate('identity')}>לבדיקת זהות המותג</button>}
      </Panel>
    </div>
    {manual && <Dialog title="הזנת עובדות ידנית" onClose={() => setManual(false)}>
      <form className="stack" onSubmit={event => {
        event.preventDefault()
        const payload = Object.fromEntries(new FormData(event.currentTarget))
        void action.run(async () => {
          await write(base + '/profile/manual', payload)
          notify('הפרופיל נשמר לבדיקה')
          navigate('identity')
        })
      }}>
        <p>הזינו עובדות מדויקות שאפשר להשתמש בהן בתוכן. הפרופיל יחכה לאישור שלכם.</p>
        {([['offering', 'מה המותג מציע?'], ['audience', 'למי הוא מיועד?'], ['differentiator', 'מה מייחד אותו?']]).map(([key, label]) =>
          <label key={key}>{label}<textarea required maxLength={600} name={key} rows={2}/></label>
        )}
        <ErrorNote error={action.error}/><button className="primary" disabled={action.pending}>שמירת פרופיל לבדיקה</button>
      </form>
    </Dialog>}
  </>
}

type ClaimDialog = {kind: 'source'; claim: Claim} | {kind: 'edit'; claim: Claim} | {kind: 'gap'; question: string} | {kind: 'approve'}

const claimKeys: Record<string, string> = {offering: 'הצעת המותג', audience: 'קהל', differentiator: 'בידול'}

export function Identity({base, notify}: ScreenProps) {
  const load = useCallback(async (signal: AbortSignal) => {
    const [claims, profile] = await Promise.all([api<Claim[]>(base + '/claims', signal), api<Profile | null>(base + '/profile?allow_empty=true', signal)])
    return {claims, profile}
  }, [base])
  const data = useQuery(load)
  const action = useAction()
  const [filter, setFilter] = useState('all')
  const [dialog, setDialog] = useState<ClaimDialog>()
  const claims = data.data?.claims ?? []
  const profile = data.data?.profile
  const active = claims.filter(claim => claim.status !== 'SUPERSEDED')
  const visible = claims.filter(claim => filter === 'all' ? claim.status !== 'SUPERSEDED'
    : filter === 'history' ? claim.status === 'SUPERSEDED' : claim.origin === filter)
  const gaps = (profile?.document.knowledge_gaps ?? []).filter(gap =>
    !active.some(claim => claim.field_key === `user_answer:${gap.question}` && claim.status === 'APPROVED'))
  const pending = active.filter(claim => claim.status === 'UNREVIEWED')
  const unresolved = pending.length > 0 || gaps.length > 0
  return <>
    <ErrorNote error={data.error ?? action.error}/>
    {data.loading ? <Busy/> : <div className="two-col">
      <Panel title="עובדות המותג" note="בדקו את ההבחנה בין ראיות, השערות ותיקוני בעל המותג.">
        <label>סינון עובדות<select value={filter} onChange={event => setFilter(event.target.value)}>
          <option value="all">עובדות פעילות</option><option value="observed">נצפה במקור</option>
          <option value="inferred">הסקות ותיקונים</option><option value="history">היסטוריית תיקונים</option>
        </select></label>
        {!visible.length && <Empty title="אין עובדות בתצוגה זו" action={!profile && <button onClick={() => navigate('onboarding')}>להקמת המותג</button>}/>}
        <ul className="rows">{visible.map(claim => <li className="claim-row" key={claim.id}>
          <div><small>{claimKeys[claim.field_key] ?? claim.field_key}</small><strong dir="auto">{display(claim.value)}</strong>
            <span className="pill">{claim.is_user_override ? 'הזנת בעל המותג' : claim.origin === 'observed' ? 'נצפה במקור' : 'הסקה לבדיקה'}</span></div>
          <div className="actions">
            <Status value={claim.status}/>
            {claim.quoted_span && <button onClick={() => setDialog({kind: 'source', claim})}>ציטוט ומקור</button>}
            {claim.status !== 'SUPERSEDED' && <button onClick={() => setDialog({kind: 'edit', claim})}>תיקון</button>}
            {claim.status === 'UNREVIEWED' && <button disabled={action.pending} onClick={() => void action.run(async () => {
              await write(base + '/claims/' + claim.id + '/approve')
              data.refresh()
              notify('העובדה אושרה')
            })}>אישור עובדה</button>}
          </div>
        </li>)}</ul>
      </Panel>
      <div className="stack">
        <Panel title="סקירת הפרופיל">
          {profile ? <><Status value={profile.status}/><p>גרסה {profile.version}</p>
            {(profile.document.conflicts ?? []).map((conflict, index) => <p className="notice error" key={index}>{conflict.description}</p>)}
            {profile.status !== 'APPROVED' && <button className="primary" disabled={unresolved || profile.status === 'DRAFT'}
              onClick={() => setDialog({kind: 'approve'})}>אישור פרופיל המותג</button>}
            {unresolved && <p className="muted">יש להשלים {pending.length} בדיקות ו־{gaps.length} פערי ידע.</p>}
            {profile.status === 'APPROVED' && <button onClick={() => navigate('design')}>לשפה החזותית</button>}</> : <Empty title="עדיין אין פרופיל לבדיקה"/>}
        </Panel>
        <Panel title="פערי ידע">
          {gaps.length ? <ul className="rows">{gaps.map(gap => <li key={gap.question}><div><b>{gap.question}</b>
            <small>{gap.reason}</small></div><button onClick={() => setDialog({kind: 'gap', question: gap.question})}>השלמת תשובה</button></li>)}</ul>
          : <p className="muted">אין פערי ידע פתוחים.</p>}
        </Panel>
      </div>
    </div>}
    {dialog?.kind === 'source' && <Dialog title="המקור והציטוט המדויק" drawer onClose={() => setDialog(undefined)}>
      <blockquote dir="auto">{dialog.claim.quoted_span}</blockquote>
      {dialog.claim.source?.url && <SourceLink url={dialog.claim.source.url}>{dialog.claim.source.url}</SourceLink>}
    </Dialog>}
    {(dialog?.kind === 'edit' || dialog?.kind === 'gap') && <Dialog title={dialog.kind === 'edit' ? 'תיקון עובדה' : dialog.question}
      onClose={() => setDialog(undefined)}>
      <form className="stack" onSubmit={event => {
        event.preventDefault()
        const answer = String(new FormData(event.currentTarget).get('answer')).trim()
        if (!answer) return
        void action.run(async () => {
          if (dialog.kind === 'edit') await write(base + '/claims/' + dialog.claim.id + '/override', {value: answer})
          else await write(base + '/knowledge-gaps', {question: dialog.question, answer})
          data.refresh()
          setDialog(undefined)
          notify('התשובה נשמרה כעובדה מאושרת')
        })
      }}>
        <label>העובדה המדויקת<textarea name="answer" required defaultValue={dialog.kind === 'edit' ? display(dialog.claim.value) : ''}/></label>
        <ErrorNote error={action.error}/>
        <button className="primary" disabled={action.pending}>שמירה</button>
      </form>
    </Dialog>}
    {dialog?.kind === 'approve' && <Confirm title="אישור פרופיל המותג" onClose={() => setDialog(undefined)}
      action={async () => {await write(base + '/profile/approve'); data.refresh(); notify('פרופיל המותג אושר')}}>
      האישור שומר גרסה חדשה של הפרופיל ומאפשר להתקדם לאסטרטגיה אחרי אישור השפה החזותית.
    </Confirm>}
  </>
}

const designDefaults: DesignFields = {
  colors: {primary: '#222820', secondary: '#A6CBBB', accent: '#D8EB9B'},
  font_pairings: {hebrew_family: 'Heebo', english_family: 'Inter'},
  logo_safe_zones: {safe_zone_percent: 6, minimum_clear_space: '1x'},
  layout_templates: ['editorial_hero', 'educational_card'],
  flux_style_prefix: 'Editorial photography with space for overlays, text-free',
  flux_constraints: {text_free: true, no_letters: true, no_logos: true},
}

function contrast(a: string, b: string) {
  const luminance = (hex: string) => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((value, i) => (value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4) * [.2126, .7152, .0722][i])
    .reduce((sum, value) => sum + value, 0)
  const x = luminance(a), y = luminance(b)
  return (Math.max(x, y) + .05) / (Math.min(x, y) + .05)
}

export function DesignStudio(props: ScreenProps) {
  const load = useCallback((signal: AbortSignal) => api<Design | null>(props.base + '/design-system?allow_empty=true', signal), [props.base])
  const view = useQuery(load)
  return <><ErrorNote error={view.error}/>{view.loading ? <Busy/> : view.data !== undefined &&
    <DesignEditor key={view.data?.id ?? 'new'} {...props} initial={view.data} onSave={view.setData}/>}</>
}

function DesignEditor({initial, base, capabilities, notify, onSave}: ScreenProps & {
  initial: Design | null; onSave: (design: Design) => void
}) {
  const [draft, setDraft] = useState<DesignFields>(() => initial ? {
    colors: {...designDefaults.colors, ...initial.colors},
    font_pairings: {...designDefaults.font_pairings, ...initial.font_pairings},
    logo_safe_zones: {...designDefaults.logo_safe_zones, ...initial.logo_safe_zones},
    layout_templates: initial.layout_templates, flux_style_prefix: initial.flux_style_prefix,
    flux_constraints: initial.flux_constraints,
  } : designDefaults)
  const [dirty, setDirty] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const action = useAction()
  const update = (value: Partial<DesignFields>) => {setDraft(current => ({...current, ...value})); setDirty(true)}
  const save = () => write<Design>(base + '/design-system', draft, 'PUT')
  const zone = draft.logo_safe_zones.safe_zone_percent
  const logo = draft.logo_safe_zones.asset_id
  return <>
    <ErrorNote error={action.error}/>
    <div className="two-col">
      <Panel title="פלטת צבעים" note="חלוקת צבעים מומלצת בעיצוב. בדקו ניגודיות לטקסט רגיל לפי WCAG AA.">
        <div className="palette-band">{(['primary', 'secondary', 'accent'] as const).map((key, i) =>
          <span key={key} style={{flex: [60, 30, 10][i], background: draft.colors[key]}}/>)}</div>
        {(['primary', 'secondary', 'accent'] as const).map((key, i) => {
          const background = draft.colors[key]
          const color = /^#[\da-f]{6}$/i.test(background) ? background : '#222820'
          const dark = contrast(color, '#000000')
          const light = contrast(color, '#FFFFFF')
          const ratio = Math.max(dark, light)
          return <div className="palette-row" key={key}>
            <label><span>{['ראשי · 60%', 'משני · 30%', 'הדגשה · 10%'][i]}</span>
              <input type="color" value={color} onChange={event => update({colors: {...draft.colors, [key]: event.target.value}})}/>
            </label>
            <bdi>{color.toUpperCase()}</bdi><span className="pill ok" title={`לבן ${light.toFixed(2)} · שחור ${dark.toFixed(2)}`}>
              AA · {ratio.toFixed(2)}:1 · {dark > light ? 'טקסט שחור' : 'טקסט לבן'}
            </span>
          </div>
        })}
      </Panel>
      <Panel title="לוגו ומרווח בטוח" note="אזור החשיפה המקווקו מסמן את המרווח סביב הסמל.">
        <div className="logo-stage" style={{padding: `${zone}%`}}><div className="safe-zone">
          {logo ? <img src={apiUrl(base + '/assets/' + logo + '/preview')} alt="לוגו המותג"/> : <span>לוגו</span>}
        </div></div>
        <label>מרווח בטוח · {zone}%<input type="range" min="0" max="20" value={zone} onChange={event =>
          update({logo_safe_zones: {...draft.logo_safe_zones, safe_zone_percent: Number(event.target.value)}})}/></label>
        <label>העלאת לוגו<input type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" disabled={action.pending}
          onChange={event => {
            const selected = event.target.files?.[0]
            if (!selected) return
            void action.run(async () => {
              if (selected.size > 25 * 1024 * 1024) throw new Error('אפשר להעלות עד 25 MiB')
              const form = new FormData()
              form.append('file', selected)
              const asset = await write<Asset>(base + '/assets?kind=logo', form)
              update({logo_safe_zones: {...draft.logo_safe_zones, asset_id: asset.id}})
              notify('הלוגו הועלה')
            })
          }}/></label>
      </Panel>
      <Panel title="טיפוגרפיה">
        {(['hebrew_family', 'english_family'] as const).map((key, i) => <label key={key}>{i ? 'גופן באנגלית' : 'גופן בעברית'}
          <select value={draft.font_pairings[key]} onChange={event => update({font_pairings: {...draft.font_pairings, [key]: event.target.value}})}>
            {fonts.map(([name, file]) => <option value={name} key={name}>{name}{capabilities?.fonts.includes(file) ? '' : ' · חסר מקומית'}</option>)}
          </select></label>)}
        <div className="type-preview">
          <strong style={{fontFamily: fontStyle(draft.font_pairings.hebrew_family, capabilities?.fonts)}}>רעיון טוב מתחיל בפרט אמיתי.</strong>
          <p dir="ltr" style={{fontFamily: fontStyle(draft.font_pairings.english_family, capabilities?.fonts)}}>Good brands feel unmistakably themselves.</p>
        </div>
      </Panel>
      <Panel title="שפת צילום">
        <label>הנחיות צילום וחלל פנוי<textarea rows={5} value={draft.flux_style_prefix} dir="auto"
          onChange={event => update({flux_style_prefix: event.target.value})}/></label>
        <p className="muted">הטיפוגרפיה והלוגו מורכבים בשכבות נפרדות מעל הצילום.</p>
      </Panel>
    </div>
    <footer className="page-actions">
      {initial && <Status value={dirty ? 'DRAFT' : initial.status}/>}
      <button disabled={action.pending || !dirty && !!initial} onClick={() => void action.run(async () => {
        onSave(await save()); notify('גרסת טיוטה נשמרה')
      })}>שמירת טיוטה</button>
      <button className="primary" disabled={action.pending || !dirty && initial?.status === 'APPROVED'}
        onClick={() => setConfirm(true)}>אישור השפה החזותית</button>
      {initial?.status === 'APPROVED' && !dirty && <button onClick={() => navigate('strategy')}>לאסטרטגיה</button>}
    </footer>
    {confirm && <Confirm title="אישור השפה החזותית" onClose={() => setConfirm(false)} action={async () => {
      if (dirty || !initial) await save()
      onSave(await write<Design>(base + '/design-system/approve'))
      notify('השפה החזותית אושרה')
    }}>הגרסה המאושרת תשמש את תוכניות המותג ואת ההפקות הבאות.</Confirm>}
  </>
}
