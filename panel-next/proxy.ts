import { NextRequest, NextResponse } from 'next/server';
import { sessionCookie } from './lib/transport';
export function proxy(request:NextRequest) {
  if(sessionCookie(request.headers.get('cookie')||''))return NextResponse.next();
  const origin=process.env.PANEL_PUBLIC_ORIGIN||request.nextUrl.origin;
  const url=new URL('/auth/github',origin);
  if(request.nextUrl.pathname!=='/'||request.nextUrl.search)url.searchParams.set('return_to',request.nextUrl.pathname+request.nextUrl.search);
  const response=NextResponse.redirect(url);
  response.headers.set('Cache-Control','private, no-store, max-age=0');
  return response;
}
export const config={matcher:['/']};
