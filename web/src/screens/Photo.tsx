import {useCallback, useEffect, useState} from 'react'
import {ImagePlus, Scan, Sparkles, UploadCloud} from 'lucide-react'
import {api, apiUrl, write} from '../api'
import {navigate} from '../App'
import type {ScreenProps} from '../App'
import {aspects, modes, platformLabel} from '../models'
import type {Aspect, Asset, Brief, Mode, Platform, Visual} from '../models'
import {Empty, ErrorNote, Panel, PlatformSelect, useAction, useQuery} from '../ui'
import {VisualPreview} from '../visual'

const modeIcons = [ImagePlus, Scan, Sparkles]

export function PhotoStudio({base, notify}: ScreenProps) {
  const load = useCallback(async (signal: AbortSignal) => {
    const [assets, briefs] = await Promise.all([api<Asset[]>(base + '/assets', signal), api<Brief[]>(base + '/on-demand-photo-briefs', signal)])
    return {assets: assets.filter(asset => ['image/png', 'image/jpeg', 'image/webp'].includes(asset.metadata.media_type)), briefs}
  }, [base])
  const view = useQuery(load)
  const action = useAction()
  const [assetId, setAssetId] = useState('')
  const [upload, setUpload] = useState<File>()
  const [preview, setPreview] = useState('')
  const [dragging, setDragging] = useState(false)
  const [note, setNote] = useState('')
  const [mode, setMode] = useState<Mode>('original_branded')
  const [platform, setPlatform] = useState<Platform>('instagram')
  const [brief, setBrief] = useState<Brief>()
  const [visual, setVisual] = useState<Visual>()
  const [aspect, setAspect] = useState<Aspect>('4:5')

  useEffect(() => {
    if (!upload) return
    const url = URL.createObjectURL(upload)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [upload])

  function select(file?: File) {
    if (!file) return
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
      action.setError('בחרו קובץ JPEG, PNG או WebP.')
      return
    }
    if (file.size > 25 * 1024 * 1024) {
      action.setError('התמונה גדולה מדי. אפשר להעלות קובץ עד 25 MiB.')
      return
    }
    action.setError(null); setPreview(''); setUpload(file); setAssetId(''); setBrief(undefined); setVisual(undefined)
  }
  const original = upload ? preview : assetId ? apiUrl(base + '/assets/' + assetId + '/preview') : ''
  return <>
    <ErrorNote error={action.error ?? view.error}/>
    <form onSubmit={event => {
      event.preventDefault()
      void action.run(async () => {
        let id = assetId
        if (upload) {
          const form = new FormData()
          form.append('file', upload)
          id = (await write<Asset>(base + '/assets?kind=product_photo', form)).id
          setUpload(undefined); setAssetId(id); view.refresh()
        }
        const result = await write<Brief>(base + '/on-demand-photo-briefs',
          {source_asset_id: id, prompt: note.trim(), treatment_mode: mode, platform})
        setBrief({...result, prompt: note.trim()}); setVisual(undefined); view.refresh(); notify('בריף הצילום נשמר')
      })
    }}>
      <div className="two-col">
        <Panel title="תמונה ואירוע" note="העלו צילום אמיתי בגודל של עד 25 MiB.">
          <label className={`dropzone ${dragging ? 'dragging' : ''}`} onDragOver={event => {
            event.preventDefault(); if (!action.pending) setDragging(true)
          }} onDragLeave={() => setDragging(false)} onDrop={event => {
            event.preventDefault(); setDragging(false); if (!action.pending) select(event.dataTransfer.files[0])
          }}>
            {original ? <img src={original} alt="התמונה המקורית"/> : <UploadCloud size={30}/>}
            גררו תמונה או בחרו קובץ
            <input type="file" accept="image/png,image/jpeg,image/webp" disabled={action.pending}
              onChange={event => select(event.target.files?.[0])}/>
          </label>
          {!!view.data?.assets.length && <label>בחירה מקבצים שמורים<select value={assetId} disabled={action.pending} onChange={event => {
            setAssetId(event.target.value); setUpload(undefined); setBrief(undefined); setVisual(undefined)
          }}><option value="">בחרו תמונה</option>{view.data.assets.map(asset => <option key={asset.id} value={asset.id}>
            {asset.kind} · {asset.metadata.width}×{asset.metadata.height} · {asset.id.slice(0, 6)}
          </option>)}</select></label>}
          <label>מה קורה, מתי, ומה חשוב לציין?<textarea rows={4} required maxLength={2000} dir="auto" value={note} onChange={event => {
            setNote(event.target.value); setBrief(undefined); setVisual(undefined)
          }}/><small>{note.length} / 2000</small></label>
          <PlatformSelect value={platform} onChange={value => {setPlatform(value); setBrief(undefined); setVisual(undefined)}}/>
        </Panel>
        <Panel title="השוואת טיפולים" note="ההשוואה ממחישה את הטיפולים. הפקה יוצרת תוצאה נפרדת.">
          <fieldset className="modes"><legend className="sr-only">טיפול בתמונה</legend>{modes.map((item, index) => {
            const Icon = modeIcons[index]
            return <label key={item.id} className={mode === item.id ? 'mode selected' : 'mode'}>
              <input type="radio" name="mode" checked={mode === item.id} value={item.id} onChange={() => {
                setMode(item.id); setBrief(undefined); setVisual(undefined)
              }}/>
              <span className={'mode-illustration mode-' + index}>{original ? <img src={original} alt=""/> : <Icon size={20}/>}</span>
              <span><b>{item.title}</b><small>{item.description}</small></span>
            </label>
          })}</fieldset>
          <p className="muted">התמונה המקורית נשמרת בכל אחד מהמצבים.</p>
        </Panel>
      </div>
      <footer className="page-actions"><button className="primary" disabled={action.pending || !(upload || assetId) || !note.trim()}>
        {action.pending ? 'הפעולה מתבצעת…' : 'שמירת בריף צילום'}</button>
      </footer>
    </form>
    <Panel title="בריפים שמורים">
      {view.data?.briefs.length ? <ul className="rows">{view.data.briefs.map(row => <li key={row.id}>
        <div><b dir="auto">{row.prompt}</b><small>{platformLabel[row.platform]} · {modes.find(item => item.id === row.treatment_mode)?.title}</small></div>
        <button onClick={() => {setBrief(row); setVisual(undefined)}}>פתיחה</button>
      </li>)}</ul> : <Empty title="עדיין אין בריפים לצילום"/>}
      {brief && <div className="stack">
        <p>בריף נבחר · <bdi>{brief.prompt}</bdi></p>
        <div className="actions">
          <button className="primary" onClick={() => navigate('content', 'brief:' + brief.id)}>יצירת תוכן מהבריף</button>
          <button onClick={() => navigate('calendar')}>ללוח התוכן</button>
          <label>יחס תצוגה<select value={aspect} onChange={event => setAspect(event.target.value as Aspect)}>
            {aspects.map(value => <option key={value}>{value}</option>)}</select></label>
          <button disabled={action.pending} onClick={() => void action.run(async () => {
            setVisual(await write<Visual>(base + '/visual-assets/render', {on_demand_brief_id: brief.id, aspect}))
          })}>הפקת תצוגה מהבריף</button>
        </div>
        {visual && <VisualPreview key={visual.id} base={base} visual={visual}/>}
      </div>}
    </Panel>
  </>
}
