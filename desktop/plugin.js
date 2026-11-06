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
// Geocoder titles are full postal strings; people read the place name first.
const splitName = text => { const parts = String(text || '').split(',').map(s => s.trim()).filter(Boolean); const n = parts.length > 1 && /^\d+[a-z]?$/i.test(parts[0]) ? 2 : 1; return [parts.slice(0, n).reverse().join(' '), parts.slice(n, n + 2).join(', ')] }
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
function Handoff({nav}) {
  if (!nav) return null
  const points = nav.points
  const group = (name, legs) => legs.length === 1 ? [jsx(ExternalLink,{url:legs[0].url,text:`Open in ${name}`},name)] : legs.map((leg,i)=>jsx(ExternalLink,{url:leg.url,text:`${name} · leg ${i+1}/${legs.length} (${legName(leg,points)})`},`${name}${i}`))
  const mobile = nav.google_mobile.legs
  return jsxs('div',{style:{...panel,display:'flex',flexDirection:'column',gap:10},children:[
    jsx('h3',{style:{margin:0},children:'Navigate'}),
    jsxs('div',{style:row,children:[...group('Google Maps',nav.google.legs),...group('Apple Maps',nav.apple.legs)]}),
    jsxs('details',{children:[jsx('summary',{style:{cursor:'pointer'},children:'OpenStreetMap'}),
      jsx('div',{style:{...row,marginTop:8},children:nav.osm.legs.map((leg,i)=>jsx(ExternalLink,{url:leg.url,text:legName(leg,points)},`osm${i}`))})]}),
    paragraph('Scan to open on your phone'),
    jsx('div',{style:{...row,alignItems:'flex-start',gap:16},children:mobile.map((leg,i)=>jsx(QrCode,{qr:leg.qr,caption:mobile.length>1?`Leg ${i+1}/${mobile.length}: ${legName(leg,points)}`:'Whole route'},`qr${i}`))}),
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
function RoutingSurface({ editable = false, scope }) {
  const client = useQueryClient()
  const key = [ID, ...scope]
  const query = useQuery({ queryKey: key, queryFn: () => rest('/current'), refetchInterval: 15000 })
  const [message, setMessage] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [region, setRegion] = useState('')
  const [results, setResults] = useState(null)
  const [searching, setSearching] = useState(false)
  const [title, setTitle] = useState('')
  const [coordinates, setCoordinates] = useState('')
  const [pending, setPending] = useState(null)
  const [before, setBefore] = useState('')
  const [after, setAfter] = useState('')
  const mutation = useMutation({ mutationFn: ({action, args}) => {
    if (host.state.connectionId.get() !== scope[0] || host.state.profile.get() !== scope[1]) throw new Error('Profile changed. Refresh.')
    return rest(`/actions/${action}`, { method: 'POST', body: args })
  }, onSuccess: result => { setMessage(result.ok ? 'Route updated.' : result.error?.message || 'Edit refused.'); setPending(null); client.invalidateQueries({queryKey:key}) }, onError: error => { setMessage(/expected revision|route changed|plan was made/i.test(error.message || '') ? 'The route changed. Refreshed.' : error.message || 'Something went wrong. Refresh.'); setPending(null); client.invalidateQueries({queryKey:key}) } })
  if (query.isPending) return paragraph('Loading route…')
  if (query.error || !query.data?.ok) return paragraph(query.data?.error?.message || 'Could not load the route.')
  const data = query.data, state = data.state, solution = state.solution
  const fresh = solution && data.solved_revision === state.revision
  const byId = new Map(state.stops.map(s => [s.id,s]))
  const order = fresh ? solution.remaining_order : state.stops.filter(s => !s.visited).map(s => s.id)
  // Buttons are the deliberate step: they edit directly. Only removing a stop asks again.
  const run = (name,args={}) => mutation.mutate({action:name,args:{...args,expected_revision:state.revision}})
  const action = (name,args={}) => name === 'remove_stop' ? setPending({action:name,args:{...args,expected_revision:state.revision}}) : run(name,args)
  const button = (text, click, disabled=false, variant='secondary') => jsx(Button,{onClick:click,disabled:disabled || mutation.isPending,variant,size:'sm',children:text},text)
  const primary = (text, click, disabled=false) => button(text, click, disabled, 'default')
  const search = async () => {
    if (host.state.connectionId.get() !== scope[0] || host.state.profile.get() !== scope[1]) return
    setSearching(true); setResults(null)
    try {
      const result = await rest('/actions/search_locations', {method:'POST',body:{query:searchQuery,region}})
      if (result.ok) setResults(result)
      else setMessage(result.error?.message || 'Search failed.')
    } catch (error) { setMessage(error.message || 'Search unavailable.') }
    finally { setSearching(false) }
  }
  const usePlace = (candidate, role) => { action('confirm_location',{candidate_id:candidate.candidate_id,role}); setResults(null); setSearchQuery('') }
  const useCoordinates = (name, field) => {
    const parts = coordinates.split(',').map(s=>s.trim())
    if (parts.length!==2 || parts.some(s=>!s || !Number.isFinite(Number(s)))) { setMessage('Enter longitude, latitude.'); return }
    action(name,{[field]:{title,coordinates:parts.map(Number)}}); setTitle(''); setCoordinates('')
  }
  const home = state.return_origin || state.return_location || state.home || state.start_origin
  const hasStart = Boolean(state.origin)
  const open = state.stops.filter(s => !s.visited)
  const canPlan = hasStart && open.length > 0 && data.engine.available
  const removing = pending?.action === 'remove_stop' ? byId.get(pending.args.stop_id) : null
  const resultActions = candidate => hasStart
    ? [primary('Add stop',()=>usePlace(candidate,'stop')), button('End here',()=>usePlace(candidate,'endpoint'),false,'text')]
    : [primary('Start here',()=>usePlace(candidate,'origin'))]
  const stopRow = (id, index) => {
    const stop = byId.get(id); if (!stop) return null
    const isNext = state.next_id === id
    return jsxs('li',{style:{...row,padding:'12px 0',borderTop:'1px solid var(--ui-stroke-tertiary)'},children:[
      jsx('span',{style:{display:'grid',placeItems:'center',width:30,height:30,borderRadius:'50%',background:fresh?'var(--ui-accent)':'var(--ui-bg-quaternary)',color:fresh?'var(--ui-bg-primary, white)':'var(--ui-text-primary)',fontWeight:700},children:fresh?index+1:'•'}),
      jsxs('div',{style:{flex:1,minWidth:160},children:[jsx(NameLines,{text:stop.title || stop.coordinates.join(', ')}),isNext?jsx('div',{style:{...muted,color:'var(--ui-accent)',fontWeight:600},children:'Going here next'}):null]}),
      editable?jsxs('span',{style:row,children:[
        isNext ? button('Don’t go next',()=>action('set_next',{stop_id:null}),false,'text') : button('Go here next',()=>action('set_next',{stop_id:id}),false,'text'),
        button('Visited',()=>action('mark_visited',{stop_id:id})),
        button('Remove',()=>action('remove_stop',{stop_id:id}),false,'outline')]}):null]},id)
  }
  const nameOf = id => short(byId.get(id)?.title) || 'Stop'
  return jsxs('section',{'data-revision':state.revision,style:{padding:24,maxWidth:1120,margin:'0 auto',display:'flex',flexDirection:'column',gap:14,color:'var(--ui-text-primary)',fontFamily:'inherit'},children:[
    jsxs('header',{style:{...row,justifyContent:'space-between'},children:[jsx('h2',{style:{margin:0,fontSize:25,letterSpacing:'-.5px'},children:'Your driving route'}),button('Refresh',()=>query.refetch(),false,'ghost')]}),
    jsxs('div',{style:{...panel,display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(200px,1fr))',gap:18,alignItems:'start'},children:[
      jsxs('div',{children:[jsx('strong',{children:'Start'}),paragraph(hasStart?label(state.origin):'Not set yet')]}),
      jsxs('div',{children:[jsx('strong',{children:'End'}),paragraph(state.round_trip?`Back to start`:state.endpoint?label(state.endpoint):'At the last stop')]}),
      jsxs('div',{children:[jsx('strong',{children:'Driving'}),
        fresh ? jsxs('div',{children:[paragraph(`${Math.ceil(solution.road_duration_seconds/60)} min · ${(solution.distance_m/1000).toFixed(1)} km · ${order.length} stop${order.length===1?'':'s'} to go`),solution.comparison?.message?jsx('p',{style:{margin:'6px 0',fontWeight:700,color:'var(--ui-accent)'},children:solution.comparison.message}):null]})
          : canPlan ? jsxs('div',{style:{...row,marginTop:6},children:[primary('Plan route',()=>run('solve',{solve_seconds:2}))]})
          : paragraph(!hasStart ? 'Add a start and a stop' : !open.length ? 'Add a stop' : 'Routing engine not installed')]})
    ]}),
    data.pending_plan ? jsx(PendingPlan,{plan:data.pending_plan,state,busy:mutation.isPending,onApply:(args)=>run('confirm_plan',args)},data.pending_plan.plan_id) : null,
    fresh && solution.repair ? jsxs('div',{style:panel,children:[jsx('strong',{children:'Changes'}),paragraph(solution.repair.summary),
      jsx('ul',{style:{...muted,margin:'4px 0',paddingLeft:20},children:[...solution.repair.added.map(s=>jsx('li',{children:`Added ${short(s.title)} as stop ${s.position}`},'a'+s.id)),...solution.repair.removed.map(s=>jsx('li',{children:`Removed ${short(s.title)}`},'r'+s.id)),...solution.repair.visited.map(s=>jsx('li',{children:`Visited ${short(s.title)}`},'v'+s.id)),...solution.repair.moved.map(s=>jsx('li',{children:`${short(s.title)}: stop ${s.previous_position} → ${s.position}`},'m'+s.id))]})]}) : null,
    data.map_document ? jsx(SandboxedFrame,{src:'data:text/html;charset=utf-8,'+encodeURIComponent(data.map_document),sandbox:'allow-scripts',title:'Route map',style:{width:'100%',height:'clamp(320px, 38vw, 460px)',border:'1px solid var(--ui-stroke-secondary)',borderRadius:12,flexShrink:0}}) : null,
    jsxs('div',{style:panel,children:[
      jsx('h3',{style:{margin:'0 0 4px'},children:fresh?'Itinerary':'Stops'}),
      order.length ? jsx('ol',{style:{listStyle:'none',padding:0,margin:0},children:order.map(stopRow)}) : paragraph(hasStart ? 'No stops yet.' : 'Search for where you start, then add the places you want to visit.'),
      fresh && state.round_trip ? paragraph(`Then back to start`) : fresh && state.endpoint ? paragraph(`Then end at ${label(state.endpoint)}`) : null,
      state.visited.length ? jsxs('details',{style:{marginTop:12},children:[jsx('summary',{style:{cursor:'pointer'},children:`Visited · ${state.visited.length}`}),jsx('ul',{children:state.visited.map(id=>jsx('li',{children:label(byId.get(id))},id))})]}):null,
      removing ? jsxs('div',{role:'alert',style:{...row,marginTop:12,padding:12,borderRadius:8,border:'1px solid var(--ui-accent)'},children:[jsx('span',{style:{flex:1},children:`Remove ${short(removing.title) || 'this stop'}?`}),primary('Remove',()=>mutation.mutate(pending)),button('Keep',()=>setPending(null),false,'text')]}) : null
    ]}),
    fresh ? jsx(Handoff,{nav:data.navigation}) : null,
    editable ? jsxs('div',{style:{...panel,display:'flex',flexDirection:'column',gap:12},children:[
      jsx('h3',{style:{margin:0},children:hasStart?'Add a stop':'Where do you start?'}),
      jsxs('form',{style:row,onSubmit:e=>{e.preventDefault(); if(searchQuery.trim() && !searching) search()},children:[
        jsx('input',{style:{...field,flex:2},value:searchQuery,disabled:searching,maxLength:300,onChange:e=>{setSearchQuery(e.target.value);setResults(null)},'aria-label':'Place or address',placeholder:'Place or street address'}),
        jsx('input',{style:field,value:region,disabled:searching,maxLength:200,onChange:e=>{setRegion(e.target.value);setResults(null)},'aria-label':'City or region',placeholder:'City or region'}),
        jsx(Button,{type:'submit',disabled:searching || !searchQuery.trim(),size:'sm',children:searching?'Searching…':'Search'})]}),
      results ? (results.candidates.length ? jsx('ul',{style:{listStyle:'none',padding:0,margin:0},children:results.candidates.map(candidate=>jsxs('li',{style:{...row,padding:'10px 0',borderTop:'1px solid var(--ui-stroke-tertiary)'},children:[
          jsx('div',{style:{flex:1,minWidth:200,overflowWrap:'anywhere'},children:jsx(CandidateText,{c:candidate})}),
          jsx('span',{style:row,children:resultActions(candidate)})]},candidate.candidate_id))}) : paragraph('No matches.')) : null,
      results && results.expected_kind && results.candidates.length && !results.candidates.some(c=>c.matches_query_kind) ? paragraph(`No ${results.expected_kind} found. Try the official name.`) : null,
      jsxs('details',{children:[jsx('summary',{style:{cursor:'pointer',...muted},children:'Use coordinates instead'}),
        jsxs('div',{style:{...row,marginTop:10},children:[
          jsx('input',{style:field,value:title,onChange:e=>setTitle(e.target.value),placeholder:'Name','aria-label':'Name'}),
          jsx('input',{style:field,value:coordinates,onChange:e=>setCoordinates(e.target.value),placeholder:'longitude, latitude','aria-label':'Longitude, latitude'}),
          ...(hasStart ? [primary('Add stop',()=>useCoordinates('add_stop','stop')),button('End here',()=>useCoordinates('set_end','endpoint'),false,'text')] : [primary('Start here',()=>useCoordinates('set_origin','origin'))])]})]})
    ]}) : null,
    editable && hasStart ? jsxs('div',{style:{...panel,display:'flex',flexDirection:'column',gap:12},children:[
      jsx('h3',{style:{margin:0},children:'Preferences'}),
      jsxs('label',{style:{...row,cursor:'pointer'},children:[jsx('input',{type:'checkbox',checked:Boolean(state.round_trip),disabled:mutation.isPending,onChange:()=>action('set_round_trip',{enabled:!state.round_trip})}),'Return to the start at the end']}),
      state.endpoint && !state.round_trip ? jsxs('div',{style:row,children:[jsx('span',{children:`Ends at ${label(state.endpoint)}`}),button('Remove end',()=>action('set_end',{endpoint:null}),false,'text')]}) : null,
      open.length > 1 ? jsxs('div',{style:{display:'flex',flexDirection:'column',gap:8},children:[
        jsx('strong',{children:'Visit order'}),
        ...state.precedence.map(([a,b],i)=>jsxs('div',{style:row,children:[jsx('span',{children:`${nameOf(a)} before ${nameOf(b)}`}),button('Remove',()=>action('precedence',{pairs:state.precedence.filter((_,n)=>n!==i)}),false,'text')]},`${a}:${b}:${i}`)),
        jsxs('div',{style:row,children:[
          jsx('span',{children:'Visit'}),
          jsx('select',{style:{...field,flex:'0 1 220px',minWidth:140},'aria-label':'First',value:before,onChange:e=>setBefore(e.target.value),children:[jsx('option',{value:'',children:'choose a stop'},'placeholder'),...open.map(s=>jsx('option',{value:s.id,children:short(s.title) || s.id},s.id))]}),
          jsx('span',{children:'before'}),
          jsx('select',{style:{...field,flex:'0 1 220px',minWidth:140},'aria-label':'Then',value:after,onChange:e=>setAfter(e.target.value),children:[jsx('option',{value:'',children:'choose a stop'},'placeholder'),...open.map(s=>jsx('option',{value:s.id,children:short(s.title) || s.id},s.id))]}),
          button('Add',()=>{action('precedence',{pairs:[...state.precedence,[before,after]]});setBefore('');setAfter('')},!before || !after || before===after)]})]}) : null
    ]}) : null,
    jsx('div',{role:'status',style:muted,children:message}), mutation.isPending?paragraph('Working…'):null
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
