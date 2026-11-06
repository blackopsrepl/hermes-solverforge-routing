import { Button, SandboxedFrame, host, useValue, useQuery, useMutation, useQueryClient, ROUTES_AREA, SIDEBAR_NAV_AREA, TRANSCRIPT_DIRECTIVE_AREA } from '@hermes/plugin-sdk'
import { useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'solverforge-routing'
let rest
const muted = { color:'var(--ui-text-secondary)', fontSize:13, lineHeight:1.5 }
const panel = { border:'1px solid var(--ui-stroke-secondary)', borderRadius:12, padding:18, background:'var(--ui-bg-elevated, transparent)' }
const row = { display:'flex', alignItems:'center', flexWrap:'wrap', gap:8 }
const field = { padding:'9px 12px', borderRadius:7, border:'1px solid var(--ui-stroke-secondary)', background:'var(--ui-bg-primary, transparent)', color:'var(--ui-text-primary)', minWidth:180, flex:1 }
const paragraph = text => jsx('p', { style:{...muted,margin:'6px 0'}, children: text })
const label = place => place ? `${place.title || 'Supplied location'} (${place.coordinates.join(', ')})` : 'Not supplied'
const escape = text => String(text).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))

export function mapDocument(state) {
  const solution = state.solution
  const geometry = solution?.geometry?.coordinates || []
  const places = new Map([['origin', state.origin], ...state.stops.map(s => [s.id, s])])
  if (state.endpoint) places.set(state.endpoint.id, state.endpoint)
  const home = state.return_origin || state.return_location || state.home || state.start_origin
  if (home) places.set('return', home)
  const ids = solution?.itinerary_ids || ['origin', ...state.stops.filter(s => !s.visited).map(s => s.id)]
  const markers = ids.map(id => places.get(id)).filter(Boolean)
  const project = ([lon, lat]) => [lon * Math.PI / 180, Math.log(Math.tan(Math.PI / 4 + Math.max(-85, Math.min(85, lat)) * Math.PI / 360))]
  const all = [...geometry, ...markers.map(p => p.coordinates)].map(project)
  let content = '<text x="30" y="50">Supply an origin and stops to begin.</text>'
  if (all.length) {
    const [xmin,xmax,ymin,ymax] = all.reduce(([x0,x1,y0,y1],[x,y]) => [Math.min(x0,x),Math.max(x1,x),Math.min(y0,y),Math.max(y1,y)], [Infinity,-Infinity,Infinity,-Infinity])
    const scale = Math.min(720 / Math.max(xmax-xmin, 0.00001), 320 / Math.max(ymax-ymin, 0.00001))
    const point = coordinates => { const [x,y] = project(coordinates); return [400 + (x-(xmin+xmax)/2)*scale, 190 - (y-(ymin+ymax)/2)*scale] }
    content = geometry.length ? `<polyline fill="none" stroke="#206cbe" stroke-width="4" points="${geometry.map(p => point(p).join(',')).join(' ')}"/>` : ''
    const groups = new Map()
    markers.forEach((place, index) => {
      const key = place.coordinates.join(',')
      if (!groups.has(key)) groups.set(key,{place,numbers:[]})
      groups.get(key).numbers.push(index)
    })
    content += [...groups.values()].map(({place:p,numbers}) => {const [x,y] = point(p.coordinates); const text = numbers.join('/'); return `<g><title>${escape(label(p))}</title><circle cx="${x}" cy="${y}" r="${Math.max(13,text.length*4)}" fill="#173d65"/><text x="${x}" y="${y+4}" text-anchor="middle" fill="white">${text}</text></g>`}).join('')
  }
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><style>body{margin:0;height:100vh;display:flex;flex-direction:column;background:#eef3f6;color:#18344b;font:12px system-ui}svg{display:block;width:100%;flex:1;min-height:0}p{margin:5px 14px}</style></head><body><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 380" role="img" aria-label="Schematic geographic road geometry">${content}</svg><p>Schematic geographic road geometry · not a full slippy map</p><p>Road data © OpenStreetMap contributors · routing OSRM. No external tiles.</p></body></html>`
}

function RoutingSurface({ editable = false, scope }) {
  const client = useQueryClient()
  const key = [ID, ...scope]
  const query = useQuery({ queryKey: key, queryFn: () => rest('/current'), refetchInterval: 15000 })
  const [message, setMessage] = useState('')
  const [title, setTitle] = useState('')
  const [coordinates, setCoordinates] = useState('')
  const [pending, setPending] = useState(null)
  const [before, setBefore] = useState('')
  const [after, setAfter] = useState('')
  const mutation = useMutation({ mutationFn: ({action, args}) => {
    if (host.state.connectionId.get() !== scope[0] || host.state.profile.get() !== scope[1]) throw new Error('Profile changed. Refresh before editing.')
    return rest(`/actions/${action}`, { method: 'POST', body: args })
  }, onSuccess: result => { setMessage(result.ok ? 'Route updated.' : result.error?.message || 'Edit refused.'); setPending(null); client.invalidateQueries({queryKey:key}) }, onError: error => { setMessage(/expected revision|route changed/i.test(error.message || '') ? 'Route changed elsewhere. Refreshed to the latest version; review before retrying.' : error.message || 'Request failed; refresh before retrying.'); setPending(null); client.invalidateQueries({queryKey:key}) } })
  if (query.isPending) return paragraph('Loading route…')
  if (query.error || !query.data?.ok) return paragraph(query.data?.error?.message || 'Could not load route. Try refreshing.')
  const data = query.data, state = data.state, solution = state.solution
  const fresh = solution && data.solved_revision === state.revision
  const byId = new Map(state.stops.map(s => [s.id,s]))
  const order = fresh ? solution.remaining_order : state.stops.filter(s => !s.visited).map(s => s.id)
  const action = (name,args={}) => setPending({action:name,args:{...args,expected_revision:state.revision}})
  const button = (text, click, disabled=false) => jsx(Button,{onClick:click,disabled:disabled || mutation.isPending,children:text},text)
  const home = state.return_origin || state.return_location || state.home || state.start_origin
  return jsxs('section',{style:{padding:24,maxWidth:1120,margin:'0 auto',display:'flex',flexDirection:'column',gap:14,color:'var(--ui-text-primary)',fontFamily:'inherit'},children:[
    jsxs('header',{style:{...row,justifyContent:'space-between'},children:[jsxs('div',{children:[jsx('h2',{style:{margin:'0 0 4px',fontSize:25,letterSpacing:'-.5px'},children:'Your driving route'}),paragraph(`Active profile: ${scope[1]} · current shared route, not a saved historical itinerary`)]}),button('Refresh',()=>query.refetch())]}),
    jsxs('div',{style:{...panel,display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(230px,1fr))',gap:18},children:[
      jsxs('div',{children:[jsx('strong',{children:'START · supplied origin'}),paragraph(label(state.origin)),home ? paragraph(`Stable return home: ${label(home)}`) : null]}),
      jsxs('div',{children:[jsx('strong',{children:fresh?'Current for this revision':'Stale / pending solve'}),paragraph(fresh?`Estimated driving: ${Math.ceil(solution.road_duration_seconds/60)} min · ${(solution.distance_m/1000).toFixed(1)} km`:'No current road route · solve after editing'),paragraph(`${order.length} remaining · ${state.visited.length} visited · revision ${state.revision}`)]}),
      jsxs('div',{children:[jsx('strong',{children:state.round_trip?'RETURN HOME':state.endpoint?'FIXED END':'OPEN END'}),paragraph(state.round_trip?label(home):state.endpoint?label(state.endpoint):'Finish at the last selected stop'),paragraph(`Last read: ${query.dataUpdatedAt ? new Date(query.dataUpdatedAt).toLocaleTimeString() : 'now'}`)]})
    ]}),
    !data.engine.available ? paragraph('Routing engine missing. Configure the executable path; no automatic download.') : null,
    paragraph(`Solve sends exact coordinates to ${data.provider.url}. Estimated driving only. Best-found, not proven optimal. No live traffic.`),
    jsx(SandboxedFrame,{src:'data:text/html;charset=utf-8,'+encodeURIComponent(mapDocument({...state,solution:fresh?solution:null})),title:'Read-only schematic geographic road geometry',style:{width:'100%',height:'clamp(260px, 38vw, 390px)',border:'1px solid var(--ui-stroke-secondary)',borderRadius:12}}),
    jsxs('div',{style:panel,children:[
      jsx('h3',{style:{margin:'0 0 12px'},children:fresh?'Itinerary':'Stops awaiting solve · input order, not a proposed route'}),
      order.length ? jsx('ol',{style:{listStyle:'none',padding:0,margin:0},children:order.map((id,index) => { const stop=byId.get(id); if(!stop)return null; return jsxs('li',{style:{...row,padding:'12px 0',borderTop:'1px solid var(--ui-stroke-tertiary)'},children:[jsx('span',{style:{display:'grid',placeItems:'center',width:30,height:30,borderRadius:'50%',background:'var(--ui-accent)',color:'var(--ui-bg-primary, white)',fontWeight:700},children:index+1}),jsxs('div',{style:{flex:1,minWidth:160},children:[jsx('strong',{children:stop.title || 'Supplied location'}),paragraph(`${stop.coordinates.join(', ')}${state.next_id===id?' · fixed next':''}`)]}),editable?jsxs('span',{style:row,children:[button('Set next',()=>action('set_next',{stop_id:id})),button('Mark visited',()=>action('mark_visited',{stop_id:id})),button('Remove',()=>action('remove_stop',{stop_id:id}))]}):null]},id) })}):paragraph('No stops yet. Add a supplied location below.'),
      fresh && state.round_trip ? paragraph(`${order.length + 1}. Return home: ${label(home || state.origin)}`) : fresh && state.endpoint ? paragraph(`${order.length + 1}. End: ${label(state.endpoint)}`) : null,
      state.visited.length ? jsxs('details',{style:{marginTop:12},children:[jsx('summary',{children:`Visited history · ${state.visited.length}`}),jsx('ul',{children:state.visited.map(id=>jsx('li',{children:label(byId.get(id))},id))})]}):null
    ]}),
    editable ? jsxs('div',{style:{...panel,display:'flex',flexDirection:'column',gap:12},children:[
      jsx('h3',{style:{margin:0},children:'Add a supplied location'}),
      jsxs('div',{style:row,children:[jsx('input',{style:field,value:title,onChange:e=>setTitle(e.target.value),placeholder:'Location title','aria-label':'Location title'}),
      jsx('input',{style:field,value:coordinates,onChange:e=>setCoordinates(e.target.value),placeholder:'longitude, latitude','aria-label':'Longitude, latitude'})]}),
      jsxs('div',{style:row,children:[...[['Set origin','set_origin','origin'],['Add stop','add_stop','stop'],['Set endpoint','set_end','endpoint']].map(([text,name,field])=>button(text,()=>{const parts=coordinates.split(',').map(s=>s.trim());if(parts.length!==2 || parts.some(s=>!s || !Number.isFinite(Number(s)))){setMessage('Supply two numeric coordinates: longitude, latitude.');return}action(name,{[field]:{title,coordinates:parts.map(Number)}})})),
      ]}),
      jsxs('details',{children:[jsx('summary',{style:{cursor:'pointer',fontWeight:600},children:`Constraints · ${state.precedence.length} order locks${state.next_id?' · next stop fixed':''}`}),
      jsxs('div',{style:{...row,marginTop:12},children:[button('Clear endpoint',()=>action('set_end',{endpoint:null})),button(state.round_trip?'Disable return home':'Return home',()=>action('set_round_trip',{enabled:!state.round_trip})),
      button('Clear next lock',()=>action('set_next',{stop_id:null})),
      jsx('h3',{children:'Visit order locks'}),
      ...[['Before stop',before,setBefore],['After stop',after,setAfter]].map(([text,value,set])=>jsx('select',{style:field,'aria-label':text,value,onChange:e=>set(e.target.value),children:[jsx('option',{value:'',children:text},'placeholder'),...state.stops.filter(s=>!s.visited).map(s=>jsx('option',{value:s.id,children:s.title || s.id},s.id))]},text)),
      button('Add order lock',()=>action('precedence',{pairs:[...state.precedence,[before,after]]}),!before || !after || before===after),
      jsx('ul',{children:state.precedence.map(([a,b],i)=>jsxs('li',{children:[`${label(byId.get(a))} before ${label(byId.get(b))}`,button('Remove order lock',()=>action('precedence',{pairs:state.precedence.filter((_,n)=>n!==i)}))]},`${a}:${b}:${i}`))}),
      ]})]}),
      button('Solve road route',()=>action('solve',{solve_seconds:2}),!data.engine.available),
      pending ? jsxs('div',{style:{...panel,borderColor:'var(--ui-accent)'},role:'alert',children:[paragraph(`Confirm ${pending.action.replaceAll('_',' ')} for revision ${pending.args.expected_revision}? ${pending.action==='solve'?'Exact coordinates will be sent to OSRM.':'This changes the stored route.'}`),button('Confirm',()=>mutation.mutate(pending)),button('Cancel',()=>setPending(null))]}):null,
      jsx('div',{role:'status',children:message}), mutation.isPending?paragraph('Working… edits disabled until complete.'):null
    ]}):paragraph('Read-only current route · manage deliberate edits on the Route page.'),
  ]})
}
function Surface(props) {
  const connection = useValue(host.state.connectionId), profile = useValue(host.state.profile)
  return jsx(RoutingSurface,{...props,scope:[connection,profile]},JSON.stringify([connection,profile]))
}
export default { id:ID, name:'SolverForge Routing', register(ctx) {
  rest = ctx.rest
  return ctx.registerMany([
    {id:'page',area:ROUTES_AREA,data:{path:'/route'},render:()=>jsx(Surface,{editable:true})},
    {id:'nav',area:SIDEBAR_NAV_AREA,data:{path:'/route',label:'Route',codicon:'map'}},
    {id:'route-card',area:TRANSCRIPT_DIRECTIVE_AREA,data:{name:'route',render:()=>jsx(Surface,{editable:false})}}
  ])
}}
