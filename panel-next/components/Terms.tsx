'use client';
import { useState } from 'react';
import { mutate } from '@/lib/client';
import { text } from '@/lib/model';
import type { PageData } from '@/lib/types';
export function Terms({data}:{data:PageData}) {
  const [query,setQuery]=useState('');const [busy,setBusy]=useState(false);const [status,setStatus]=useState('');
  return <section><header className="page-head"><h1>Переводы Wiki</h1></header><label className="search"><input type="search" aria-label="Поиск термина" placeholder="Найти термин" value={query} onChange={e=>setQuery(e.target.value)}/></label>{status&&<p role="status" className="notice">{status}</p>}<form onSubmit={async e=>{e.preventDefault();const form=new FormData(e.currentTarget);setBusy(true);try{const result=await mutate(form);setStatus(result.message);}catch(error){setStatus(error instanceof Error?error.message:'Ошибка сохранения.');}finally{setBusy(false);}}}>
    <input type="hidden" name="action" value="save_wiki_terms"/><input type="hidden" name="csrf" value={data.csrf}/><div className="terms-grid">{Object.entries(data.terms||{}).map(([type,rows])=><section key={type} className="panel-card"><h2>{data.termLabels?.[type]||type}</h2>{rows.map((row,i)=><label key={text(row.term_en)} hidden={!text(row.term_en).toLowerCase().includes(query.toLowerCase())&&!text(row.term_ru,'').toLowerCase().includes(query.toLowerCase())}><span>{text(row.term_en)}</span><input type="hidden" name={`terms[${type}][${i}][en]`} value={text(row.term_en)}/><input name={`terms[${type}][${i}][ru]`} defaultValue={text(row.term_ru,'')} placeholder="Русский перевод"/></label>)}</section>)}</div><button type="submit" className="button" disabled={busy}>{busy?'Сохраняем…':'Сохранить переводы'}</button></form></section>;
}
