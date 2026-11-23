import {useLayoutEffect} from 'react'
import {apiUrl} from './api'

export const fonts = [
  ['Heebo', 'heebo', '100 900'], ['Assistant', 'assistant', '200 800'], ['Rubik', 'rubik', '300 900'],
  ['Noto Sans Hebrew', 'noto-sans-hebrew', '100 900'], ['Inter', 'inter', '100 900'],
]

export function useLocalFonts(installed?: string[]) {
  useLayoutEffect(() => {
    if (!installed?.length) return
    const faces = fonts.filter(([, key]) => installed.includes(key)).map(([family, key, weight]) =>
      new FontFace(family, `url(${apiUrl('/fonts/' + key)})`, {weight, display: 'swap'}))
    faces.forEach(face => document.fonts.add(face))
    return () => faces.forEach(face => document.fonts.delete(face))
  }, [installed])
}

export function fontStyle(name: string, installed: string[] = []) {
  return installed.includes(fonts.find(([value]) => value === name)?.[1] ?? '') ? `"${name}", sans-serif` : 'system-ui, sans-serif'
}
