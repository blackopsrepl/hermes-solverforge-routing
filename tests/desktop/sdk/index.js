import { jsx } from 'react/jsx-runtime'
let fixture
export const calls=[]
export function setFixture(value){fixture=value}
export const Button=({onClick,disabled,children})=>jsx('button',{onClick,disabled,children})
export const SandboxedFrame=props=>jsx('div',{'data-src':props.src,title:props.title})
export const panes=[]
export const host={state:{connectionId:{get:()=>null},profile:{get:()=> 'default'}},openWorkspace:(id,options)=>{panes.push({id,...options});return ()=>{}}}
export const useValue=atom=>atom.get()
export const useQuery=options=>({...fixture,refetch:()=>calls.push('refresh')})
export const useMutation=()=>({mutate:args=>calls.push(args),isPending:false})
export const useQueryClient=()=>({invalidateQueries:()=>{}})
export const PALETTE_AREA='palette',STATUSBAR_AREAS={left:'statusBar.left',right:'statusBar.right'},TRANSCRIPT_DIRECTIVE_AREA='directives'
