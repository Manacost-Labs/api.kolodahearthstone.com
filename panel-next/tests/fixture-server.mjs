// Local integration fixture only. Never part of the production service.
import http from 'node:http';
export const fixtureSession='fixture-session';
const user={id:42,login:'UI fixture'};
const categories={minion:'Существа',spell:'Заклинания',hero:'Герои',hero_skin:'Скины героев',pet:'Питомцы',coin:'Монетки',constructed:'Стандарт / Вольный',trinket:'Аксессуары'};
const tribes={murloc:'Мурлок',beast:'Зверь',dragon:'Дракон'};
const rows=Array.from({length:24},(_,i)=>({id:i+1,card_id:'BG_FIXTURE_'+(i+1),dbf:69000+i,name:['Мурлок-разведчик','Мурлок-полководец','Болотный разведчик','Рыбный следопыт','Юный мурлок','Приливный страж','Морской охотник','Мурлок-ветеран'][i%8],name_en:'Fixture card '+(i+1),card_type:'minion',creature_type:i<16?'murloc':'beast',tavern_tier:1+i%6,attack:i%8,health:2+i%8,in_pool:i%3?1:0,art_image:'https://api.kolodahearthstone.com/uploads/art/BG26_146.jpg',card_image:'https://api.kolodahearthstone.com/uploads/cards/BG26_146.png',updated_at:'2026-10-02T12:00:00Z',notes:'Тестовая запись для проверки интерфейса.',golden_variant:{card_id:'BG_FIXTURE_'+(i+1)+'_G',dbf:79000+i}}));
const scopes={'database:read':{label:'Чтение полной базы',description:'Collections и records'},admin:{label:'Управление API',description:'Служебные endpoints'},'tokens:manage':{label:'Управление токенами',description:'Выпуск и отзыв'}};
let tokens=[{id:'FixtureKey01',name:'Тестовый сайт',scopes:['database:read'],expires_at:'2027-01-01T00:00:00Z',requests_used:123},{id:'FixtureMgr01',name:'Менеджер токенов',scopes:['tokens:manage'],expires_at:'2027-01-01T00:00:00Z'}];
let nonce=1;
let catalogErrorTriggered=false;
const requests=[];
const snapshot={revision:4,sections:[{id:'meta',label:'Мета',enabled:true,sources:[{id:'hsguru',label:'HSGuru',health:'ok',rowsTotal:120,lastSuccessAt:'2026-10-02T12:00:00Z',nextRunAt:'2026-10-03T12:00:00Z'},{id:'replay',label:'HSReplay',health:'warning',rowsTotal:20,lastSuccessAt:'2026-10-01T12:00:00Z',servingCachedDataset:true}]},{id:'cards',label:'Карты',enabled:true,sources:[{id:'cards',label:'Карты Blizzard',health:'missing',rowsTotal:0}]}],activeRun:null,recentRuns:[]};
export const server=http.createServer(async(req,res)=>{
 const url=new URL(req.url,'http://127.0.0.1:18768');
 const send=(data,status=200)=>{res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'private, no-store'});res.end(JSON.stringify(data));};
 if(url.pathname==='/test/requests')return send(requests);
 if(!String(req.headers.cookie||'').split(';').map(v=>v.trim()).includes('koloda_admin='+fixtureSession))return send({ok:false,message:'Требуется вход через GitHub.'},401);
 if(url.pathname==='/parser-control.php'){
  if(req.method==='GET')return send({ok:true,data:snapshot});
  let body='';for await(const chunk of req)body+=chunk;
  const input=JSON.parse(body);if(req.headers['x-csrf-token']!=='fixture-parser-csrf')return send({ok:false,message:'CSRF'},422);
  requests.push({view:'parsers',action:input.action});
  if(input.action==='section'){if(input.revision!==snapshot.revision)return send({ok:false,message:'Версия настроек устарела.'},422);snapshot.sections.find(s=>s.id===input.section_id).enabled=input.enabled;snapshot.revision++;}
  return send({ok:true,data:{id:'fixture-run',status:'queued'}},input.action==='run'?202:200);
 }
 if(url.pathname==='/analytics.php')return send({ok:true,module:url.searchParams.get('module')||'overview',title:'Все источники данных',description:'Тестовый реестр для проверки интерфейса.',summary:[{label:'Источники',value:3},{label:'Работают',value:2},{label:'Требуют внимания',value:1}],columns:[{key:'source_id',label:'Источник'},{key:'state',label:'Состояние'},{key:'rows',label:'Строк'},{key:'updated_at',label:'Обновлено'}],rows:[{source_id:'hsguru',state:'ok',rows:120,updated_at:'2026-10-02T12:00:00Z'},{source_id:'hsreplay',state:'warning',rows:20,updated_at:'2026-10-01T12:00:00Z'}],meta:{total:2,updated_at:'2026-10-02T12:00:00Z',cached:false,stale:false},parsing_reliability:{windows:[],message:'Накапливаем статистику'}});
 if(url.pathname!=='/next-data.php')return send({ok:false,message:'Unknown endpoint'},404);
 let input;
 if(req.method==='POST'){
  const request=new Request('http://127.0.0.1'+req.url,{method:'POST',headers:req.headers,body:req,duplex:'half'});
  input=await request.formData();
  if(input.get('csrf')!=='fixture-csrf')return send({ok:false,error:'Сессия формы устарела.'},422);
 }else input=url.searchParams;
 const action=input.get('action')||'list';
 if(action==='session')return send({ok:true,user,logoutCsrf:'fixture-logout-csrf',parserCsrf:'fixture-parser-csrf'});
 if(action==='list'&&input.get('q')==='__fixture_error__'&&!catalogErrorTriggered){catalogErrorTriggered=true;return send({ok:false,message:'Fixture failure'},503);}
 if(req.method==='GET'&&action==='list'&&process.env.PANEL_FIXTURE_DELAY_MS)await new Promise(resolve=>setTimeout(resolve,Number(process.env.PANEL_FIXTURE_DELAY_MS)));
 if(action==='analytics_registry')return send({ok:true,modules:{overview:{title:'Все источники',description:'Источники',params:{}},card:{title:'Статистика карты',description:'Карты',params:{q:{type:'string',max:120}}},meta:{title:'Мета Standard / Wild',description:'Архетипы',params:{format:{type:'enum',values:['standard','wild'],default:'standard'}}}}});
 const type=input.get('card_type')||'';const perPage=Math.max(1,Number(input.get('per_page')||8));
 const filtered=rows.filter(row=>(!input.get('q')||(row.name+' '+row.card_id).toLowerCase().includes(input.get('q').toLowerCase()))&&(!input.get('creature_type')||row.creature_type===input.get('creature_type'))&&(!input.get('tier')||row.tavern_tier===Number(input.get('tier'))));
 const totalPages=Math.max(1,Math.ceil(filtered.length/perPage));const page=Math.min(totalPages,Math.max(1,Number(input.get('page')||1)));
 const data={ok:true,version:1,action,title:type?categories[type]:'Карты Полей сражений',user,csrf:'fixture-csrf',logoutCsrf:'fixture-logout-csrf',parserCsrf:'fixture-parser-csrf',message:'',error:'',cardType:type,records:filtered.slice((page-1)*perPage,page*perPage),form:rows.find(r=>String(r.id)===input.get('id'))||{},page,perPage,totalPages,total:filtered.length,from:filtered.length?(page-1)*perPage+1:0,to:Math.min(page*perPage,filtered.length),categories,tribes,mediaLabels:{golden:'Есть Golden'},rarities:{rare:'Редкие'},activeFilters:[],tokenConfigured:true,managerId:'FixtureMgr01',scopeCatalog:scopes,tokens,issueNonce:String(nonce),terms:{mechanic:[{term_en:'Battlecry',term_ru:'Боевой клич'}]},termLabels:{mechanic:'Механики'}};
 if(action==='new'){data.form={card_type:'minion',in_pool:1};data.title='Новая карта';}
 if(action==='edit')data.title='Редактировать карту';
 if(req.method==='POST'){
  requests.push({view:'panel',action});
  if(action==='issue_api_token'){
   if(input.get('form_nonce')!==String(nonce))return send({ok:false,error:'Форма выпуска устарела.'},422);
   nonce++;data.issueNonce=String(nonce);data.action='api_tokens';
   if(input.get('name')==='Fixture rejected')return send({...data,ok:false,error:'Тестовая ошибка после использования формы.'},422);
   const token={id:'FixtureNew'+String(nonce).padStart(2,'0'),name:input.get('name'),scopes:input.getAll('scopes[]'),expires_at:'2027-01-01T00:00:00Z'};
   tokens=[...tokens,token];data.tokens=tokens;data.issuedToken={...token,token:'khs_v1_FixtureOnly_'+String(nonce).repeat(43)};data.message='Токен выпущен.';
  }else if(action==='revoke_api_token'){tokens=tokens.map(t=>t.id===input.get('token_id')?{...t,revoked_at:'2026-10-03T00:00:00Z'}:t);data.tokens=tokens;data.action='api_tokens';data.message='Токен отозван.';}
  else data.message='Изменения сохранены.';
 }
 send(data);
});
if(process.argv[1]===new URL(import.meta.url).pathname)server.listen(18768,'127.0.0.1',()=>console.log('Local panel fixture: 127.0.0.1:18768'));
