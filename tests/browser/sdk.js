// Browser-only SDK adapter: actual React hooks, fetch and a restrictive sandbox.
import React, {useState, useEffect} from 'react'
export const PALETTE_AREA='palette', STATUSBAR_AREAS={left:'statusBar.left',right:'statusBar.right'}, TRANSCRIPT_DIRECTIVE_AREA='directives'
// openWorkspace docks the pane beside a stand-in chat column (see entry.js).
export const host={state:{connectionId:{get:()=> 'isolated-browser'},profile:{get:()=> 'scratch'}},openWorkspace:(id,options)=>{window.dispatchEvent(new CustomEvent('hermes-pane',{detail:{id,options}}));return ()=>{window.dispatchEvent(new CustomEvent('hermes-pane',{detail:null}));options.onClose?.()}}}
export const useValue=value=>value.get()
export const Button=props=>React.createElement('button',props)
export const SandboxedFrame=props=>React.createElement('iframe',{...props,sandbox:'allow-scripts',referrerPolicy:'no-referrer'})
const listeners=new Set()
export const useQueryClient=()=>({invalidateQueries:()=>listeners.forEach(fn=>fn())})
export function useQuery({queryFn,refetchInterval}) {
 const [state,set]=useState({isPending:true})
 const load=async()=>{try {const data=await queryFn();set({data,isPending:false,dataUpdatedAt:Date.now()})}catch(error){set({error,isPending:false})}}
 // Honour refetchInterval like the real query client: chat edits must reach the pane.
 useEffect(()=>{listeners.add(load);load();const timer=refetchInterval?setInterval(load,refetchInterval):null;return()=>{listeners.delete(load);if(timer)clearInterval(timer)}},[])
 return {...state,refetch:load}
}
export function useMutation({mutationFn,onSuccess,onError}) {
 const [isPending,set]=useState(false)
 return {isPending,mutate:async args=>{set(true);try{onSuccess(await mutationFn(args))}catch(error){onError(error)}finally{set(false)}}}
}
