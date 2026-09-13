() => {
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  const colors = new Map();
  function rgba(value) {
    if (colors.has(value)) return colors.get(value);
    ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = value; ctx.fillRect(0, 0, 1, 1);
    const p = [...ctx.getImageData(0, 0, 1, 1).data]; p[3] /= 255;
    colors.set(value, p); return p;
  }
  const blend = (fg, bg) => [0,1,2].map(i => fg[i] * fg[3] + bg[i] * (1-fg[3]));
  function luminance(c) { const v=c.map(x=>{x/=255;return x<=.04045?x/12.92:((x+.055)/1.055)**2.4});return v[0]*.2126+v[1]*.7152+v[2]*.0722; }
  const ratio = (a,b) => {const x=[luminance(a),luminance(b)].sort((a,b)=>b-a);return (x[0]+.05)/(x[1]+.05)};
  const visible = e => { const r=e.getBoundingClientRect(), s=getComputedStyle(e); if (e.checkVisibility && !e.checkVisibility({ checkOpacity:true, checkVisibilityCSS:true })) return false; return r.width>2&&r.height>2&&s.display!=='none'&&s.visibility!=='hidden'&&s.opacity!=='0'; };
  const modal = [...document.querySelectorAll('dialog[open]')].at(-1);
  const inScope = e => !modal || modal.contains(e);
  function description(e) {
    const text = (e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.textContent || '').trim().replace(/\s+/g,' ');
    return text.replace(/(?:sk-|Bearer\s+)[A-Za-z0-9_\-.]+/g,'[redacted]').slice(0,72);
  }
  function selector(e) {
    if(e.id) return '#'+CSS.escape(e.id);
    const result=[]; for(let n=e;n&&n!==document.body&&result.length<4;n=n.parentElement){let s=n.tagName.toLowerCase();const same=[...n.parentElement?.children||[]].filter(c=>c.tagName===n.tagName);if(same.length>1)s+=`:nth-of-type(${same.indexOf(n)+1})`;result.unshift(s)}return result.join(' > ');
  }
  function background(e) {
    let rgb=[255,255,255], gradient=false;
    const chain=[]; for(let n=e;n;n=n.parentElement)chain.unshift(n);
    for(const n of chain){const s=getComputedStyle(n);rgb=blend(rgba(s.backgroundColor),rgb);if(s.backgroundImage!=='none')gradient=true}
    return {rgb, gradient};
  }
  const contrast=[], seen=new Set(), walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
  while(walker.nextNode()){
    const node=walker.currentNode,e=node.parentElement;
    if(!node.textContent.trim()||!e||seen.has(e)||!visible(e)||!inScope(e)||e.closest('script,style,svg,[aria-hidden="true"],button:disabled,[aria-disabled="true"],option'))continue;
    seen.add(e);const s=getComputedStyle(e),bg=background(e);if(bg.gradient)continue;
    let opacity=1;for(let n=e;n;n=n.parentElement)opacity*=Number(getComputedStyle(n).opacity);
    if(opacity<.6)continue;
    const fg=[...rgba(s.color)];fg[3]*=opacity;
    const actual=ratio(blend(fg,bg.rgb),bg.rgb), fontSize=parseFloat(s.fontSize), large=fontSize>=24||(fontSize>=18.66&&Number(s.fontWeight)>=700),minimum=large?3:4.5;
    if(actual+.02<minimum)contrast.push({text:description(e),selector:selector(e),foreground:s.color,background:bg.rgb.map(Math.round),ratio:+actual.toFixed(2),minimum,fontSize,inline:e.getAttribute('style')||'',className:typeof e.className==='string'?e.className:''});
  }
  for (const e of document.querySelectorAll('input:not([type=hidden]),select,textarea')) {
    if (!visible(e) || !inScope(e) || e.disabled) continue;
    const bg=background(e); if(bg.gradient)continue;
    const style=getComputedStyle(e), fg=rgba(style.color), actual=ratio(blend(fg,bg.rgb),bg.rgb);
    const name=e.getAttribute('aria-label') || e.labels?.[0]?.textContent?.trim() || e.getAttribute('placeholder') || e.tagName;
    if(actual+.02<4.5)contrast.push({text:name.slice(0,72),selector:selector(e),foreground:style.color,background:bg.rgb.map(Math.round),ratio:+actual.toFixed(2),minimum:4.5,kind:'control-value'});
    if (!e.value && e.getAttribute('placeholder')) {
      const placeholder=getComputedStyle(e,'::placeholder'), color=[...rgba(placeholder.color)];color[3]*=Number(placeholder.opacity);
      const value=ratio(blend(color,bg.rgb),bg.rgb);
      if(value+.02<4.5)contrast.push({text:name.slice(0,72),selector:selector(e),foreground:placeholder.color,background:bg.rgb.map(Math.round),ratio:+value.toFixed(2),minimum:4.5,kind:'placeholder'});
    }
  }
  const fields=[...document.querySelectorAll('input:not([type=hidden]),select,textarea')].filter(e=>visible(e)&&inScope(e)&&!e.closest('[aria-hidden=true]')&&!e.labels?.length&&!e.getAttribute('aria-label')&&!e.getAttribute('aria-labelledby')&&!e.title).map(e=>({selector:selector(e),placeholder:e.getAttribute('placeholder')||'',type:e.type}));
  const buttons=[...document.querySelectorAll('button,[role=button]')].filter(e=>visible(e)&&inScope(e)&&!description(e)&&!e.title).map(e=>({selector:selector(e),className:e.className}));
  const clipped=[...document.querySelectorAll('.metric-card__value,.ui-dialog, .workspace-topbar, main input, main select')].filter(e=>visible(e)&&inScope(e)&&e.scrollWidth>e.clientWidth+3).map(e=>({selector:selector(e),kind:e.tagName,clientWidth:e.clientWidth,scrollWidth:e.scrollWidth}));
  const tokens=getComputedStyle(document.documentElement), body=getComputedStyle(document.body);
  const bodyMatchesTheme = JSON.stringify(rgba(body.color)) === JSON.stringify(rgba(tokens.getPropertyValue('--text-main').trim())) && JSON.stringify(rgba(body.backgroundColor)) === JSON.stringify(rgba(tokens.getPropertyValue('--bg-app').trim()));
  const ids=[...document.querySelectorAll('input[id],select[id],textarea[id]')].map(e=>e.id);
  const duplicateFieldIds=[...new Set(ids.filter((id,i)=>ids.indexOf(id)!==i))];
  return {bodyMatchesTheme,duplicateFieldIds,url:location.pathname,theme:document.documentElement.dataset.theme,width:innerWidth,height:innerHeight,pageOverflow:document.documentElement.scrollWidth>innerWidth+1,contrast,unlabeledFields:fields,unlabeledButtons:buttons,clipped,headings:[...document.querySelectorAll('h1,h2')].filter(e=>visible(e)&&inScope(e)).map(e=>description(e)).slice(0,16)};
}
