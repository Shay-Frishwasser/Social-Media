import {useEffect, useState} from 'react'
import {file, saveFile} from './api'
import type {Visual} from './models'
import {Busy, ErrorNote, useAction} from './ui'

export function VisualPreview({base, visual}: {base: string; visual: Visual}) {
  const [page, setPage] = useState(1)
  const [pdfBlob, setPdfBlob] = useState<Blob>()
  const [pdfUrl, setPdfUrl] = useState('')
  const [url, setUrl] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(true)
  const action = useAction()
  const pngs = visual.files.filter(item => item.format === 'png')
  const pdf = visual.files.find(item => item.format === 'pdf')
  const path = base + '/visual-assets/' + visual.id + '/download'
  useEffect(() => {
    let mounted = true
    let objectUrl = ''
    void file(path + '?page=' + page).then(blob => {
      if (!mounted) return
      objectUrl = URL.createObjectURL(blob)
      setUrl(objectUrl)
      setError(null)
    }).catch(reason => {if (mounted) setError(reason.message)}).finally(() => {
      if (mounted) setPending(false)
    })
    return () => {mounted = false; if (objectUrl) URL.revokeObjectURL(objectUrl)}
  }, [path, page])
  useEffect(() => {
    if (!pdfBlob) {
      setPdfUrl('')
      return
    }
    const objectUrl = URL.createObjectURL(pdfBlob)
    setPdfUrl(objectUrl)
    return () => URL.revokeObjectURL(objectUrl)
  }, [pdfBlob])

  return <div className="stack preview">
    {pending ? <Busy label="טוען תצוגה…"/> : error ? <ErrorNote error={error}/> : <img src={url} alt={`תוצר חזותי · עמוד ${page}`}/>}
    <div className="actions">
      {pngs.length > 1 && <label>עמוד בקרוסלה<select value={page} onChange={event => {
        setPending(true); setPage(Number(event.target.value))
      }}>{pngs.map(item => <option key={item.name} value={item.page ?? 1}>{item.page}</option>)}</select></label>}
      {pdf && <button disabled={action.pending} onClick={() => {
        if (pdfBlob) setPdfBlob(undefined)
        else void action.run(async () => setPdfBlob(await file(path + '?format=pdf')))
      }}>{pdfBlob ? 'סגירת תצוגת PDF' : 'תצוגת קרוסלת PDF'}</button>}
      {pdf && <button disabled={action.pending} onClick={() => void action.run(async () => {
        saveFile(await file(path + '?format=pdf'), pdf.name)
      })}>הורדת קרוסלת PDF</button>}
    </div>
    {pdfBlob && pdfUrl && <iframe title="תצוגת קרוסלת PDF" src={pdfUrl}/>}
    <ErrorNote error={action.error}/>
  </div>
}
