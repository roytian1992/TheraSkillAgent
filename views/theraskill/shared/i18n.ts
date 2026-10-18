import {useSyncExternalStore} from 'react'
import catalog from './catalog.json'
export type Locale='zh'|'en'
const KEY='theraskill-locale'
const EVENT='theraskill-locale-changed'
const translations:Record<string,string>=catalog
const subscribe=(listener:()=>void)=>{window.addEventListener(EVENT,listener);window.addEventListener('storage',listener);return()=>{window.removeEventListener(EVENT,listener);window.removeEventListener('storage',listener)}}
const current=():Locale=>window.localStorage.getItem(KEY)==='en'?'en':'zh'
const reverse:Record<string,string>=Object.fromEntries(Object.entries(translations).map(([zh,en])=>[en,zh]))
export function tr(value:string):string {
  const lead=value.match(/^\s*/)?.[0]||'',tail=value.match(/\s*$/)?.[0]||'',core=value.trim()
  const translated=current()==='zh'?reverse[core]:translations[core]
  return translated?lead+translated+tail:value
}
export function useTranslationSubscription(){return useSyncExternalStore(subscribe,current,()=> 'zh' as Locale)}
export function useLocale(){
  const locale=useTranslationSubscription()
  const setLocale=(value:Locale)=>{window.localStorage.setItem(KEY,value);document.documentElement.lang=value==='en'?'en':'zh-CN';window.dispatchEvent(new Event(EVENT))}
  return {locale,setLocale,toggleLocale:()=>setLocale(locale==='zh'?'en':'zh')}
}
