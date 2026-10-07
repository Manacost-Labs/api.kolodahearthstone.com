'use client';
import { useMemo, useState } from 'react';
import { Value } from './Details';
import type { Row } from '@/lib/types';
export function DataTable({rows,columns,onDetail}:{rows:Row[];columns:{key:string;label:string;type?:string}[];onDetail?:(row:Row)=>void}) {
  const [sort,setSort]=useState<{key:string;desc:boolean}|null>(null);
  const [page,setPage]=useState(1);
  const [hidden,setHidden]=useState<string[]>([]);
  const rowKeys=useMemo(()=>new Map(rows.map((row,index)=>[row,index])),[rows]);
  const ordered=useMemo(()=>sort?[...rows].sort((a,b)=>{
    const left=a[sort.key],right=b[sort.key];const numeric=left!==null&&right!==null&&left!==''&&right!==''&&Number.isFinite(Number(left))&&Number.isFinite(Number(right));
    const value=numeric?Number(left)-Number(right):String(left??'').localeCompare(String(right??''),'ru');return sort.desc?-value:value;
  }):rows,[rows,sort]);
  const totalPages=Math.max(1,Math.ceil(ordered.length/25));
  const current=Math.min(page,totalPages);
  const visible=columns.filter((c,i)=>i===0||!hidden.includes(c.key));
  return <><div className="table-tools"><span>{rows.length} записей</span><details><summary>Колонки</summary><div className="column-options">{columns.slice(1).map(col=><label key={col.key}><input type="checkbox" checked={!hidden.includes(col.key)} onChange={e=>setHidden(e.target.checked?hidden.filter(k=>k!==col.key):[...hidden,col.key])}/>{col.label}</label>)}</div></details></div>
    <div className="table-scroll" tabIndex={0} aria-label="Таблица данных"><table><thead><tr>{visible.map(col=><th key={col.key} aria-sort={sort?.key===col.key?(sort.desc?'descending':'ascending'):'none'}><button type="button" onClick={()=>{setPage(1);setSort({key:col.key,desc:sort?.key===col.key?!sort.desc:false});}}>{col.label}{sort?.key===col.key?(sort.desc?' ↓':' ↑'):''}</button></th>)}{onDetail&&<th>Детали</th>}</tr></thead><tbody>{ordered.slice((current-1)*25,current*25).map(row=><tr key={rowKeys.get(row)}>{visible.map(col=><td key={col.key}><Value value={row[col.key]}/></td>)}{onDetail&&<td><button type="button" className="link-button" onClick={()=>onDetail(row)}>Подробнее</button></td>}</tr>)}</tbody></table></div>
    <div className="table-pagination"><button type="button" className="button secondary" disabled={current===1} onClick={()=>setPage(current-1)}>← Назад</button><span>{current} / {totalPages}</span><button type="button" className="button secondary" disabled={current===totalPages} onClick={()=>setPage(current+1)}>Вперёд →</button></div>
  </>;
}
