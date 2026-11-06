import React, {useEffect, useState} from 'react'
import {createRoot} from 'react-dom/client'
import plugin from '../../desktop/plugin.js'
let contributions
plugin.register({registerMany:items=>{contributions=items},rest:async(path,options={})=>{
 const response=await fetch('/api'+path,{method:options.method||'GET',headers:{'Content-Type':'application/json'},body:options.body?JSON.stringify(options.body):undefined})
 const result=await response.json()
 if(!response.ok)throw Error(result.error?.message||`HTTP ${response.status}`)
 return result
}})
const find=area=>contributions.find(c=>c.area===area)
// Desktop stand-in: a chat column, the docked Route pane beside it, and the status bar.
function App(){
 const [pane,setPane]=useState(null)
 useEffect(()=>{document.body.style.margin='0'},[])
 useEffect(()=>{const on=e=>setPane(e.detail);window.addEventListener('hermes-pane',on)
  // Open the pane the way the palette command does, unless the test asks for a closed start.
  if(!new URLSearchParams(location.search).has('closed')) find('palette').data.run()
  return()=>window.removeEventListener('hermes-pane',on)},[])
 const card=new URLSearchParams(location.search).has('card')
 const e=React.createElement
 return e('div',{style:{display:'flex',flexDirection:'column',height:'100vh'}},
  e('style',null,'body{margin:0}'),
  e('div',{style:{flex:1,display:'flex',minHeight:0}},
   e('main',{id:'chat',style:{flex:'1 1 55%',minWidth:0,overflow:'auto',padding:16,borderRight:'1px solid #334'}},card?find('directives').data.render({attrs:{}}):e('p',null,'Chat')),
   pane?e('section',{id:'pane','aria-label':pane.options.title,style:{flex:'1 1 45%',minWidth:0,minHeight:0,display:'flex',flexDirection:'column'}},pane.options.render()):null),
  e('footer',{id:'statusbar',style:{height:24,display:'flex',justifyContent:'flex-end',borderTop:'1px solid #334'}},find('statusBar.right').render()))
}
createRoot(document.getElementById('root')).render(React.createElement(App))
