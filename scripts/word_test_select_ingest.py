import json, random, shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.cache_batch import inspect_cache_zip, CacheBatchIngestor

root=Path(__file__).resolve().parents[1]; source=root/'debug_zips'; run=root/'runtime_data'; picked=run/'word_test_input'; db=run/'word_test_db'
if run.exists(): shutil.rmtree(run)
picked.mkdir(parents=True)
items=list(source.rglob('*.zip')); random.Random(20260901).shuffle(items)
chosen=[]; hashes=set(); keys=set()
for p in items:
 r=inspect_cache_zip(p,root=source)
 if r.recoverable and r.zip_hash not in hashes and r.source_key not in keys:
  chosen.append(r); hashes.add(r.zip_hash); keys.add(r.source_key); shutil.copy2(p,picked/r.zip_path)
  if len(chosen)==10: break
if len(chosen)!=10: raise RuntimeError(f'only {len(chosen)} usable unique archives')
ing=CacheBatchIngestor(picked,db)
try: report=ing.ingest(limit=10,batch_size=32)
finally: ing.close()
(run/'selection_and_ingest.json').write_text(json.dumps({'seed':20260901,'selected':[{'zip':x.zip_path,'source':x.source_key,'extension':x.origin_extension,'sha256':x.zip_hash} for x in chosen],'ingest':report},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
