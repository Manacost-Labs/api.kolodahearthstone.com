'use client';
import { useEffect, useRef, useState } from 'react';
export function Artwork({urls}:{urls:string[]}) {
  const [index,setIndex]=useState(0);const image=useRef<HTMLImageElement>(null);
  useEffect(()=>{const img=image.current;if(img?.complete&&!img.naturalWidth)setIndex(value=>value+1);},[index]);
  return <><span className="art-empty">Изображение недоступно</span>{index<urls.length&&<img key={urls[index]} ref={image} src={urls[index]} alt="" loading="lazy" onError={()=>setIndex(value=>value+1)}/>}</>;
}
