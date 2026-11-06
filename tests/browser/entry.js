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
createRoot(document.getElementById('root')).render(contributions.find(c=>c.area==='routes').render())
