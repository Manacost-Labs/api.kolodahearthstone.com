import type { Row } from '../types';
declare const model: {
  flattenSources(snapshot:Row):Row[];
  sourcePresentation(source:Row):{key:string;filter:string;label:string;tone:string;description:string;attemptFailed:boolean};
  buildSummary(snapshot:Row):{total:number;fresh:number;fallback:number;unavailable:number;disabled:number;issues:number;nextRunAt:string|null;activeRun:Row|null};
  statusMeta(value:unknown):{key:string;label:string;tone:string};
  runProgress(run:Row):{total:number;done:number;percent:number};
};
export = model;
