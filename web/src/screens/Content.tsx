import {useCallback, useEffect, useMemo, useState} from 'react'
import {api, write} from '../api'
import {navigate} from '../App'
import type {ScreenProps} from '../App'
import {dateLabel} from '../dates'
import {aspects, depths, platformLabel} from '../models'
import type {Aspect, Brief, Citation, Claim, Content, ContentSummary, Gate, Revision, Topic, ValidationReport, Visual} from '../models'
import {Busy, Confirm, Empty, ErrorNote, Panel, SourceLink, Status, display, isolate, useAction, useQuery} from '../ui'
import {VisualPreview} from '../visual'

export function ContentStudio(props: ScreenProps) {
  const {base, selection} = props
  const origin = selection?.startsWith('slot:') ? 'slot' : selection?.startsWith('brief:') ? 'brief' : undefined
  const load = useCallback(async (signal: AbortSignal) => {
    const [items, briefs] = await Promise.all([api<ContentSummary[]>(base + '/content', signal),
      api<Brief[]>(base + '/on-demand-photo-briefs', signal)])
    const selected = origin ? undefined : items.find(item => item.id === selection) ?? (selection ? undefined : items[0])
    const current = selected && await api<Content>(base + '/content/' + selected.id, signal)
    return {items, briefs, current}
  }, [base, selection, origin])
  const view = useQuery(load)
  return <>
    <ErrorNote error={view.error}/>
    {view.loading ? <Busy/> : view.data && <>
      <div className="selection-bar">
        <label>תוכן שמור<select value={view.data.current?.id ?? ''} onChange={event => navigate('content', event.target.value || 'new')}>
          <option value="">בחרו פריט</option>{view.data.items.map(item => <option value={item.id} key={item.id}>
            {isolate(platformLabel[item.platform])} · {depths[item.content_depth]} · {isolate(dateLabel(item.created_at))}
          </option>)}</select></label>
        <button onClick={() => navigate('content', 'new')}>יצירת תוכן חדש</button>
      </div>
      {view.data.current ? <Editor key={view.data.current.revision.id} {...props} item={view.data.current}
        onUpdate={item => view.setData({...view.data!, current: item})}/> :
        <Generate key={selection} {...props} briefs={view.data.briefs} origin={origin} sourceId={origin ? selection!.slice(origin.length + 1) : ''}/>}
    </>}
  </>
}

function Generate({base, capabilities, briefs, origin, sourceId, notify}: ScreenProps & {
  briefs: Brief[]; origin?: 'slot' | 'brief'; sourceId: string
}) {
  const [briefId, setBriefId] = useState(origin === 'brief' ? sourceId : '')
  const [language, setLanguage] = useState('auto')
  const [topic, setTopic] = useState<Topic>()
  const action = useAction()
  return <div className="two-col">
    <Panel title="יצירת תוכן" note="התחילו ממשבצת משוריינת או מבריף צילום שמור.">
      <form className="stack" onSubmit={event => {
        event.preventDefault()
        const source = origin === 'slot' ? 'from-slot/' + sourceId : 'from-on-demand/' + briefId
        void action.run(async () => {
          const item = await write<Content>(base + '/content/generate/' + source, {
            language, ...(topic ? {research_topic_id: topic.id} : {}),
          })
          notify('התוכן נוצר ועבר בדיקת כללים')
          navigate('content', item.id)
        })
      }}>
        {origin === 'slot' ? <p>מקור: משבצת מהתוכנית המאושרת</p> :
          <label>בריף צילום<select value={briefId} onChange={event => setBriefId(event.target.value)}>
            <option value="">בחרו בריף</option>{briefs.map(brief => <option key={brief.id} value={brief.id}>{brief.prompt}</option>)}
          </select></label>}
        {!briefs.length && origin !== 'slot' && <div className="actions">
          <button type="button" onClick={() => navigate('photo')}>לסטודיו הצילום</button>
          <button type="button" onClick={() => navigate('calendar')}>לבחירת משבצת</button>
        </div>}
        <label>שפת ההצעה<select value={language} onChange={event => setLanguage(event.target.value)}>
          <option value="auto">אוטומטי</option><option value="he">עברית</option><option value="en">אנגלית</option>
        </select></label>
        {topic && <p>מחקר מקושר · <bdi>{topic.topic}</bdi> <button type="button" onClick={() => setTopic(undefined)}>ניתוק</button></p>}
        <ErrorNote error={action.error}/>
        <button className="primary" disabled={action.pending || (!briefId && origin !== 'slot')}>
          {action.pending ? 'יוצר ובודק תוכן…' : 'יצירת תוכן'}</button>
      </form>
    </Panel>
    <Panel title="מחקר להעשרת הפוסט"><Research base={base} enabled={capabilities?.topic_research}
      onAttach={setTopic} attachTopicOnly/></Panel>
  </div>
}

const tabLabels = {validation: 'בדיקת תוכן', research: 'מחקר', visual: 'תוצר חזותי'}
const length = (value: string) => Array.from(value).length
const paragraph = (source: Citation) => `“${source.quoted_span.trim().split(/\s+/).join(' ')}” [${source.label}]`

function Editor({base, notify, capabilities, item, onUpdate}: ScreenProps & {item: Content; onUpdate: (item: Content) => void}) {
  const [history, setHistory] = useState('')
  const current = item.revision
  const revision = item.revisions.find(entry => entry.id === history) ?? current
  const historical = revision.id !== current.id
  const locked = historical || ['SCHEDULED', 'PUBLISHED'].includes(item.status)
  const [copy, setCopy] = useState({hook: current.hook, body: current.body, cta: current.cta, hashtags: current.hashtags.join(' ')})
  const [evidence, setEvidence] = useState(current.cited_claim_ids)
  const [linkedTopic, setLinkedTopic] = useState(current.research_topic_id)
  const [external, setExternal] = useState(current.cited_external_evidence_ids)
  const [tab, setTab] = useState<keyof typeof tabLabels>('validation')
  const [approve, setApprove] = useState(false)
  const action = useAction()
  const loadClaims = useCallback((signal: AbortSignal) => api<Claim[]>(base + '/claims', signal), [base])
  const claims = useQuery(loadClaims)
  const format = item.context.format_rules
  const tags = copy.hashtags.split(/\s+/).filter(Boolean)
  const dirty = copy.hook !== current.hook || copy.body !== current.body || copy.cta !== current.cta
    || tags.join(' ') !== current.hashtags.join(' ') || evidence.join() !== current.cited_claim_ids.join()
    || external.join() !== current.cited_external_evidence_ids.join() || linkedTopic !== current.research_topic_id
  const patch = useMemo(() => ({
    ...copy, hashtags: copy.hashtags.split(/\s+/).filter(Boolean), revision_id: current.id, cited_claim_ids: evidence,
    research_topic_id: linkedTopic, cited_external_evidence_ids: external,
  }), [copy, current.id, evidence, linkedTopic, external])
  const check = useDraftValidation(base + '/content/' + item.id + '/validate',
    tab === 'validation' && !locked && dirty, patch)
  const fields = [
    ['hook', 'פתיח', format.hook_max], ['body', 'גוף התוכן', format.caption_max],
    ['cta', 'קריאה לפעולה', format.cta_max], ['hashtags', 'תגיות', format.hashtag_max],
  ] as const

  const attach = (topic: Topic, citation?: Citation) => {
    const source = citation ?? topic.evidence[0]
    if (!source || locked) return
    const existing = topic.id === linkedTopic ? external : []
    const known = current.external_citations.map(paragraph)
    const nextBody = copy.body.split(/\n\s*\n/).filter(block => topic.id === linkedTopic ||
      (!known.includes(block.trim()) && !/^“.+” \[\d+\]$/s.test(block.trim())))
    if (!nextBody.includes(paragraph(source))) nextBody.push(paragraph(source))
    setCopy({...copy, body: nextBody.filter(Boolean).join('\n\n')})
    setLinkedTopic(topic.id)
    setExternal(Array.from(new Set([...existing, source.id])))
    notify('ציטוט מדויק נוסף לטיוטה')
  }

  return <>
    <ErrorNote error={action.error ?? claims.error}/>
    <div className="two-col">
      <Panel title="עורך תוכן" note={`${platformLabel[item.platform]} · ${depths[item.content_depth]} · גרסה ${revision.version}`}>
        <div className="split"><Status value={item.status}/>
          <label>היסטוריית גרסאות<select value={history} onChange={event => setHistory(event.target.value)}>
            <option value="">גרסה נוכחית</option>{item.revisions.filter(row => row.id !== current.id).map(row =>
              <option value={row.id} key={row.id}>גרסה {row.version}</option>)}</select></label>
        </div>
        {fields.map(([key, label, max]) => {
          const value = historical ? (key === 'hashtags' ? revision.hashtags.join(' ') : revision[key]) : copy[key]
          const count = key === 'hashtags' ? value.split(/\s+/).filter(Boolean).length : length(value)
          return <label key={key}>{label}<textarea rows={key === 'body' ? 7 : 2} dir="auto" value={value}
            readOnly={locked || action.pending} onChange={event => setCopy({...copy, [key]: event.target.value})}/>
            <small className={count > max ? 'warning-text' : ''}><bdi>{count} / {max}</bdi></small></label>
        })}
        <p className="muted">מקסימום {format.caption_max} תווים בכיתוב. {format.link_placement === 'body' ? 'קישורים בגוף בלבד.' : 'קישורים אינם מותרים.'}</p>
        <details><summary>עובדות מותג מצוטטות</summary><div className="stack">
          {claims.data?.filter(claim => claim.status === 'APPROVED').map(claim => <label className="check" key={claim.id}>
            <input type="checkbox" disabled={locked} checked={(historical ? revision.cited_claim_ids : evidence).includes(claim.id)} onChange={event =>
              setEvidence(event.target.checked ? [...evidence, claim.id] : evidence.filter(id => id !== claim.id))}/>
            <bdi>{display(claim.value)}</bdi>
          </label>)}
        </div></details>
      </Panel>
      <Panel title="בדיקה ותצוגה">
        <div className="segmented">{(Object.keys(tabLabels) as (keyof typeof tabLabels)[]).map(key =>
          <button key={key} aria-pressed={tab === key} onClick={() => setTab(key)}>{tabLabels[key]}</button>)}</div>
        {tab === 'validation' && <Validation revision={revision} dirty={dirty && !historical} check={check}/>}
        {tab === 'research' && <>
          {!!revision.external_citations.length && <div className="stack"><h3>מקורות בגרסה השמורה</h3>
            {revision.external_citations.map(source => <Quote key={source.id} source={source}/>)}</div>}
          <Research base={base} enabled={capabilities?.topic_research} contentId={item.id}
            onAttach={locked ? undefined : attach}/>
          {!locked && linkedTopic && <button onClick={() => {
            const registered = current.external_citations.map(paragraph)
            setCopy({...copy, body: copy.body.split(/\n\s*\n/).filter(block =>
              !registered.includes(block.trim()) && !/^“.+” \[\d+\]$/s.test(block.trim())).join('\n\n')})
            setExternal([]); setLinkedTopic(null)
          }}>ניתוק מחקר והסרת הציטוטים</button>}
        </>}
        {tab === 'visual' && !historical && <Compositor base={base} item={item} dirty={dirty} notify={notify}/>}
        {tab === 'visual' && historical && <Empty title="עברו לגרסה הנוכחית לפני הפקה"/>}
      </Panel>
    </div>
    <footer className="page-actions">
      <button disabled={locked || !dirty || action.pending} onClick={() => void action.run(async () => {
        onUpdate(await write<Content>(base + '/content/' + item.id, patch, 'PATCH'))
        notify('גרסה חדשה נשמרה והכללים נבדקו מחדש')
      })}>שמירת גרסה ובדיקה מחדש</button>
      <button className="primary" disabled={locked || dirty || item.status !== 'READY_FOR_REVIEW'} onClick={() => setApprove(true)}>
        אישור הגרסה הנוכחית</button>
      <button disabled={dirty || historical} onClick={() => navigate('publishing', item.id)}>לבדיקת פרסום</button>
    </footer>
    {approve && <Confirm title="אישור הגרסה הנוכחית" onClose={() => setApprove(false)} action={async () => {
      onUpdate(await write<Content>(base + '/content/' + item.id + '/approve', {revision_id: current.id}))
      notify('גרסת התוכן אושרה')
    }}>האישור חל על גרסה {current.version}. מסירה דורשת גם תוצר חזותי תואם.</Confirm>}
  </>
}

function useDraftValidation(path: string, enabled: boolean, draft: object) {
  const [result, setResult] = useState<{draft: object; report?: ValidationReport; error?: string}>()
  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    const timer = setTimeout(() => {
      void write<ValidationReport>(path, draft, 'POST', controller.signal)
        .then(report => setResult({draft, report}))
        .catch(error => {
          if (!controller.signal.aborted) setResult({draft, error: error instanceof Error ? error.message : String(error)})
        })
    }, 650)
    return () => {clearTimeout(timer); controller.abort()}
  }, [path, enabled, draft])
  return enabled && result?.draft === draft ? result : undefined
}

function Validation({revision, dirty, check}: {
  revision: Revision; dirty: boolean; check: ReturnType<typeof useDraftValidation>
}) {
  const report = dirty ? check?.report : revision.validation_report
  return <div className="stack">
    {dirty && <p className="notice">זוהי בדיקת טיוטה. שמירה ובדיקה מחדש יוצרות גרסה לבדיקה ולאישור.</p>}
    {dirty && !check && <Busy label="בודק את הטיוטה…"/>}
    <ErrorNote error={check?.error ?? null}/>
    {report && <Status value={report.valid ? dirty ? 'הטיוטה עברה בדיקה' : 'הגרסה השמורה עברה בדיקה' : 'VALIDATION_FAILED'}/>}
    {!dirty && <p>פנייה בעברית · {revision.hebrew_register_check.applies ? revision.hebrew_register_check.passed ? 'תקינה' : 'נדרש תיקון' : 'לא חל'}</p>}
    {report?.violations.map((issue, index) => <div className="finding" key={index}>
      <strong dir="auto">{issue.message}</strong><small dir="ltr">{issue.field} · {issue.code}</small>
      {Object.keys(issue.details).length > 0 && <pre dir="ltr">{JSON.stringify(issue.details, null, 2)}</pre>}
    </div>)}
    {report && !dirty && <details><summary>ניסיונות בדיקה ({report.attempts.length})</summary>
      <ul>{report.attempts.map(attempt => <li key={attempt.number}>ניסיון {attempt.number} · {attempt.violations.length} ממצאים</li>)}</ul>
    </details>}
  </div>
}

function Quote({source, add}: {source: Citation; add?: () => void}) {
  return <div className="finding">
    <strong><bdi>[{source.label}] {source.title}</bdi></strong>
    <blockquote dir="auto">{source.quoted_span}</blockquote>
    <SourceLink url={source.url}>{source.url}</SourceLink><small>{dateLabel(source.retrieved_at)}</small>
    {add && <button onClick={add}>הוספת הציטוט</button>}
  </div>
}

function Research({base, enabled, contentId, onAttach, attachTopicOnly = false}: {
  base: string; enabled?: boolean; contentId?: string; onAttach?: (topic: Topic, source?: Citation) => void
  attachTopicOnly?: boolean
}) {
  const load = useCallback((signal: AbortSignal) => api<Topic[]>(base + '/research/topics', signal), [base])
  const view = useQuery(load)
  const [query, setQuery] = useState('')
  const [domains, setDomains] = useState('')
  const [refresh, setRefresh] = useState(false)
  const [chosen, setChosen] = useState<Topic>()
  const action = useAction()
  const available = view.data?.filter(item => !item.content_id || item.content_id === contentId) ?? []
  return <div className="stack">
    <form className="stack" onSubmit={event => {
      event.preventDefault()
      void action.run(async () => {
        setChosen(await write<Topic>(base + '/research/topics', {
          topic: query.trim(), domains: domains.split(/[,\s]+/).filter(Boolean), refresh,
          ...(contentId ? {content_id: contentId} : {}),
        }))
        view.refresh()
      })
    }}>
      <label>נושא ציבורי למחקר<input required value={query} maxLength={200} dir="auto" onChange={event => setQuery(event.target.value)}/></label>
      <label>דומיינים מותרים (אופציונלי)<input dir="ltr" placeholder="example.com" value={domains} onChange={event => setDomains(event.target.value)}/></label>
      <label className="check"><input type="checkbox" checked={refresh} onChange={event => setRefresh(event.target.checked)}/> רענון ממקורות חיים</label>
      <button disabled={!enabled || !query.trim() || action.pending}>{action.pending ? 'מאמת מקורות…' : 'חיפוש ואימות ציטוטים'}</button>
      {enabled === false && <small>חיפוש חי כבוי בהגדרות המקומיות. ניתן לפתוח מחקרים שכבר נשמרו.</small>}
    </form>
    <ErrorNote error={view.error ?? action.error}/>
    {available.length > 0 && <label>מחקרים שמורים<select value={chosen?.id ?? ''} onChange={event =>
      setChosen(available.find(item => item.id === event.target.value))}>
      <option value="">בחרו מחקר</option>{available.map(item => <option value={item.id} key={item.id}>{item.topic}</option>)}
    </select></label>}
    {chosen && <>
      <h3 dir="auto">{chosen.topic}</h3>
      {chosen.evidence.map(source => <Quote key={source.id} source={source}
        add={onAttach && !attachTopicOnly ? () => onAttach(chosen, source) : undefined}/>)}
      {attachTopicOnly && onAttach && !!chosen.evidence.length && <button onClick={() => onAttach(chosen)}>שימוש במחקר ביצירת התוכן</button>}
      {!chosen.evidence.length && <Empty title="אין ציטוטים מאומתים למחקר הזה"/>}
    </>}
  </div>
}

function Compositor({base, item, dirty, notify}: {base: string; item: Content; dirty: boolean; notify: (text: string) => void}) {
  const [aspect, setAspect] = useState<Aspect>('4:5')
  const [carousel, setCarousel] = useState(item.content_depth === 'carousel')
  const [selected, setSelected] = useState('')
  const action = useAction()
  const load = useCallback((signal: AbortSignal) => api<Visual[]>(base + '/visual-assets', signal), [base])
  const assets = useQuery(load)
  const matching = assets.data?.filter(row => row.content_revision_id === item.revision.id) ?? []
  const visual = matching.find(row => row.id === selected) ?? matching[0]
  const eligible = !!visual && ['APPROVED', 'PUBLISHED', 'SCHEDULED'].includes(item.status)
  const gateLoad = useCallback((signal: AbortSignal) => eligible
    ? api<Gate>(base + '/publishing/gate/' + item.id + '?visual_asset_id=' + visual.id, signal)
    : Promise.resolve(null), [eligible, base, item.id, visual?.id])
  const gate = useQuery(gateLoad)
  const render = (rerender: boolean) => void action.run(async () => {
    const result = await write<Visual>(base + '/visual-assets/' + (rerender ? visual.id + '/rerender' : 'render'),
      {aspect, carousel, ...(rerender ? {} : {content_id: item.id})})
    assets.setData([result, ...(assets.data ?? [])])
    setSelected(result.id)
    notify('התוצר החזותי נשמר')
  })
  return <div className="stack">
    <label>יחס תצוגה<select value={aspect} onChange={event => setAspect(event.target.value as Aspect)}>
      {aspects.map(value => <option key={value}>{value}</option>)}</select></label>
    <label className="check"><input type="checkbox" checked={carousel} onChange={event => setCarousel(event.target.checked)}/> קרוסלה מרובת עמודים</label>
    <div className="actions">
      <button className="primary" disabled={dirty || action.pending || !item.revision.validation_report.valid}
        onClick={() => render(false)}>הפקת תוצר חזותי</button>
      {visual && <button disabled={dirty || action.pending} onClick={() => render(true)}>הפקה מחדש</button>}
    </div>
    {action.pending && <Busy label="מרכיב תמונה וקבצים…"/>}
    <ErrorNote error={action.error ?? assets.error ?? gate.error}/>
    {visual && <>
      <label>תוצרים לגרסה הנוכחית<select dir="ltr" value={visual.id} onChange={event => setSelected(event.target.value)}>
        {matching.map(row => <option key={row.id} value={row.id}>{row.aspect} · {row.width}×{row.height} · {row.id.slice(0, 6)}</option>)}</select></label>
      <small>ניגודיות <bdi>{visual.layout.contrast.toFixed(2)}:1</bdi> · <bdi>Seed {visual.seed}</bdi></small>
      <small dir="ltr">{Object.values(visual.providers).join(' · ')}</small>
      {eligible && !dirty && gate.data?.allowed && gate.data.visual_asset_id === visual.id
        ? <VisualPreview key={visual.id} base={base} visual={visual}/>
        : <p className="muted">התצוגה הסופית זמינה לאחר אישור ובדיקת פרסום.</p>}
    </>}
  </div>
}
