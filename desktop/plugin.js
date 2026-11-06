import { Button, SandboxedFrame, host, useValue, useQuery, useMutation, useQueryClient, PALETTE_AREA, STATUSBAR_AREAS, TRANSCRIPT_DIRECTIVE_AREA } from '@hermes/plugin-sdk'
import { useEffect, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'solverforge-routing'
let rest
const muted = { color:'var(--ui-text-secondary)', fontSize:13, lineHeight:1.5 }
const panel = { border:'1px solid var(--ui-stroke-secondary)', borderRadius:12, padding:18, background:'var(--ui-bg-elevated, transparent)' }
const row = { display:'flex', alignItems:'center', flexWrap:'wrap', gap:8 }
const paragraph = text => jsx('p', { style:{...muted,margin:'6px 0'}, children: text })
// Geocoder titles are full postal strings; people read the place name first.
const splitName = text => { const parts = String(text || '').split(',').map(s => s.trim()).filter(Boolean).filter(p => !/^[0-9_]+$/.test(p)); const n = parts.length > 1 && /^\d+[a-z]?$/i.test(parts[0]) ? 2 : 1; return [parts.slice(0, n).reverse().join(' '), parts.slice(n, n + 2).join(', ')] }
const short = text => splitName(text)[0]
const label = place => place ? (place.title ? short(place.title) : place.coordinates.join(', ')) : 'Not set'
const NameLines = ({text, strong=true}) => { const [name, rest] = splitName(text); return jsxs('div',{children:[jsx(strong?'strong':'span',{children:name}), rest ? jsx('div',{style:{...muted,fontSize:12},children:rest}) : null]}) }
// Only these exact provider prefixes are rendered as links (built server-side
// from coordinates). No target attribute: Electron's window-open policy denies
// every new-window request, while a main-frame link navigation is cancelled and handed to
// the app's audited external opener (http/https allow-list), i.e. the system
// browser or provider app. Right-click offers the app's own link menu.
const LINK_PREFIXES = ['https://www.google.com/maps/dir/?api=1&', 'https://maps.apple.com/directions?', 'https://www.openstreetmap.org/directions?engine=fossgis_osrm_car&']
const safeLink = url => {
  if (typeof url !== 'string' || url.length > 2048 || !LINK_PREFIXES.some(prefix => url.startsWith(prefix))) return null
  try { const parsed = new URL(url); return !parsed.username && !parsed.password && !parsed.hash && LINK_PREFIXES.some(prefix => parsed.href.startsWith(prefix)) ? parsed.href : null } catch { return null }
}
const linkStyle = { display:'inline-flex', alignItems:'center', padding:'8px 12px', borderRadius:7, border:'1px solid var(--ui-stroke-secondary)', color:'var(--ui-text-primary)', textDecoration:'none', fontWeight:600 }
const ExternalLink = ({url, text}) => { const href = safeLink(url); return href ? jsx('a',{href,rel:'noopener noreferrer',referrerPolicy:'no-referrer',style:linkStyle,children:`${text} ↗`}) : null }
const minutes = seconds => `${Math.ceil(seconds/60)} min`
const legName = (leg, points) => `${leg.from === 0 ? 'Start' : `stop ${leg.from}`} → ${leg.to === points.length - 1 ? 'finish' : `stop ${leg.to}`}`
function QrCode({qr, caption}) {
  if (!qr || typeof qr.path !== 'string' || !/^[Mhvz0-9,-]*$/.test(qr.path) || !Number.isInteger(qr.size)) return null
  return jsxs('figure',{style:{margin:0,display:'flex',flexDirection:'column',alignItems:'center',gap:6,width:176},children:[
    jsxs('svg',{role:'img','aria-label':caption,viewBox:`0 0 ${qr.size} ${qr.size}`,width:176,height:176,shapeRendering:'crispEdges',style:{background:'#fff',borderRadius:6},children:[jsx('rect',{width:qr.size,height:qr.size,fill:'#ffffff'}),jsx('path',{d:qr.path,fill:'#000000'})]}),
    jsx('figcaption',{style:{...muted,fontSize:12,textAlign:'center'},children:caption})]})
}
function Handoff({nav, showQr, onToggleQr}) {
  if (!nav) return null
  const points = nav.points
  const group = (name, legs) => legs.length === 1 ? [jsx(ExternalLink,{url:legs[0].url,text:name},name)] : legs.map((leg,i)=>jsx(ExternalLink,{url:leg.url,text:`${name} ${i+1}/${legs.length}`},`${name}${i}`))
  const mobile = nav.google_mobile.legs
  return jsxs('div',{style:{display:'flex',flexDirection:'column',gap:8,borderTop:'1px solid var(--ui-stroke-tertiary)',paddingTop:12},children:[
    jsx('h3',{style:{margin:0,fontSize:14},children:'Navigate'}),
    jsxs('div',{style:row,children:[...group('Google Maps',nav.google.legs),...group('Apple Maps',nav.apple.legs)]}),
    jsxs('details',{style:muted,children:[jsx('summary',{style:{cursor:'pointer'},children:'OpenStreetMap'}),
      jsx('div',{style:{...row,marginTop:8},children:nav.osm.legs.map((leg,i)=>jsx(ExternalLink,{url:leg.url,text:legName(leg,points)},`osm${i}`))})]}),
    jsx(Button,{onClick:onToggleQr,variant:'text',size:'sm',children:showQr?'Hide QR code':'Send to phone (QR code)'}),
    showQr ? jsxs('div',{style:{...row,alignItems:'flex-start',gap:16},children:[
      ...nav.apple.legs.map((leg,i)=>jsx(QrCode,{qr:leg.qr,caption:nav.apple.legs.length>1?`Part ${i+1}/${nav.apple.legs.length}: ${legName(leg,points)}`:'On iPhone: opens the Apple Maps app'},`qr-apple${i}`)),
      ...mobile.map((leg,i)=>jsx(QrCode,{qr:leg.qr,caption:mobile.length>1?`Part ${i+1}/${mobile.length}: ${legName(leg,points)}`:'On Android: opens Google Maps'},`qr-google${i}`)),
    ]}) : null
  ]})
}
const km = metres => `${(metres/1000).toFixed(1)} km`
// Kind, locality and distance on every candidate so a station is not confused with a
// company and a street in one town with the same name in another.
const facts = c => [c.kind, c.locality, c.distance_from_start_m != null ? `${km(c.distance_from_start_m)} from start` : c.nearest_route_point_m != null ? `${km(c.nearest_route_point_m)} from nearest stop` : null].filter(Boolean).join(' · ')
const farFlag = c => c.far ? jsx('strong',{style:{color:'var(--ui-warning, #f5b041)'},children:' · far away'}) : null
function CandidateText({c}) {
  return jsxs('div',{children:[jsx('strong',{children:short(c.label)}),jsxs('div',{style:{...muted,fontSize:12},children:[facts(c),farFlag(c)]})]})
}
const initialChoice = item => item.status === 'supplied' ? 'use' : item.preselected || (item.status === 'needs_choice' ? null : 'skip')
const ROLE = { origin:'Start', stop:'Stop', endpoint:'End' }
function PendingPlan({plan, state, onApply, busy}) {
  const [choices, setChoices] = useState(() => Object.fromEntries(plan.items.map(item => [item.index, initialChoice(item)])))
  const stale = plan.base_revision !== state.revision
  const unresolved = plan.items.filter(item => !choices[item.index])
  const using = plan.items.filter(item => choices[item.index] && choices[item.index] !== 'skip')
  const apply = () => onApply({plan_id:plan.plan_id, choices:plan.items.filter(item=>!['use','skip',null].includes(choices[item.index])).map(item=>({index:item.index,candidate_id:choices[item.index]})), skip:plan.items.filter(item=>choices[item.index]==='skip').map(item=>item.index), expected_revision:plan.base_revision}, using.length)
  const ends = [plan.round_trip===true?'return home':plan.round_trip===false?'no return home':null, plan.endpoint_action==='clear'?'clear the fixed end':null].filter(Boolean)
  return jsxs('div',{style:{...panel,borderColor:'var(--ui-accent)',display:'flex',flexDirection:'column',gap:8},children:[
    jsx('h3',{style:{margin:0},children:'Places to add'}),
    ends.length ? paragraph(`Also: ${ends.join(', ')}.`) : null,
    jsx('ol',{style:{paddingLeft:20,margin:0},children:plan.items.map(item => jsxs('li',{style:{marginBottom:12},children:[
      jsx('strong',{children:`${ROLE[item.role]}: ${item.query ?? item.location?.title ?? 'Shared location'}`}), item.region ? jsx('span',{style:muted,children:` · ${item.region}`}) : null,
      item.status === 'supplied' ? paragraph(item.location.coordinates.join(', ')) :
      item.status === 'search_failed' ? paragraph(`Search failed: ${item.error}`) :
      item.status === 'no_match' ? paragraph('Not found') :
      item.note ? jsx('p',{style:{...muted,margin:'6px 0',...(item.far||item.reason==='kind_mismatch'?{color:'var(--ui-warning, #f5b041)',fontWeight:600}:{})},children:(item.far?'⚠ ':'')+item.note}) :
      paragraph(item.status === 'preselected' ? (item.candidates.length > 1 ? 'Same place, several entries' : 'One match') : `${item.candidates.length} matches · choose one`),
      ...(item.candidates || []).map(c => jsxs('label',{style:{display:'block',overflowWrap:'anywhere',fontSize:13,marginBottom:4},children:[jsx('input',{type:'radio',name:`plan-${plan.plan_id}-${item.index}`,checked:choices[item.index]===c.candidate_id,disabled:stale||busy,onChange:()=>setChoices({...choices,[item.index]:c.candidate_id})}),jsx(CandidateText,{c})]},c.candidate_id)),
      item.status !== 'supplied' && item.candidates?.length ? jsxs('label',{style:{display:'block',fontSize:13},children:[jsx('input',{type:'radio',name:`plan-${plan.plan_id}-${item.index}`,checked:choices[item.index]==='skip',disabled:stale||busy,onChange:()=>setChoices({...choices,[item.index]:'skip'})}),' Skip']}) : null
    ]},item.index))}),
    stale ? paragraph('The route changed since. Plan these places again.') : unresolved.length ? null : null,
    jsx(Button,{onClick:apply,disabled:stale||busy||unresolved.length>0||!using.length,children:`Add ${using.length} place${using.length===1?'':'s'}`})
  ]})
}
// The route is edited in chat. This view shows it: the map first, then the itinerary.
function RoutingSurface({ compact = false, scope }) {
  const client = useQueryClient()
  const key = [ID, ...scope]
  // Short poll: edits arrive from the chat, and the map should follow them promptly.
  const query = useQuery({ queryKey: key, queryFn: () => rest('/current'), refetchInterval: 4000 })
  const [message, setMessage] = useState('')
  const [showQr, setShowQr] = useState(false)
  const mutation = useMutation({ mutationFn: ({action, args}) => {
    if (host.state.connectionId.get() !== scope[0] || host.state.profile.get() !== scope[1]) throw new Error('Profile changed. Refresh.')
    return rest(`/actions/${action}`, { method: 'POST', body: args })
  }, onSuccess: result => { setMessage(result.ok ? '' : result.error?.message || 'Not applied.'); client.invalidateQueries({queryKey:key}) }, onError: error => { setMessage(/expected revision|route changed|plan was made/i.test(error.message || '') ? 'The route changed. Refreshed.' : error.message || 'Something went wrong.'); client.invalidateQueries({queryKey:key}) } })
  if (query.isPending) return paragraph('Loading route…')
  if (query.error || !query.data?.ok) return paragraph(query.data?.error?.message || 'Could not load the route.')
  const data = query.data, state = data.state, solution = state.solution
  const fresh = solution && data.solved_revision === state.revision
  const byId = new Map(state.stops.map(s => [s.id,s]))
  const order = fresh ? solution.remaining_order : state.stops.filter(s => !s.visited).map(s => s.id)
  const run = (name,args={}) => mutation.mutate({action:name,args:{...args,expected_revision:state.revision}})
  const hasStart = Boolean(state.origin)
  const empty = !hasStart && !state.stops.length
  const canPlan = !fresh && hasStart && order.length > 0 && data.engine.available
  const dot = (text, solid) => jsx('span',{style:{display:'grid',placeItems:'center',width:26,height:26,flexShrink:0,borderRadius:'50%',fontSize:12,fontWeight:700,background:solid?'var(--ui-accent)':'var(--ui-bg-quaternary)',color:solid?'var(--ui-bg-primary, white)':'var(--ui-text-primary)'},children:text})
  // Hop colours must stay in step with assets/map/guest.js: the chip in each
  // itinerary row is the colour of that hop's line on the map.
  const LEG_COLORS = ['#1d4f9c', '#047857', '#7c3aed', '#be185d', '#0e7490', '#52525b']
  const HOME_COLOR = '#b45309'
  const chip = (color) => jsx('span',{style:{flexShrink:0,alignSelf:'center',width:18,height:5,borderRadius:3,background:color}, 'aria-hidden':true})
  const runstreet = (runs) => runs && runs.length ? jsx('div',{style:{...muted,fontSize:12,marginTop:2},children:runs.slice(0, 6).map((run) => `${run.name} · ${km(Math.round(run.distance_m))}`).join(' → ') + (runs.length > 6 ? ` → … +${runs.length - 6} more` : '')}) : null
  const streetRun = ((runs) => runs && runs.length ? jsx('div',{style:{...muted,fontSize:12,marginTop:2},children:runs.slice(0, 6).map((run, i) => runLabel(run)).join(' → ') + (runs.length > 6 ? ` → … +${runs.length - 6}` : '')}) : null)
  const line = (badge, text, extra=null, k, chipColor=null, streets=null) => jsxs('li',{style:{display:'flex',gap:10,alignItems:'flex-start',padding:'8px 0'},children:[badge, chipColor ? chip(chipColor) : null, jsxs('div',{style:{minWidth:0},children:[jsx(NameLines,{text}),streets,extra]})]},k)
  const summary = fresh
    ? jsxs('div',{children:[
        jsx('div',{style:{fontSize:22,fontWeight:700,letterSpacing:'-.3px'},children:`${minutes(solution.road_duration_seconds)} · ${km(solution.distance_m)}`}),
        paragraph(`${order.length} stop${order.length===1?'':'s'} · ${state.round_trip?'round trip':state.endpoint?'ends there':'one way'}`),
        solution.comparison?.message ? jsx('div',{style:{fontWeight:600,color:'var(--ui-accent)'},children:solution.comparison.message}) : null]})
    : empty ? null
    : jsxs('div',{style:{display:'flex',flexDirection:'column',gap:8,alignItems:'flex-start'},children:[
        paragraph(!hasStart ? 'Tell Hermes where you start' : order.length ? 'Not planned yet' : 'Tell Hermes where to stop'),
        canPlan ? jsx(Button,{onClick:()=>run('solve',{solve_seconds:2}),disabled:mutation.isPending,size:'sm',children:mutation.isPending?'Planning…':'Plan route'}) : null,
        hasStart && order.length && !data.engine.available ? paragraph('Routing engine not installed.') : null]})
  const itinerary = empty
    ? jsxs('div',{style:{display:'flex',flexDirection:'column',gap:6},children:[
        jsx('strong',{children:'Tell Hermes where you’re going'}),
        jsx('div',{style:{...muted,fontStyle:'italic'},children:'“Start at Milano Centrale, then the Duomo and Castello Sforzesco, and back.”'}),
        jsx('div',{style:muted,children:'On your phone, share your location in Telegram to start from where you are.'})]})
    : jsx('ol',{style:{listStyle:'none',padding:0,margin:0},children:[
        hasStart ? line(dot('S', fresh), state.origin.title || state.origin.coordinates.join(', '), null, 'start') : null,
        ...order.map((id,i)=>{ const stop = byId.get(id); const runs = fresh && data.street_runs ? data.street_runs[i] : null; return stop ? line(dot(fresh ? i+1 : '•', fresh), stop.title || stop.coordinates.join(', '), state.next_id===id ? jsx('div',{style:{...muted,color:'var(--ui-accent)',fontWeight:600},children:'Next'}) : null, id, fresh ? LEG_COLORS[i % LEG_COLORS.length] : null, runstreet(runs)) : null }),
        fresh && data.round_trip ? line(dot('S', true), (state.return_origin || state.origin)?.title || 'Start', null, 'home', HOME_COLOR, runstreet(fresh && data.street_runs ? data.street_runs[data.street_runs.length - 1] : null)) : null,
        fresh && state.endpoint && !state.round_trip ? line(dot('E', true), state.endpoint.title || state.endpoint.coordinates.join(', '), null, 'end', HOME_COLOR) : null]})
  const repair = fresh && solution.repair ? jsxs('div',{style:{...muted,borderTop:'1px solid var(--ui-stroke-tertiary)',paddingTop:10},children:[
      jsx('div',{style:{fontWeight:600,color:'var(--ui-text-primary)'},children:solution.repair.summary}),
      ...solution.repair.added.map(s=>jsx('div',{children:`Added ${short(s.title)} as stop ${s.position}`},'a'+s.id)),
      ...solution.repair.removed.map(s=>jsx('div',{children:`Removed ${short(s.title)}`},'r'+s.id)),
      ...solution.repair.moved.map(s=>jsx('div',{children:`${short(s.title)}: stop ${s.previous_position} → ${s.position}`},'m'+s.id))]}) : null
  const nav = fresh ? jsx(Handoff,{nav:data.navigation,showQr,onToggleQr:()=>setShowQr(!showQr)}) : null
  const visited = state.visited.length ? jsxs('details',{style:muted,children:[jsx('summary',{style:{cursor:'pointer'},children:`Visited · ${state.visited.length}`}),jsx('ul',{style:{margin:'6px 0',paddingLeft:18},children:state.visited.map(id=>jsx('li',{children:label(byId.get(id))},id))})]}) : null
  const map = data.map_document ? jsx(SandboxedFrame,{src:'data:text/html;charset=utf-8,'+encodeURIComponent(data.map_document),sandbox:'allow-scripts',title:'Route map',style:{width:'100%',height:'100%',minHeight:compact?260:420,border:0,borderRadius:compact?10:12,display:'block'}}) : paragraph('Map unavailable.')
  const plan = data.pending_plan ? jsx(PendingPlan,{plan:data.pending_plan,state,busy:mutation.isPending,onApply:(args)=>run('confirm_plan',args)},data.pending_plan.plan_id) : null
  const status = message ? jsx('div',{role:'status',style:muted,children:message}) : jsx('div',{role:'status'})
  if (compact) return jsxs('section',{'data-revision':state.revision,style:{display:'flex',flexDirection:'column',gap:12,maxWidth:720,color:'var(--ui-text-primary)'},children:[
    plan,
    jsx('div',{style:{height:280,border:'1px solid var(--ui-stroke-secondary)',borderRadius:10,overflow:'hidden'},children:map}),
    summary, itinerary, nav,
    jsx('div',{children:jsx(Button,{onClick:openRoutePane,variant:'secondary',size:'sm',children:'Show map beside the chat'})}),
    status]})
  // Beside the chat: the map takes the height, the itinerary sits under it.
  return jsxs('section',{'data-revision':state.revision,style:{display:'flex',flexDirection:'column',height:'100%',minHeight:0,color:'var(--ui-text-primary)'},children:[
    jsx('div',{style:{flex:'1 1 60%',minHeight:320,padding:10,paddingBottom:0},children:jsx('div',{style:{height:'100%',border:'1px solid var(--ui-stroke-secondary)',borderRadius:12,overflow:'hidden'},children:map})}),
    jsxs('div',{style:{flex:'0 1 auto',maxHeight:'45%',overflowY:'auto',display:'flex',flexDirection:'column',gap:14,padding:'14px 16px'},children:[
      plan, summary, itinerary, repair, nav, visited, status]})
  ]})
}
function Surface(props) {
  const connection = useValue(host.state.connectionId), profile = useValue(host.state.profile)
  return jsx(RoutingSurface,{...props,scope:[connection,profile]},JSON.stringify([connection,profile]))
}
// The route is talked into existence in the chat; the map docks beside it.
let closePane = null, closedByUser = false
function openRoutePane() {
  if (closePane || typeof host.openWorkspace !== 'function') return
  closedByUser = false
  closePane = host.openWorkspace(`${ID}:map`, { title:'Route', minWidth:'24rem', dock:{ pane:'workspace', pos:'right' },
    render: () => jsx(Surface,{}), onClose: () => { closePane = null; closedByUser = true } })
}
function toggleRoutePane() { if (closePane) closePane(); else openRoutePane() }
function StatusChip() {
  const connection = useValue(host.state.connectionId), profile = useValue(host.state.profile)
  const query = useQuery({ queryKey:[ID, connection, profile], queryFn: () => rest('/current'), refetchInterval: 4000 })
  const seen = useRef(null)
  const data = query.data?.ok ? query.data : null
  const revision = data?.state.revision
  // A route edited from the chat brings the map in, unless the user closed it.
  useEffect(() => {
    if (revision == null) return
    if (seen.current != null && revision !== seen.current && !closedByUser) openRoutePane()
    seen.current = revision
  }, [revision])
  if (!data) return null
  const state = data.state, solution = state.solution
  const fresh = solution && data.solved_revision === state.revision
  const left = fresh ? solution.remaining_order.length : state.stops.filter(s => !s.visited).length
  const next = fresh && left ? short(state.stops.find(s => s.id === solution.remaining_order[0])?.title) : null
  const text = next ? `Next: ${next} · ${left} left` : left ? `Route: ${left} stop${left===1?'':'s'}, not planned` : 'Route'
  return jsx('button',{type:'button',onClick:toggleRoutePane,title:closePane?'Hide the route map':'Show the route map beside the chat',style:{color:next?'var(--ui-accent)':'var(--ui-text-tertiary)',fontSize:'0.6875rem',padding:'0 6px',height:'100%',background:'none',border:0,cursor:'pointer'},children:text})
}
export default { id:ID, name:'SolverForge Routing', register(ctx) {
  rest = ctx.rest
  return ctx.registerMany([
    {id:'chip',area:STATUSBAR_AREAS.right,order:126,render:()=>jsx(StatusChip,{})},
    {id:'open',area:PALETTE_AREA,data:{id:`${ID}.open`,label:'Route: show map',keywords:['route','map','stops','driving'],run:openRoutePane}},
    {id:'route-card',area:TRANSCRIPT_DIRECTIVE_AREA,data:{name:'route',render:()=>jsx(Surface,{compact:true})}}
  ])
}}
