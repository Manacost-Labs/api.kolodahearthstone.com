'use client';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { XIcon } from '@phosphor-icons/react/dist/ssr/X';
let openDialogs=0;
let previousOverflow='';
export function Modal({title,children,onClose}:{title:string;children:ReactNode;onClose:()=>void}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const closeTimer=useRef<ReturnType<typeof setTimeout>|null>(null);
  const [closing,setClosing]=useState(false);
  useEffect(()=>{
    const el=dialog.current; el?.showModal();
    if(openDialogs++===0){previousOverflow=document.documentElement.style.overflow;document.documentElement.style.overflow='hidden';}
    return ()=>{
      if(closeTimer.current)clearTimeout(closeTimer.current);
      el?.close();
      if(--openDialogs===0)document.documentElement.style.overflow=previousOverflow;
    };
  },[]);
  const close=()=>{
    if(closing)return;
    if(window.matchMedia('(prefers-reduced-motion: reduce)').matches){dialog.current?.close();return;}
    setClosing(true);
    closeTimer.current=setTimeout(()=>dialog.current?.close(),180);
  };
  return <dialog ref={dialog} className="modal" data-closing={closing||undefined} aria-label={title} onClose={onClose} onCancel={event=>{event.preventDefault();close();}} onClick={event=>{
    if(event.target!==event.currentTarget)return;
    const r=event.currentTarget.getBoundingClientRect();
    if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom) close();
  }}>
    <header className="modal-head"><h2>{title}</h2><button type="button" className="icon-button" aria-label="Закрыть" onClick={close} autoFocus><XIcon size={20}/></button></header>
    {children}
  </dialog>;
}
