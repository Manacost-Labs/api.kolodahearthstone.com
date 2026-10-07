import fs from 'node:fs/promises';
for(const name of ['.next/static','public']) {
 const destination=name==='.next/static'?'.next/standalone/.next/static':'.next/standalone/public';
 await fs.cp(new URL('../'+name,import.meta.url),new URL('../'+destination,import.meta.url),{recursive:true});
}
