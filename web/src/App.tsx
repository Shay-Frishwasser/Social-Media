import {useCallback, useEffect, useState} from 'react'
import {BookOpen, CalendarDays, CircleDot, ImagePlus, Menu, Palette, Plus, Send, Sparkles, Workflow} from 'lucide-react'
import {api, brandPath, write} from './api'
import {useLocalFonts} from './fonts'
import type {Brand, Capabilities, Job} from './models'
import {Busy, Dialog, Empty, ErrorNote, Status, useAction, useQuery} from './ui'
import {Onboarding, Identity, DesignStudio} from './screens/Brand'
import {Calendar, StrategyStudio} from './screens/Planning'
import {PhotoStudio} from './screens/Photo'
import {ContentStudio} from './screens/Content'
import {Publishing} from './screens/Publishing'

const pages = {
  onboarding: {title: 'הקמת מותג', note: 'התחילו במחקר פומבי או הזינו את עובדות המותג בעצמכם.', icon: CircleDot},
  identity: {title: 'זהות ואימות עובדות', note: 'בדקו את הראיות ואשרו את פרופיל המותג.', icon: BookOpen},
  design: {title: 'שפה חזותית', note: 'הגדירו שפה עקבית לצבע, לטיפוגרפיה ולצילום.', icon: Palette},
  strategy: {title: 'אסטרטגיה וכללי פלטפורמה', note: 'בנו תוכנית עם עמודי תוכן, סגנון כתיבה וכללים לכל ערוץ.', icon: Workflow},
  calendar: {title: 'לוח תוכן', note: 'תכננו ושריינו משבצות תוכן בשעון ישראל.', icon: CalendarDays},
  photo: {title: 'סטודיו לפוסט תמונה', note: 'הפכו תמונת מוצר אמיתית לפוסט בשפת המותג.', icon: ImagePlus},
  content: {title: 'סטודיו תוכן', note: 'כתבו, בדקו מקורות והכינו תוצרים מהגרסה הנוכחית.', icon: Sparkles},
  publishing: {title: 'פרסום ומסירה', note: 'בדקו את הדרישות לפני ייצוא קבצים או שליחה לערוץ.', icon: Send},
}

export type Page = keyof typeof pages
const pageNames = Object.keys(pages) as Page[]
const readPage = (): {page: Page; selection?: string} => {
  const [page, selection] = location.hash.slice(1).split('/')
  return {page: pageNames.includes(page as Page) ? page as Page : 'onboarding', selection}
}
export const navigate = (page: Page, selection?: string) => {
  location.hash = selection ? `${page}/${selection}` : page
}

export type ScreenProps = {
  brand: Brand
  base: string
  capabilities?: Capabilities
  selection?: string
  notify: (text: string) => void
}

const screens = {
  onboarding: Onboarding, identity: Identity, design: DesignStudio, strategy: StrategyStudio,
  calendar: Calendar, photo: PhotoStudio, content: ContentStudio, publishing: Publishing,
}

const loadBrands = (signal: AbortSignal) => api<Brand[]>('/brands', signal)
const loadCaps = (signal: AbortSignal) => api<Capabilities>('/workspace', signal)
const jobNames: Record<string, string> = {
  onboarding_research: 'מחקר מותג', content_generation: 'יצירת תוכן',
  visual_render: 'הפקה חזותית', publish: 'מסירה לפרסום',
}

export default function App() {
  const [route, setRoute] = useState(readPage)
  const [brandId, setBrandId] = useState(() => localStorage.getItem('brandai.active'))
  const [dialog, setDialog] = useState<'new' | 'jobs' | 'nav' | null>(null)
  const [notice, setNotice] = useState('')
  const brandList = useQuery(loadBrands)
  const capabilities = useQuery(loadCaps)
  useLocalFonts(capabilities.data?.fonts)
  const brand = brandList.data?.find(row => row.id === brandId) ?? brandList.data?.[0]
  const base = brand ? brandPath(brand.id) : ''
  const loadJobs = useCallback((signal: AbortSignal) => base ? api<Job[]>(base + '/jobs', signal) : Promise.resolve([]), [base])
  const jobs = useQuery(loadJobs, base ? 2500 : 0)
  const activeJobs = jobs.data?.filter(job => ['RUNNING', 'QUEUED', 'RETRY_WAIT'].includes(job.status)) ?? []

  useEffect(() => {
    const handle = () => setRoute(readPage())
    window.addEventListener('hashchange', handle)
    return () => window.removeEventListener('hashchange', handle)
  }, [])
  useEffect(() => {
    if (!notice) return
    const timer = setTimeout(() => setNotice(''), 4200)
    return () => clearTimeout(timer)
  }, [notice])

  function choose(id: string) {
    localStorage.setItem('brandai.active', id)
    setBrandId(id)
    if (route.selection) navigate(route.page)
  }

  const page = pages[route.page]
  const Screen = screens[route.page]

  const navigation = <>
    <div className="brand-logo"><span className="mark">BA</span><strong dir="ltr">BrandAI</strong></div>
    <label className="switcher">מותג פעיל<select aria-label="מותג פעיל" value={brand?.id ?? ''} onChange={event => choose(event.target.value)}>
      {!brand && <option value="">בחרו מותג</option>}
      {brandList.data?.map(row => <option value={row.id} key={row.id}>{row.name}</option>)}
    </select></label>
    <button className="new-brand" onClick={() => setDialog('new')}><Plus size={17}/> מותג חדש</button>
    <nav aria-label="סביבת עבודה">
      {pageNames.map(key => {
        const Icon = pages[key].icon
        return <a href={'#' + key} key={key} aria-current={route.page === key ? 'page' : undefined}
          onClick={() => setDialog(null)}><Icon size={17} aria-hidden/>{pages[key].title}</a>
      })}
    </nav>
    <button className="jobs-link" onClick={() => setDialog('jobs')}>
      <span className={activeJobs.length ? 'status-dot pulse' : 'status-dot'}/>
      {jobs.error ? 'תור לא זמין' : activeJobs.length ? `${activeJobs.length} משימות פעילות` : 'עיבוד מקומי'}
    </button>
  </>

  return <div className={`workspace ${capabilities.data?.fonts.includes('heebo') ? 'font-heebo' : ''} ${capabilities.data?.fonts.includes('inter') ? 'font-inter' : ''}`}>
    <a className="skip" href="#main" onClick={event => {event.preventDefault(); document.getElementById('main')?.focus()}}>דילוג לתוכן</a>
    <aside className="rail">{navigation}</aside>
    <div className="mobile-bar"><button className="icon-button" aria-label="תפריט" onClick={() => setDialog('nav')}><Menu/></button>
      <strong><bdi>{brand?.name ?? 'BrandAI'}</bdi></strong><button onClick={() => setDialog('jobs')}>משימות {activeJobs.length || ''}</button></div>
    <main id="main" tabIndex={-1}>
      <div className="page-frame" key={route.page + ':' + (brand?.id ?? '')}>
        <header className="page-header"><div className="crumb"><bdi>{brand?.name ?? 'BrandAI'}</bdi><span>/</span>{page.title}</div>
          <h1>{page.title}</h1><p>{page.note}</p></header>
        <ErrorNote error={brandList.error ?? capabilities.error}/>
        {brandList.loading ? <Busy/> : brand ? <Screen brand={brand} base={base} capabilities={capabilities.data}
          selection={route.selection} notify={setNotice}/> :
          <Empty title="עדיין אין מותגים" action={<button className="primary" onClick={() => setDialog('new')}>יצירת מותג ראשון</button>}>
            לכל מותג יש פרופיל, קבצים ותוכנית תוכן משלו.
          </Empty>}
      </div>
    </main>
    <div className="live-toast" role="status">{notice && <span>✓ {notice}</span>}</div>
    {dialog === 'nav' && <Dialog title="סביבת עבודה" drawer onClose={() => setDialog(null)}>{navigation}</Dialog>}
    {dialog === 'new' && <NewBrand onClose={() => setDialog(null)} onCreate={row => {
      brandList.setData([row, ...brandList.data ?? []])
      choose(row.id)
      navigate('onboarding')
      setDialog(null)
    }}/>}
    {dialog === 'jobs' && <Dialog title="משימות ועיבוד מקומי" drawer onClose={() => setDialog(null)}>
      <ErrorNote error={jobs.error}/>
      {jobs.loading ? <Busy/> : jobs.data?.length ? <ul className="rows">{jobs.data.map(job => <li key={job.id}>
        <div><b>{jobNames[job.kind] ?? job.kind}</b><small>ניסיון {job.attempts} · <bdi>{new Date(job.updated_at).toLocaleString('he-IL', {timeZone: 'Asia/Jerusalem'})}</bdi></small>
          {job.error_code && <small dir="ltr">{job.error_code}</small>}</div><Status value={job.status}/>
      </li>)}</ul> : <Empty title="אין משימות בתור"/>}
    </Dialog>}
  </div>
}

function NewBrand({onClose, onCreate}: {onClose: () => void; onCreate: (brand: Brand) => void}) {
  const [name, setName] = useState('')
  const action = useAction()
  return <Dialog title="מותג חדש" onClose={onClose}>
    <form className="stack" onSubmit={event => {
      event.preventDefault()
      void action.run(async () => onCreate(await write<Brand>('/brands', {name: name.trim()})))
    }}>
      <label>שם המותג<input autoFocus required maxLength={250} value={name} onChange={event => setName(event.target.value)}/></label>
      <ErrorNote error={action.error}/>
      <button className="primary" disabled={!name.trim() || action.pending}>יצירת מותג</button>
    </form>
  </Dialog>
}
