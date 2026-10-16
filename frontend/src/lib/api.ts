const API=process.env.NEXT_PUBLIC_API_URL||'http://localhost:8000';
export async function api<T>(path:string, options:RequestInit={}){const r=await fetch(API+path,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})},cache:'no-store'});if(!r.ok) throw new Error((await r.json().catch(()=>({detail:'Request failed'}))).detail||'Request failed');return r.json() as Promise<T>}
export {API};
