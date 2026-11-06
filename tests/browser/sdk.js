// Browser-only SDK adapter: actual React hooks, fetch and a restrictive sandbox.
import React, {useState, useEffect} from 'react'
export const ROUTES_AREA='routes', SIDEBAR_NAV_AREA='nav', TRANSCRIPT_DIRECTIVE_AREA='directives'
export const host={state:{connectionId:{get:()=> 'isolated-browser'},profile:{get:()=> 'scratch'}}}
export const useValue=value=>value.get()
export const Button=props=>React.createElement('button',props)
export const SandboxedFrame=props=>React.createElement('iframe',{...props,sandbox:'allow-scripts',referrerPolicy:'no-referrer'})
const listeners=new Set()
export const useQueryClient=()=>({invalidateQueries:()=>listeners.forEach(fn=>fn())})
export function useQuery({queryFn}) {
 const [state,set]=useState({isPending:true})
 const load=async()=>{try {const data=await queryFn();set({data,isPending:false,dataUpdatedAt:Date.now()})}catch(error){set({error,isPending:false})}}
 useEffect(()=>{listeners.add(load);load();return()=>listeners.delete(load)},[])
 return {...state,refetch:load}
}
export function useMutation({mutationFn,onSuccess,onError}) {
 const [isPending,set]=useState(false)
 return {isPending,mutate:async args=>{set(true);try{onSuccess(await mutationFn(args))}catch(error){onError(error)}finally{set(false)}}}
}
