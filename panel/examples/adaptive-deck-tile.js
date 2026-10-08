// A portable reference renderer for API tile_layout. Native apps use the same
// dimensions multiplied by their chosen height / 64; width stays independent.
const fonts = new Map();
async function fontFamily(url) {
  if (!fonts.has(url)) {
    const name = `DeckTile${fonts.size}`;
    const face = new FontFace(name, `url(${JSON.stringify(url)})`);
    fonts.set(url, face.load().then(loaded => { document.fonts.add(loaded); return name; }));
  }
  return fonts.get(url);
}

export async function renderDeckTile(element, layout, height = 64) {
  const scale = height / layout.reference_size.height;
  const px = value => `${value * scale}px`;
  const gradient = layout.background.gradient;
  Object.assign(element.style, {
    position: 'relative', height: px(64), minWidth: px(layout.min_width),
    overflow: 'hidden', borderRadius: px(layout.corner_radius),
    background: gradient ? `linear-gradient(${gradient.angle_deg}deg, ${gradient.stops.map(stop => `${stop.color} ${stop.position * 100}%`).join(', ')})` : layout.background.color,
  });
  element.setAttribute('aria-label', layout.title.text);
  element.replaceChildren();
  const part = (style, text = '') => {
    const node = document.createElement('span');
    node.textContent = text;
    Object.assign(node.style, {position:'absolute',top:0,height:'100%',display:'flex',alignItems:'center',boxSizing:'border-box'}, style);
    element.append(node);
    return node;
  };
  const image = (parent, icon) => {
    const node = document.createElement('img');
    node.src = icon.url;
    node.alt = '';
    Object.assign(node.style, {width:px(icon.width),height:px(icon.height),objectFit:'contain',position:'absolute',left:'50%',top:'50%',transform:'translate(-50%,-50%)'});
    parent.append(node);
  };
  if (layout.art.url) {
    const node = document.createElement('img');
    node.src = layout.art.url;
    node.alt = '';
    Object.assign(node.style, {position:'absolute',right:px(layout.art.end_inset),top:0,width:px(64*layout.art.aspect_ratio),height:'100%',objectFit:layout.art.fit});
    element.append(node);
  }
  const shadow = layout.text_style.shadow;
  const textStyle = (item, family) => ({
    fontFamily:`${family}, serif`,fontSize:px(item.font_size),fontWeight:item.font_weight || 400,color:item.color,
    WebkitTextStroke:`${px(layout.text_style.outline_width)} ${item.outline_color || layout.text_style.outline_color}`,
    paintOrder:'stroke fill',textShadow:`0 ${px(shadow.offset_y)} ${px(shadow.blur)} ${shadow.color}`,
  });
  const [costFont,titleFont,badgeFont] = await Promise.all([layout.cost,layout.title,layout.badge].map(item => fontFamily(item.font_url)));
  const cost = part({left:0,width:px(layout.cost.column_width),justifyContent:'center'});
  if (layout.cost.icon) image(cost,layout.cost.icon);
  const costText = document.createElement('span');
  costText.textContent = layout.cost.text;
  Object.assign(costText.style,{position:'relative',...textStyle(layout.cost,costFont)});
  cost.append(costText);
  const title = part({left:px(layout.title.inset_start),right:px(layout.title.inset_end),...textStyle(layout.title,titleFont)});
  const titleText = document.createElement('span');
  titleText.textContent = layout.title.text;
  Object.assign(titleText.style,{overflow:'hidden',whiteSpace:'nowrap',textOverflow:'ellipsis'});
  title.append(titleText);
  const badge = part({right:0,width:px(layout.badge.column_width),justifyContent:'center',background:layout.background.color,...textStyle(layout.badge,badgeFont)});
  if (layout.badge.icon) image(badge,layout.badge.icon);
  else badge.textContent = layout.badge.text;
}
