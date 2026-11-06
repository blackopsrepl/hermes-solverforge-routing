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
const label = place => place ? (place.title || place.coordinates.join(', ')) : 'Not set'
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
  return jsxs('span',{children:[` ${c.label}`,jsxs('span',{style:{...muted,display:'block',marginLeft:22},children:[facts(c),farFlag(c)]})]})
}
const initialChoice = item => item.status === 'supplied' ? 'use' : item.preselected || (item.status === 'needs_choice' ? null : 'skip')
const ROLE = { origin:'Start', stop:'Stop', endpoint:'End' }
const CONFIRM = {
  solve: () => 'Solve the route?',
  confirm_location: (p, place) => `${{origin:'Start from',stop:'Add stop',endpoint:'End at'}[p.args.role]} ${place}?`,
  confirm_plan: p => `Add ${p.count} place${p.count===1?'':'s'}?`,
  set_origin: () => 'Set the start?', add_stop: () => 'Add this stop?', set_end: p => p.args.endpoint ? 'Set the end?' : 'Clear the end?',
  set_round_trip: p => p.args.enabled ? 'Return home at the end?' : 'Don’t return home?',
  set_next: p => p.args.stop_id ? 'Go here next?' : 'Clear the next stop?',
  mark_visited: () => 'Mark as visited?', remove_stop: () => 'Remove this stop?', precedence: () => 'Update the visit order?'
}
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
  const [chosen, setChosen] = useState(null)
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
  const action = (name,args={}) => setPending({action:name,args:{...args,expected_revision:state.revision}})
  const button = (text, click, disabled=false) => jsx(Button,{onClick:click,disabled:disabled || mutation.isPending,children:text},text)
  const search = async () => {
    if (host.state.connectionId.get() !== scope[0] || host.state.profile.get() !== scope[1]) return
    setSearching(true); setResults(null); setChosen(null)
    try {
      const result = await rest('/actions/search_locations', {method:'POST',body:{query:searchQuery,region}})
      if (result.ok) setResults(result)
      else setMessage(result.error?.message || 'Search failed.')
    } catch (error) { setMessage(error.message || 'Search unavailable.') }
    finally { setSearching(false) }
  }
  const pendingCandidate = results?.candidates.find(c=>c.candidate_id===pending?.args.candidate_id)
  const confirmedLabel = pendingCandidate ? label({title:pendingCandidate.label,coordinates:pendingCandidate.coordinates}) : 'this place'
  const home = state.return_origin || state.return_location || state.home || state.start_origin
  return jsxs('section',{'data-revision':state.revision,style:{padding:24,maxWidth:1120,margin:'0 auto',display:'flex',flexDirection:'column',gap:14,color:'var(--ui-text-primary)',fontFamily:'inherit'},children:[
    jsxs('header',{style:{...row,justifyContent:'space-between'},children:[jsxs('div',{children:[jsx('h2',{style:{margin:'0 0 4px',fontSize:25,letterSpacing:'-.5px'},children:'Your driving route'})]}),button('Refresh',()=>query.refetch())]}),
    jsxs('div',{style:{...panel,display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(230px,1fr))',gap:18},children:[
      jsxs('div',{children:[jsx('strong',{children:'Start'}),paragraph(label(state.origin))]}),
      jsxs('div',{children:[jsx('strong',{children:'Driving'}),paragraph(fresh?`${Math.ceil(solution.road_duration_seconds/60)} min · ${(solution.distance_m/1000).toFixed(1)} km`:state.stops.length?'Not solved yet':'—'),fresh&&solution.comparison?.message?jsx('p',{style:{margin:'6px 0',fontWeight:700,color:'var(--ui-accent)'},children:solution.comparison.message}):null,paragraph(`${order.length} to go${state.visited.length?` · ${state.visited.length} visited`:''}`)]}),
      jsxs('div',{children:[jsx('strong',{children:'End'}),paragraph(state.round_trip?`Back home · ${label(home || state.origin)}`:state.endpoint?label(state.endpoint):'Last stop')]})
    ]}),
    !data.engine.available ? paragraph('Routing engine not installed.') : null,
    data.pending_plan ? jsx(PendingPlan,{plan:data.pending_plan,state,busy:mutation.isPending,onApply:(args,count)=>setPending({action:'confirm_plan',args,count})},data.pending_plan.plan_id) : null,
    fresh && solution.repair ? jsxs('div',{style:panel,children:[jsx('strong',{children:'Changes'}),paragraph(solution.repair.summary),
      jsx('ul',{style:{...muted,margin:'4px 0',paddingLeft:20},children:[...solution.repair.added.map(s=>jsx('li',{children:`Added ${s.title} as stop ${s.position}`},'a'+s.id)),...solution.repair.removed.map(s=>jsx('li',{children:`Removed ${s.title}`},'r'+s.id)),...solution.repair.visited.map(s=>jsx('li',{children:`Visited ${s.title}`},'v'+s.id)),...solution.repair.moved.map(s=>jsx('li',{children:`${s.title}: stop ${s.previous_position} → ${s.position}`},'m'+s.id))]})]}) : null,
    data.map_document ? jsx(SandboxedFrame,{src:'data:text/html;charset=utf-8,'+encodeURIComponent(data.map_document),sandbox:'allow-scripts',title:'Route map',style:{width:'100%',height:'clamp(360px, 42vw, 480px)',border:'1px solid var(--ui-stroke-secondary)',borderRadius:12,flexShrink:0}}) : paragraph('Map unavailable.'),
    fresh ? jsx(Handoff,{nav:data.navigation}) : null,
    jsxs('div',{style:panel,children:[
      jsx('h3',{style:{margin:'0 0 12px'},children:fresh?'Itinerary':'Stops'}),
      order.length ? jsx('ol',{style:{listStyle:'none',padding:0,margin:0},children:order.map((id,index) => { const stop=byId.get(id); if(!stop)return null; return jsxs('li',{style:{...row,padding:'12px 0',borderTop:'1px solid var(--ui-stroke-tertiary)'},children:[jsx('span',{style:{display:'grid',placeItems:'center',width:30,height:30,borderRadius:'50%',background:'var(--ui-accent)',color:'var(--ui-bg-primary, white)',fontWeight:700},children:index+1}),jsxs('div',{style:{flex:1,minWidth:160},children:[jsx('strong',{children:stop.title || stop.coordinates.join(', ')}),state.next_id===id?paragraph('Next'):null]}),editable?jsxs('span',{style:row,children:[button('Set next',()=>action('set_next',{stop_id:id})),button('Mark visited',()=>action('mark_visited',{stop_id:id})),button('Remove',()=>action('remove_stop',{stop_id:id}))]}):null]},id) })}):paragraph('No stops yet.'),
      fresh && state.round_trip ? paragraph(`${order.length + 1}. Home · ${label(home || state.origin)}`) : fresh && state.endpoint ? paragraph(`${order.length + 1}. End: ${label(state.endpoint)}`) : null,
      state.visited.length ? jsxs('details',{style:{marginTop:12},children:[jsx('summary',{children:`Visited · ${state.visited.length}`}),jsx('ul',{children:state.visited.map(id=>jsx('li',{children:label(byId.get(id))},id))})]}):null
    ]}),
    editable ? jsxs('div',{style:{...panel,display:'flex',flexDirection:'column',gap:12},children:[
      jsx('h3',{style:{margin:0},children:'Add a place'}),
      jsxs('div',{style:row,children:[jsx('input',{style:field,value:searchQuery,disabled:searching,maxLength:300,onChange:e=>{setSearchQuery(e.target.value);setResults(null);setChosen(null)},'aria-label':'Place or address',placeholder:'Place or street address'}),jsx('input',{style:field,value:region,disabled:searching,maxLength:200,onChange:e=>{setRegion(e.target.value);setResults(null);setChosen(null)},'aria-label':'City or region',placeholder:'City or region'})]}),
      button(searching?'Searching…':'Search',search,searching || !searchQuery.trim()),
      results ? jsxs('div',{children:[jsx('ul',{style:{paddingLeft:20},children:results.candidates.map(candidate=>jsx('li',{style:{marginBottom:12},children:jsxs('label',{style:{display:'block',overflowWrap:'anywhere'},children:[jsx('input',{type:'radio',name:'candidate',checked:chosen===candidate.candidate_id,onChange:()=>setChosen(candidate.candidate_id)}),jsx(CandidateText,{c:candidate})]})},candidate.candidate_id))}),
        !results.candidates.length?paragraph('No matches.'):null,
        results.expected_kind && results.candidates.length && !results.candidates.some(c=>c.matches_query_kind)?paragraph(`No ${results.expected_kind} found. Try the official name.`):null,
        jsxs('div',{style:row,children:[['Set as start','origin'],['Add stop','stop'],['Set as end','endpoint']].map(([text,role])=>button(text,()=>action('confirm_location',{candidate_id:chosen,role}),!chosen))})]}):null,
      jsx('h3',{style:{margin:0},children:'Or coordinates'}),
      jsxs('div',{style:row,children:[jsx('input',{style:field,value:title,onChange:e=>setTitle(e.target.value),placeholder:'Name','aria-label':'Name'}),
      jsx('input',{style:field,value:coordinates,onChange:e=>setCoordinates(e.target.value),placeholder:'longitude, latitude','aria-label':'Longitude, latitude'})]}),
      jsxs('div',{style:row,children:[...[['Set as start','set_origin','origin'],['Add stop','add_stop','stop'],['Set as end','set_end','endpoint']].map(([text,name,field])=>button(text,()=>{const parts=coordinates.split(',').map(s=>s.trim());if(parts.length!==2 || parts.some(s=>!s || !Number.isFinite(Number(s)))){setMessage('Enter longitude, latitude.');return}action(name,{[field]:{title,coordinates:parts.map(Number)}})})),
      ]}),
      jsxs('details',{children:[jsx('summary',{style:{cursor:'pointer',fontWeight:600},children:'Options'}),
      jsxs('div',{style:{...row,marginTop:12},children:[button('Clear end',()=>action('set_end',{endpoint:null})),button(state.round_trip?'Don’t return home':'Return home',()=>action('set_round_trip',{enabled:!state.round_trip})),
      button('Clear next',()=>action('set_next',{stop_id:null})),
      jsx('h3',{children:'Visit order'}),
      ...[['First',before,setBefore],['Then',after,setAfter]].map(([text,value,set])=>jsx('select',{style:field,'aria-label':text,value,onChange:e=>set(e.target.value),children:[jsx('option',{value:'',children:text},'placeholder'),...state.stops.filter(s=>!s.visited).map(s=>jsx('option',{value:s.id,children:s.title || s.id},s.id))]},text)),
      button('Add rule',()=>action('precedence',{pairs:[...state.precedence,[before,after]]}),!before || !after || before===after),
      jsx('ul',{children:state.precedence.map(([a,b],i)=>jsxs('li',{children:[`${label(byId.get(a))} before ${label(byId.get(b))}`,button('Remove',()=>action('precedence',{pairs:state.precedence.filter((_,n)=>n!==i)}))]},`${a}:${b}:${i}`))}),
      ]})]}),
      button('Solve route',()=>action('solve',{solve_seconds:2}),!data.engine.available),
    ]}):null,
    pending ? jsxs('div',{style:{...panel,borderColor:'var(--ui-accent)'},role:'alert',children:[paragraph(CONFIRM[pending.action]?.(pending, confirmedLabel) ?? 'Apply this change?'),button('Confirm',()=>mutation.mutate(pending)),button('Cancel',()=>setPending(null))]}):null,
    jsx('div',{role:'status',children:message}), mutation.isPending?paragraph('Working…'):null
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
