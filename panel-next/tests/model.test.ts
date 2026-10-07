import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizedCard, imagesFrom, mediaUrl, queryHref, deckTile } from '../lib/model.ts';
test('preserves zero stats and base/golden identities',()=>{
 const row={id:42,card_id:'BG_TEST',name:'Карта',attack:0,health:2,in_pool:1,art_image:'/uploads/art/test.jpg',golden_variant:{card_id:'BG_TEST_G',card_image:'/uploads/test_g.png'}};
 const card=normalizedCard(row,'',{}); assert.equal(card.attack,'0');assert.equal(card.id,'BG_TEST');assert.equal(card.editable,true);
 assert.equal(imagesFrom(row).length,2);assert.deepEqual(card.row.golden_variant,row.golden_variant);
});
test('deck tiles keep zero and double-digit mana, exclude framed fallbacks and map rarity',()=>{
  assert.deepEqual(deckTile({mana_cost:0,rarity:'LEGENDARY',horizontal_image_url:'/uploads/horizontal-art/TEST.webp'}),{cost:'0',price:'★',battlegrounds:false,color:'#715022',background:'#715022',legendary:true,image:'/uploads/horizontal-art/TEST.webp'});
  assert.equal(deckTile({mana_cost:'10',rarity:4}).cost,'10');
  assert.equal(deckTile({rarity:4}).color,'#503961');
  assert.equal(deckTile({rarity:3}).color,'#23445b');
  assert.equal(deckTile({art_image:'/uploads/art.jpg',card_image:'/uploads/framed.png'}).image,'');
  for(const mana_cost of [null,undefined,'', ' ', false, -1, 1.5, 'wrong']) assert.equal(deckTile({mana_cost}).cost,'—');
});
test('Battlegrounds tiles show tavern tier and purchase gold, including free spells',()=>{
  const minion=deckTile({card_type:'minion',tavern_tier:6,mana_cost:8},'');
  assert.equal(minion.cost,'6'); assert.equal(minion.price,'3'); assert.equal(minion.battlegrounds,true);
  const spell=deckTile({card_type:'spell',tavern_tier:2,cost:0},'spell');
  assert.equal(spell.cost,'2'); assert.equal(spell.price,'0');
  assert.equal(deckTile({card_type:'spell',tavern_tier:4,cost:5},'').price,'5');
  assert.equal(deckTile({card_type:'spell',tavern_tier:1},'spell').price,'—');
  assert.equal(deckTile({card_type:'MINION',tavern_tier:7,cost:9},'timewarped').price,'3');
  assert.equal(deckTile({card_type:'SPELL',tavern_tier:0,cost:2},'timewarped').cost,'—');
  assert.equal(deckTile({card_type:'MINION',mana_cost:4},'constructed').cost,'4');
});
test('media URLs reject executable schemes and navigation keeps unrelated filters',()=>{
 assert.equal(mediaUrl('javascript:alert(1)'),'');assert.equal(mediaUrl('//evil.test'),'');assert.equal(mediaUrl('/uploads/image.png'),'/uploads/image.png');
 assert.equal(queryHref('creature_type=murloc&page=4',{page:null,q:'BG_TEST'}),'/?creature_type=murloc&q=BG_TEST');
});
test('Battlegrounds backgrounds follow tribes with safe neutral and multi-tribe fallbacks',()=>{
  const demon=deckTile({creature_type:'demon',rarity:'LEGENDARY'},'minion');
  const murloc=deckTile({creature_type:'murloc',rarity:'LEGENDARY'},'minion');
  assert.notEqual(demon.color,murloc.color);
  assert.equal(deckTile({creature_type:'demon',rarity:'COMMON'},'minion').background,demon.background);
  for(const creature_type of [undefined,null,'', '__proto__','unknown']) assert.equal(deckTile({creature_type},'minion').background,'#606367');
  assert.equal(deckTile({creature_type:'demon'},'spell').background,'#606367');
  assert.equal(deckTile({creature_type:'demon',rarity:'LEGENDARY'},'constructed').background,'#715022');
  const dual=deckTile({creature_type:'murloc',races:'["DEMON","MURLOC"]'},'minion');
  assert.ok(dual.background.startsWith('linear-gradient('));
  assert.ok(dual.background.includes(demon.color) && dual.background.includes(murloc.color));
  assert.equal(dual.background,deckTile({races:['murloc','demon']},'minion').background);
  assert.equal(dual.color,murloc.color);
  assert.ok(deckTile({creature_type:'ALL'},'minion').background.startsWith('linear-gradient('));
  assert.equal(deckTile({race:'MECHANICAL'},'timewarped').background,deckTile({creature_type:'mech'},'minion').background);
});
