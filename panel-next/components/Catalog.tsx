'use client';
import { useState } from 'react';
import { PanelLink } from './WorkspaceNavigation';
import { useCatalogQuery } from './useCatalogQuery';
import { catalogView } from '@/lib/catalog-state';
import { SquaresFourIcon } from '@phosphor-icons/react/dist/ssr/SquaresFour';
import { RowsIcon } from '@phosphor-icons/react/dist/ssr/Rows';
import { MagnifyingGlassIcon } from '@phosphor-icons/react/dist/ssr/MagnifyingGlass';
import { normalizedCard, queryHref, entityApiBase } from '@/lib/model';
import type { PageData, Row } from '@/lib/types';
import { RecordDetails } from './Details';
import { Artwork } from './Artwork';
import { DeckTiles } from './DeckTiles';

export function Catalog({data,query}:{data:PageData;query:string}) {
  const {pending,params,search,changeSearch,go,mode,view}=useCatalogQuery(query);
  const [selection,setSelection]=useState<{query:string;row:Row}|null>(null);
  if(selection&&selection.query!==query)setSelection(null);
  const selected=selection?.query===query?selection.row:null;
  const setSelected=(row:Row|null)=>setSelection(row?{row,query}:null);
  const type=data.cardType;
  const filterType=params.get('card_type')||'';
  const resultView=catalogView(new URLSearchParams(query).get('view'));
  const select=(name:string,caption:string,options:Record<string,string>)=><select name={name} aria-label={caption} value={params.get(name)||''} onChange={e=>go({[name]:e.target.value})}><option value="">{caption}</option>{Object.entries(options).map(([key,name])=><option key={key} value={key}>{name}</option>)}</select>;
  const cards=data.records.map(row=>normalizedCard(row,type,data.tribes));
  return <section className="catalog"><header className="page-head"><div><span className="eyebrow">База Hearthstone</span><h1>{data.title}</h1><p>{data.from}–{data.to} из {data.total.toLocaleString('ru-RU')} записей</p></div>{['','minion','spell'].includes(type)&&<PanelLink className="button secondary" href="/?action=new" prefetch={false}>Добавить карту</PanelLink>}</header>
    <form className="catalog-filters" action="/" method="get" onSubmit={e=>{e.preventDefault();const form=new FormData(e.currentTarget);go(Object.fromEntries([...form.entries()].map(([k,v])=>[k,String(v)])),true);}}>
      <label className="search"><MagnifyingGlassIcon size={20}/><input type="search" name="q" value={search} placeholder="Название, ID, DBF, текст или механика" aria-label="Поиск карт" onChange={e=>changeSearch(e.target.value)}/><kbd>/</kbd></label>
      <div className="filter-row">{select('card_type','Карты Полей сражений',data.categories)}
        {!['hero','hero_skin','coin','constructed','anomaly','quest','reward','trinket'].includes(filterType)&&select('tier','Все уровни',Object.fromEntries(Array.from({length:filterType==='pet'||filterType==='darkmoon_prize'?4:7},(_,i)=>[String(i+1),'Уровень '+(i+1)])))}
        {['','minion','spell'].includes(filterType)&&select('creature_type','Все типы',data.tribes)}
        {!['hero','hero_skin','pet','coin','timewarped','constructed'].includes(filterType)&&select('pool','Любой пул',{'1':'В пуле','0':'Не в пуле'})}
        {['','minion','spell'].includes(filterType)&&select('duos','Любой режим',{'1':'Только дуо','0':'Не только дуо'})}
        {filterType==='constructed'&&select('constructed_format','Стандарт + Вольный',{standard:'Стандартный',wild:'Вольный'})}
        {filterType==='hero_skin'&&select('rarity','Любая редкость',data.rarities)}
        {['hero','hero_skin','pet','constructed'].includes(filterType)&&select('media','Все изображения',data.mediaLabels)}
        <select name="per_page" aria-label="Записей на странице" value={params.get('per_page')||data.perPage} disabled={view==='tiles'} onChange={e=>go({per_page:e.target.value})}>{[8,12,15,25,50,100,150].map(n=><option key={n} value={n}>{n} на странице</option>)}</select>
        <button className="button" type="submit">Найти</button><PanelLink className="button secondary" prefetch={false} replace scroll={false} href={queryHref('',{card_type:filterType,view,per_page:resultView==='tiles'?15:data.perPage})}>Сбросить</PanelLink>
      </div>
    </form>
    <div className="filter-chips">{data.activeFilters.map(filter=><PanelLink key={filter.label} prefetch={false} replace scroll={false} href={queryHref(filter.href.split("?")[1]||"",{view,per_page:resultView==='tiles'?15:data.perPage})}>{filter.label} ×</PanelLink>)}</div>
    <div className="catalog-toolbar"><div className="catalog-summary"><span>На странице <b>{cards.length}</b> записей</span><span className="catalog-update" role="status">{pending?'Обновляем результаты…':''}</span></div><div className="view-switch" role="group" aria-label="Вид каталога"><button type="button" aria-pressed={view==='tiles'} onClick={()=>mode('tiles')}><RowsIcon size={18}/> Плитки</button><button type="button" aria-pressed={view==='grid'} onClick={()=>mode('grid')}><SquaresFourIcon size={18}/> Карточки</button><button type="button" aria-pressed={view==='list'} onClick={()=>mode('list')}><RowsIcon size={18}/> Таблица</button></div></div>
    <div className="catalog-results" aria-busy={pending}>
    {!cards.length?<div className="empty"><h2>Ничего не найдено</h2><p>Измените запрос или сбросьте фильтры.</p></div>:resultView==='tiles'?<DeckTiles cards={cards.slice(0,15)} cardType={type} onSelect={setSelected}/>:resultView==='grid'?<div className="card-grid">{cards.map((card,index)=><button className="card-tile" type="button" key={card.id+'-'+index} onClick={()=>setSelected(card.row)} aria-label={card.name+'. Открыть детали'}>
      <span className="card-art"><Artwork urls={card.images}/>{card.tier&&<span className="tier">★ {card.tier}</span>}</span>
      <span className="card-copy"><strong>{card.name}</strong><code>{card.id}</code><span className="card-tags"><span>{card.tribe}</span>{card.inPool&&<span className="good">В пуле</span>}</span></span>
      <span className="card-footer"><span className="card-stats">{card.attack!==''&&<span className="attack">⚔ {card.attack}</span>}{card.health!==''&&<span className="health">♥ {card.health}</span>}</span><span className="more">Подробнее →</span></span>
    </button>)}</div>:<div className="table-scroll"><table><thead><tr>{['Карта','Card ID','Тип','Уровень','Атака','Здоровье','Пул','Просмотр'].map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>{cards.map((card,i)=><tr key={card.id+'-'+i}><td><strong>{card.name}</strong></td><td><code>{card.id}</code></td><td>{card.tribe}</td><td>{card.tier||'—'}</td><td>{card.attack||'—'}</td><td>{card.health||'—'}</td><td>{card.inPool?'В пуле':'—'}</td><td><button type="button" className="link-button" onClick={()=>setSelected(card.row)}>Подробнее</button></td></tr>)}</tbody></table></div>}
    </div>
    <nav className="pagination" aria-label="Страницы каталога"><button type="button" className="page-link" disabled={pending||data.page===1} onClick={()=>go({page:data.page-1})}>← Назад</button>{Array.from({length:Math.min(5,data.totalPages)},(_,i)=>Math.max(1,Math.min(data.page-2,data.totalPages-4))+i).map(page=><button key={page} type="button" className="page-link" disabled={pending} aria-current={page===data.page?'page':undefined} onClick={()=>go({page})}>{page}</button>)}<button type="button" className="page-link" disabled={pending||data.page===data.totalPages} onClick={()=>go({page:data.page+1})}>Вперёд →</button><span>Страница {data.page} из {data.totalPages}</span></nav>
    {selected&&<RecordDetails row={selected} apiBase={entityApiBase(type)} title={normalizedCard(selected,type,data.tribes).name} editable={normalizedCard(selected,type,data.tribes).editable} onClose={()=>setSelected(null)}/>}
  </section>;
}
