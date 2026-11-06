import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile,writeFile,mkdir} from 'node:fs/promises'
import {renderToStaticMarkup} from 'react-dom/server'
import {setFixture,calls,panes} from '@hermes/plugin-sdk'
await mkdir(new URL('./.build/',import.meta.url),{recursive:true})
const source=await readFile(new URL('../../desktop/plugin.js',import.meta.url),'utf8')
await writeFile(new URL('./.build/plugin.mjs',import.meta.url),source)
const {default:plugin}=await import('./.build/plugin.mjs')
let contributions
plugin.register({rest:()=>{throw Error('SSR must not call backend')},registerMany:items=>{contributions=items;return ()=>{}}})
const stop={id:'one',title:'Library',coordinates:[9.01,45.01],visited:false}
const home={title:'Home & <safe>',coordinates:[9,45]}
function data(solution=null){return {ok:true,map_document:'<!doctype html><p>Scoped map document</p>',revision:3,solved_revision:solution?3:null,engine:{available:true},provider:{url:'https://router.project-osrm.org'},state:{revision:3,solved_revision:solution?3:null,origin:{title:'Current supplied origin',coordinates:[9.02,45.02]},return_origin:home,endpoint:null,round_trip:true,stops:[stop],visited:[],next_id:null,precedence:[],solution}}}
const solution={revision:3,remaining_order:['one'],itinerary_ids:['origin','one','return'],road_duration_seconds:120,distance_m:1200,geometry:{coordinates:[[9.02,45.02],[9.01,45.01],[9,45]]}}
// The Route view lives in a pane docked beside the chat, opened from the palette.
function renderPage(value){setFixture(value);if(!panes.length)contributions.find(c=>c.area==='palette').data.run();return renderToStaticMarkup(panes[0].render())}
test('exact public SDK surfaces, no app bypasses',()=>{
 assert.deepEqual(contributions.map(c=>c.area).sort(),['directives','palette','statusBar.right'])
 assert.equal(contributions.find(c=>c.area==='directives').data.name,'route')
 contributions.find(c=>c.area==='palette').data.run()
 assert.equal(panes.length,1)
 assert.deepEqual(panes[0].dock,{pane:'workspace',pos:'right'},'the map docks beside the chat')
 assert.ok(!/host\.(request|composer)|iframe|dangerouslySetInnerHTML|document\./.test(source))
 assert.deepEqual(calls,[])
})
test('map first, then the itinerary in plain words; no forms',()=>{
 const html=renderPage({data:data(solution),dataUpdatedAt:1})
 for(const text of ['Route map','Route','2 min · 1.2 km','1 stop · back to start','Current supplied origin','Library']) assert.ok(html.includes(text),text)
 assert.ok(html.includes(encodeURIComponent(data().map_document)))
 assert.ok(!/<input|<select|<form|<textarea/.test(html),'the route is edited in chat, not in a form')
 const visible=html.replace(/<[^>]*>/g,' ')
 for(const meta of ['revision','Active profile','Best-found','disclose','sends exactly','Read-only','Last read']) assert.ok(!visible.includes(meta),meta)
 const prefixes=source.match(/const LINK_PREFIXES = \[[^\]]*\]/)[0]
 assert.ok(!/\beval\(|new Function|import\(|\bfetch\(|https?:/.test(source.replace(prefixes,'')))
 assert.ok(!/target\s*:|_blank|window\.open/.test(source),'links must not use target/_blank (Electron denies them)')
})
test('empty route: the map and one sentence pointing to the chat',()=>{
 const empty=data();empty.state.origin=null;empty.state.stops=[]
 const html=renderPage({data:empty})
 for(const text of ['Route map','Tell Hermes where you’re going','share your location in Telegram']) assert.ok(html.includes(text),text)
 assert.ok(!html.includes('Plan route'))
})
test('unplanned stops: unnumbered, and one Plan route button',()=>{
 const html=renderPage({data:data()})
 assert.ok(html.includes('Not planned yet') && html.includes('Plan route'))
 assert.ok(html.includes('Library') && !html.includes('2 min'))
})
test('chat card is the same view, compact; loading, errors, missing engine',()=>{
 setFixture({data:data(solution)})
 const card=renderToStaticMarkup(contributions.find(c=>c.area==='directives').data.render({attrs:{}}))
 assert.ok(card.includes('Library') && card.includes('Route map') && card.includes('Show map beside the chat'))
 assert.ok(renderPage({isPending:true}).includes('Loading route'))
 assert.ok(renderPage({error:new Error('offline')}).includes('Could not load the route'))
 const result=data();result.engine.available=false
 assert.ok(renderPage({data:result}).includes('Routing engine not installed'))
})

const G='https://www.google.com/maps/dir/?api=1&origin=45.020000%2C9.020000&destination=45.000000%2C9.000000&travelmode=driving&waypoints=45.010000%2C9.010000'
const A='https://maps.apple.com/directions?source=45.020000,9.020000&destination=45.000000,9.000000&waypoint=45.010000,9.010000&mode=driving'
const O1='https://www.openstreetmap.org/directions?engine=fossgis_osrm_car&route=45.020000%2C9.020000%3B45.010000%2C9.010000'
const O2='https://www.openstreetmap.org/directions?engine=fossgis_osrm_car&route=45.010000%2C9.010000%3B45.000000%2C9.000000'
const qr={size:21,path:'M4,4h1v1h-1zM6,4h1v1h-1z',svg:'<svg></svg>'}
const points=[0,1,2].map(n=>({number:n,id:String(n),title:'P'+n,coordinates:[9,45]}))
function nav(extra={}){return {revision:3,points,google:{max_waypoints:9,split:false,legs:[{from:0,to:2,url:G}]},google_mobile:{max_waypoints:3,split:false,legs:[{from:0,to:2,url:G,qr}]},apple:{legs:[{from:0,to:2,url:A}]},osm:{legs:[{from:0,to:1,url:O1},{from:1,to:2,url:O2}]},disclosure:'Opening a link or scanning a code sends these stop coordinates (not titles) to that provider.',...extra}}
const valued={...solution,comparison:{message:'9 min shorter than the order you gave'},repair:{summary:'1 stop added · 2 min longer than the previous route',added:[{id:'one',title:'Library',position:1}],removed:[{id:'x',title:'Old cafe'}],visited:[],moved:[]}}
test('navigation handoff: sanitised external links, per-leg OSM, inline QR',()=>{
 const html=renderPage({data:{...data(valued),navigation:nav()}})
 const amp=u=>u.replaceAll('&','&amp;')
 for(const url of [G,A,O1,O2]) assert.ok(html.includes(`href="${amp(url)}"`),url)
 for(const text of ['Navigate','Google Maps ↗','Apple Maps ↗','OpenStreetMap','Start → stop 1','Send to phone (QR code)']) assert.ok(html.includes(text),text)
 assert.ok(html.includes('rel="noopener noreferrer"') && !html.includes('target='))
 assert.ok(!html.includes('<svg role="img"'),'QR codes stay folded until asked for')
 assert.ok(html.includes('9 min shorter than the order you gave'))
 assert.ok(html.includes('1 stop added') && html.includes('Added Library as stop 1') && html.includes('Removed Old cafe'))
})
test('unsafe or unknown links and malformed QR are never rendered',()=>{
 const bad=nav({google:{legs:[{from:0,to:2,url:'javascript:alert(1)'}]},apple:{legs:[{from:0,to:2,url:'https://evil.example/maps.apple.com/directions?'}]},osm:{legs:[{from:0,to:1,url:'http://www.openstreetmap.org/directions?engine=fossgis_osrm_car&route=1'}]},google_mobile:{legs:[{from:0,to:2,url:G,qr:{size:21,path:'M0,0"/><script>'}}]}})
 const html=renderPage({data:{...data(solution),navigation:bad}})
 assert.ok(!html.includes('javascript:') && !html.includes('evil.example') && !html.includes('http://www.openstreetmap'))
 assert.ok(!html.includes('<script>'))
 assert.ok(!renderPage({data:{...data(),navigation:nav()}}).includes('Google Maps ↗'),'no handoff for an unsolved revision')
})
const plan={plan_id:'p'.repeat(32),base_revision:3,expires_at:1900000000,round_trip:true,endpoint_action:'keep',items:[
 {index:0,role:'origin',status:'supplied',query:null,location:{title:'Shared location (shared 09:12)',coordinates:[9.17,45.47]},candidates:[]},
 {index:1,role:'stop',status:'preselected',query:'Duomo',region:'Milano',preselected:'a'.repeat(32),candidates:[{candidate_id:'a'.repeat(32),label:'Duomo di Milano',coordinates:[9.19,45.46],kind:'place of worship',locality:'Milano',distance_from_start_m:2300,far:false}]},
 {index:2,role:'stop',status:'needs_choice',reason:'same_name',far:false,note:'Same street name in 2 places (Legnano, Sesto San Giovanni) · none in Milano · choose one',query:'Via Roma',region:'Milano',preselected:null,candidates:[{candidate_id:'b'.repeat(32),label:'Via Roma, Sesto San Giovanni',coordinates:[9.23,45.53],kind:'street',locality:'Sesto San Giovanni',distance_from_start_m:5645,far:false},{candidate_id:'c'.repeat(32),label:'Via Roma, Legnano',coordinates:[8.91,45.6],kind:'street',locality:'Legnano',distance_from_start_m:25910,far:true}]},
 {index:3,role:'stop',status:'no_match',query:'Nowhere',region:'Milano',preselected:null,candidates:[]}]}
test('pending plan shows choices and one apply button, also in the chat card',()=>{
 setFixture({data:{...data(),pending_plan:plan}})
 const card=renderToStaticMarkup(contributions.find(c=>c.area==='directives').data.render({attrs:{}}))
 for(const text of ['Places to add','Start: Shared location (shared 09:12)','One match','Same street name in 2 places (Legnano, Sesto San Giovanni) · none in Milano · choose one','street · Legnano · 25.9 km from start','far away','place of worship · Milano · 2.3 km from start','street · Sesto San Giovanni · 5.6 km from start','Not found','Skip','Add 2 places','return home']) assert.ok(card.includes(text),text)
 assert.ok(/<button disabled="">Add 2 places</.test(card),'apply waits for the unresolved choice')
 assert.ok(!card.includes('Mark visited'))
 assert.equal(card.split(' · far away').length-1,1,'only the far candidate is flagged')
 const stale=renderPage({data:{...data(),pending_plan:{...plan,base_revision:2}}})
 assert.ok(stale.includes('The route changed since'))
 assert.ok(source.includes("run('confirm_plan',args)"))
})
