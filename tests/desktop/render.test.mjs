import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile,writeFile,mkdir} from 'node:fs/promises'
import {renderToStaticMarkup} from 'react-dom/server'
import {setFixture,calls} from '@hermes/plugin-sdk'
await mkdir(new URL('./.build/',import.meta.url),{recursive:true})
const source=await readFile(new URL('../../desktop/plugin.js',import.meta.url),'utf8')
await writeFile(new URL('./.build/plugin.mjs',import.meta.url),source)
const {default:plugin,mapDocument}=await import('./.build/plugin.mjs')
let contributions
plugin.register({rest:()=>{throw Error('SSR must not call backend')},registerMany:items=>{contributions=items;return ()=>{}}})
const stop={id:'one',title:'Library',coordinates:[9.01,45.01],visited:false}
const home={title:'Home & <safe>',coordinates:[9,45]}
function data(solution=null){return {ok:true,revision:3,solved_revision:solution?3:null,engine:{available:true},provider:{url:'https://router.project-osrm.org'},state:{revision:3,solved_revision:solution?3:null,origin:{title:'Current supplied origin',coordinates:[9.02,45.02]},return_origin:home,endpoint:null,round_trip:true,stops:[stop],visited:[],next_id:null,precedence:[],solution}}}
const solution={revision:3,remaining_order:['one'],itinerary_ids:['origin','one','return'],road_duration_seconds:120,distance_m:1200,geometry:{coordinates:[[9.02,45.02],[9.01,45.01],[9,45]]}}
function renderPage(value){setFixture(value);return renderToStaticMarkup(contributions.find(c=>c.area==='routes').render())}
test('exact public SDK surfaces, no app bypasses',()=>{
 assert.equal(contributions.length,3)
 assert.equal(contributions.find(c=>c.area==='directives').data.name,'route')
 assert.ok(!/host\.(request|composer)|iframe|dangerouslySetInnerHTML|document\./.test(source))
 assert.deepEqual(calls,[])
})
test('current road geometry, stable home, driving disclaimers and coordinate controls',()=>{
 const html=renderPage({data:data(solution),dataUpdatedAt:1})
 for(const text of ['Current for this revision','Stable return home','Current supplied origin','Library','Best-found, not proven optimal','Estimated driving: 2 min','Set origin','Add stop','Solve road route','Read-only schematic']) assert.ok(html.includes(text),text)
 const frame=mapDocument(data(solution).state)
 for(const text of ['polyline','Home &amp; &lt;safe&gt;','OpenStreetMap','OSRM','not a full slippy map','>2</text>'])assert.ok(frame.includes(text),text)
 assert.ok(!frame.includes('<script'))
 assert.ok(!frame.includes('<iframe'))
})
test('pending edits hide geometry and label unsolved input order',()=>{
 const html=renderPage({data:data()})
 assert.ok(html.includes('pending solve'))
 assert.ok(html.includes('input order, not a proposed route'))
 assert.ok(!decodeURIComponent(html.match(/src="([^"]+)"/)[1]).includes('<polyline'))
})
test('directive is read only, loading, errors, missing engine',()=>{
 setFixture({data:data(solution)})
 const card=renderToStaticMarkup(contributions.find(c=>c.area==='directives').data.render({attrs:{}}))
 assert.ok(card.includes('Read-only current route'))
 assert.ok(!card.includes('Mark visited'))
 assert.ok(renderPage({isPending:true}).includes('Loading route'))
 assert.ok(renderPage({error:new Error('offline')}).includes('Could not load route'))
 const result=data();result.engine.available=false
 assert.ok(renderPage({data:result}).includes('Routing engine missing'))
})
