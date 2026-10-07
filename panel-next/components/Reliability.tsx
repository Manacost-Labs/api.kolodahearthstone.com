'use client';
import { useState } from 'react';
import view from '@/lib/vendor/parsing-reliability.cjs';
import { record, text } from '@/lib/model';
import { Value } from './Details';
export function Reliability({data}:{data:unknown}) {
  const [window,setWindow]=useState('24h');const model=view.buildReliabilityViewModel(data,window);
  const windows=Array.isArray(model.windows)?model.windows.map(String):[];
  const quality=record(model.verifiedCompleteness);
  return <section className="reliability"><div className="data-status"><h2>Надёжность сбора</h2><span className="badge info">{text(model.badge)}</span><select aria-label="Окно надёжности" value={window} onChange={e=>setWindow(e.target.value)}>{(windows.length?windows:['24h']).map(w=><option key={w}>{w}</option>)}</select></div><div className="summary-grid">{[['Полный свежий сбор',model.fullFresh],['Доступность данных',model.availability],['Принятая свежесть',model.acceptedFresh],['Покрытие наблюдений',model.coverage]].map(([name,value])=><article key={String(name)}><span>{text(name)}</span><strong>{text(value)}</strong></article>)}</div><p className="muted">{text(model.message)}</p><div className="data-status"><span>Попыток: {text(model.totalAttempts)}</span><span>Подходящих: {text(model.eligibleAttempts)}</span><span>Ошибки: {text(record(model.counts).failed)}</span><span>Тайм-ауты: {text(record(model.counts).timedOut)}</span><span>LKG: {text(record(model.counts).lkg)}</span></div><details className="extra-data"><summary>Полнота извлечения и публикации</summary><Value value={quality}/><Value value={model.scheduledReliability}/><Value value={model.freshnessSlo}/></details></section>;
}
