import { NextRequest } from 'next/server';
import { backendFetch } from '@/lib/backend';
import { bridgeTarget, mutationActions, sameOrigin, sessionCookie } from '@/lib/transport';

const privateHeaders = { 'Cache-Control': 'private, no-store, max-age=0', 'Pragma': 'no-cache' };
async function relay(response: Response) {
  const headers = new Headers(privateHeaders);
  headers.set('Content-Type', 'application/json; charset=utf-8');
  if (!response.headers.get('content-type')?.includes('application/json')) return Response.json({ok:false,message:'Не удалось связаться с серверным адаптером.'},{status:502,headers});
  for (const cookie of response.headers.getSetCookie()) {
    if (cookie.startsWith('koloda_admin=')) headers.append('Set-Cookie', cookie);
  }
  return new Response(await response.text(), {status:response.status,headers});
}
export async function GET(request: NextRequest) {
  try {
    const query = new URLSearchParams(request.nextUrl.searchParams);
    const view = query.get('view') || 'panel';
    query.delete('view');
    return relay(await backendFetch(bridgeTarget(view, query), request.headers.get('cookie') || ''));
  } catch { return Response.json({ok:false,message:'Не удалось загрузить данные.'},{status:502,headers:privateHeaders}); }
}
export async function POST(request: NextRequest) {
  if (!sameOrigin(request.headers.get('origin'), process.env.PANEL_PUBLIC_ORIGIN || request.url)) return Response.json({ok:false,message:'Недопустимый источник запроса.'},{status:403,headers:privateHeaders});
  if (!sessionCookie(request.headers.get('cookie') || '')) return Response.json({ok:false,message:'Требуется вход через GitHub.'},{status:401,headers:privateHeaders});
  if (Number(request.headers.get('content-length') || 0) > 24 * 1024 * 1024) return Response.json({ok:false,message:'Слишком большой запрос.'},{status:413,headers:privateHeaders});
  try {
    const parser = request.nextUrl.searchParams.get('view') === 'parsers';
    if (parser) {
      const body = await request.text();
      if (body.length > 32 * 1024) return Response.json({ok:false,message:'Слишком большой запрос.'},{status:413,headers:privateHeaders});
      const input = JSON.parse(body);
      if (!['run','section'].includes(input.action)) throw new Error('Invalid action');
      return relay(await backendFetch('/parser-control.php',request.headers.get('cookie') || '',{
        method:'POST',body,headers:{'Content-Type':'application/json','X-CSRF-Token':request.headers.get('x-csrf-token') || ''},
      }));
    }
    const form = await request.formData();
    if (!mutationActions.has(String(form.get('action') || ''))) throw new Error('Invalid action');
    return relay(await backendFetch('/next-data.php',request.headers.get('cookie') || '',{method:'POST',body:form}));
  } catch { return Response.json({ok:false,message:'Не удалось выполнить действие.'},{status:502,headers:privateHeaders}); }
}
