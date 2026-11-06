import React from 'react'
import {createRoot} from 'react-dom/client'
import plugin from '../../desktop/plugin.js'
let contributions
plugin.register({registerMany:items=>{contributions=items},rest:async(path,options={})=>{
 const response=await fetch('/api'+path,{method:options.method||'GET',headers:{'Content-Type':'application/json'},body:options.body?JSON.stringify(options.body):undefined})
 const result=await response.json()
 if(!response.ok)throw Error(result.error?.message||`HTTP ${response.status}`)
 return result
}})
// ?card=1 renders the read-only chat card (directive) instead of the Route page.
const card=new URLSearchParams(location.search).has('card')
createRoot(document.getElementById('root')).render(card?contributions.find(c=>c.area==='directives').data.render({attrs:{}}):contributions.find(c=>c.area==='routes').render())
