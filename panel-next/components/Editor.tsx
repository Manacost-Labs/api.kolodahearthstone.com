'use client';
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { mutate } from '@/lib/client';
import { text } from '@/lib/model';
import type { PageData } from '@/lib/types';
import { Modal } from './Modal';
export function Editor({data}:{data:PageData}) {
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [confirm,setConfirm]=useState(false);const router=useRouter();
  const submit=async(form:FormData)=>{setBusy(true);setError('');try{await mutate(form);router.push('/');router.refresh();}catch(e){setError(e instanceof Error?e.message:'Не удалось сохранить карту.');}finally{setBusy(false);}};
  const inputs=[['name','Название RU',false],['name_en','Название EN',false],['card_id','Card ID',false],['dbf','DBF',true],['tavern_tier','Уровень таверны',true],['attack','Атака',true],['health','Здоровье',true]] as const;
  return <section><header className="page-head"><div><span className="eyebrow">Каталог</span><h1>{data.title}</h1></div><Link href="/" className="button secondary" prefetch={false}>← К каталогу</Link></header>{error&&<p className="notice bad" role="alert">{error}</p>}
    <form className="editor-form" onSubmit={e=>{e.preventDefault();void submit(new FormData(e.currentTarget));}}>
      <input type="hidden" name="action" value="save"/><input type="hidden" name="csrf" value={data.csrf}/><input type="hidden" name="id" value={text(data.form.id,'')}/>
      <div className="form-grid">{inputs.map(([key,name,numeric])=><label key={key}><span>{name}</span><input name={key} defaultValue={text(data.form[key],'')} type={numeric?'number':'text'} required={key==='name'||key==='card_id'} min={numeric?0:undefined} max={key==='tavern_tier'?7:undefined}/></label>)}
        <label><span>Категория</span><select name="card_type" defaultValue={text(data.form.card_type,'minion')}><option value="minion">Существо</option><option value="spell">Заклинание</option></select></label>
        <label><span>Тип существа</span><select name="creature_type" defaultValue={text(data.form.creature_type,'')}><option value="">Не указан</option>{Object.entries(data.tribes).map(([key,name])=><option key={key} value={key}>{name}</option>)}</select></label>
        <label className="check-label"><input type="checkbox" name="in_pool" defaultChecked={Number(data.form.in_pool)===1}/> В пуле</label><label className="check-label"><input type="checkbox" name="duos_only" defaultChecked={Number(data.form.duos_only)===1}/> Только дуо</label>
      </div>
      <label><span>Описание и механики</span><textarea name="notes" rows={5} defaultValue={text(data.form.notes,'')}/></label>
      <details className="extra-data"><summary>Заменить изображения</summary><div className="form-grid">{[['card_image_file','Обычная карта'],['golden_image_file','Золотая карта'],['art_image_file','Арт'],['framed_image_file','Арт в рамке']].map(([key,name])=><label key={key}><span>{name}</span><input type="file" name={key} accept="image/png,image/jpeg,image/webp"/></label>)}</div></details>
      <div className="form-actions"><button className="button" disabled={busy} type="submit">{busy?'Сохраняем…':'Сохранить'}</button>{Boolean(data.form.id)&&<button className="button danger" type="button" disabled={busy} onClick={()=>setConfirm(true)}>Удалить карту</button>}</div>
    </form>
    {confirm&&<Modal title="Удалить карту?" onClose={()=>setConfirm(false)}><p>Карта «{text(data.form.name)}» будет удалена из базы.</p><div className="form-actions"><button type="button" className="button secondary" onClick={()=>setConfirm(false)}>Отмена</button><button type="button" className="button danger" disabled={busy} onClick={()=>{const form=new FormData();form.set('action','delete');form.set('id',text(data.form.id));form.set('csrf',data.csrf);void submit(form);}}>Удалить</button></div></Modal>}
  </section>;
}
