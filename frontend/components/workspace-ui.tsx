'use client';

import Link from 'next/link';
import {usePathname} from 'next/navigation';
import {useEffect,useRef,useState, type ReactNode} from 'react';
import {Activity, ArrowUpRight, ChevronDown, Info,X} from 'lucide-react';

const destinations = [['/','Companies'],['/invest','Insider signals'],['/portfolio','Portfolio'],['/research','Financials'],['/leaderboard','Leaderboard'],['/legend','Guide']] as const;

export function WorkspaceNav() {
  const pathname=usePathname();
  return <header className="workspace-nav"><div className="workspace-nav-inner">
    <Link href="/" className="workspace-brand"><Activity size={19}/><span>Stock Analyzer</span></Link>
    <nav aria-label="Main navigation" className="workspace-links">{destinations.map(([href,label])=><Link key={href} href={href} aria-current={pathname===href?'page':undefined}>{label}</Link>)}<Link href="/trade-evidence">Public trades</Link><Link href="/broker">Broker</Link><Link href="/system">Status</Link></nav>
  </div></header>;
}

export function WorkspaceHeading({eyebrow,title,description,children}:{eyebrow?:string;title:string;description?:string;children?:ReactNode}) {
  return <div className="workspace-heading"><div>{eyebrow&&<p className="workspace-eyebrow">{eyebrow}</p>}<h1>{title}</h1>{description&&<p className="workspace-description">{description}</p>}</div>{children&&<div className="workspace-actions">{children}</div>}</div>;
}

export function Disclosure({title,description,children,className=''}:{title:string;description?:string;children:ReactNode;className?:string}) {
  const [ready,setReady]=useState(false);
  return <details className={`workspace-disclosure ${className}`} onToggle={e=>{if(e.currentTarget.open)setReady(true);}}><summary><span>{title}</span><ChevronDown size={15}/></summary>{ready&&<div className="workspace-disclosure-body">{description&&<p className="mb-4 text-sm leading-6 text-zinc-400">{description}</p>}{children}</div>}</details>;
}

export function ResearchNote({children,message="Research only. Some figures still need verification."}:{children?:ReactNode;message?:string}) {
  return <div className="research-note"><Info size={14}/><span>{message}</span>{children&&<Disclosure title="What to know">{children}</Disclosure>}</div>;
}

export function MetricStrip({items}:{items:{label:string;value:ReactNode;note?:string}[]}) {
  return <div className="workspace-metrics">{items.map(i=><div key={i.label}><p>{i.label}</p><strong>{i.value}</strong>{i.note&&<small>{i.note}</small>}</div>)}</div>;
}

export function WorkspaceLoading({title='Loading your workspace'}:{title?:string}) {
  return <div className="workspace-loading" role="status" aria-label={title}><div className="h-5 w-40 animate-pulse rounded bg-zinc-800"/><div className="grid grid-cols-3 gap-4">{[0,1,2].map(i=><div key={i} className="h-20 animate-pulse rounded-xl bg-zinc-900"/>)}</div><div className="space-y-3 rounded-xl border border-zinc-800 p-5">{[0,1,2,3,4].map(i=><div key={i} className="h-9 animate-pulse rounded bg-zinc-900"/>)}</div><span className="sr-only">{title}</span></div>;
}

export function SourceLink({href,children}:{href:string;children:ReactNode}) {return <Link className="inline-flex items-center gap-1 text-sm text-emerald-300 hover:text-emerald-200" href={href}>{children}<ArrowUpRight size={13}/></Link>;}

export function DetailDrawer({title,children,onClose}:{title:string;children:ReactNode;onClose:()=>void}) {
  const ref=useRef<HTMLDialogElement>(null);
  useEffect(()=>{const dialog=ref.current;if(!dialog)return;const trigger=document.activeElement instanceof HTMLElement?document.activeElement:null;const overflow=document.body.style.overflow;dialog.showModal();document.body.style.overflow='hidden';return()=>{dialog.close();document.body.style.overflow=overflow;if(trigger?.isConnected)trigger.focus();};},[]);
  return <dialog ref={ref} className="workspace-drawer" aria-label={title} onCancel={e=>{e.preventDefault();onClose();}} onClick={e=>{if(e.target===e.currentTarget){const r=e.currentTarget.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)onClose();}}}>
    <header><h2>{title}</h2><button aria-label="Close details" onClick={onClose}><X size={18}/></button></header><div className="workspace-drawer-content">{children}</div>
  </dialog>;
}
