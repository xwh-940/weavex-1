import collections, gzip, json
from pathlib import Path
p=Path('output/cache-continuity-20260909/retry-8192')
with gzip.open(p/'snapshot.json.gz','rt',encoding='utf-8') as f: s=json.load(f)
failures=[t for t in s['traces'] if t['kind'] in ('tool.observation','act.observation') and not t['payload']['ok']]
print('FAILURES',json.dumps(failures,ensure_ascii=False))
projection={}
for actor in s['actors']:
    counts=collections.Counter(); receive_counts=collections.Counter(); receives=0
    for msg in actor['dialogue']:
        if msg.get('role')!='tool': continue
        v=json.loads(msg['content'])
        if v.get('source')!='receive_input' or not v.get('ok'): continue
        receives+=1; w=v['value']['local_world']
        for name,key in [('objects','object_id'),('references','reference_id'),('actors','actor_id'),('groundings','grounding_id')]:
            for item in w.get(name,[]): counts[(name,item[key])]+=1;receive_counts[name]+=1
    projection[actor['actor_id']]={'receive_inputs':receives,'delivered':dict(receive_counts),'repeated_material_ids':[list(k) for k,n in counts.items() if n>1]}
print('PROJECTION',json.dumps(projection,ensure_ascii=False))
assert all(not v['repeated_material_ids'] for v in projection.values())
(p/'projection-audit.json').write_text(json.dumps(projection,ensure_ascii=False,indent=2),encoding='utf-8')
print('LAST_DECISIONS',json.dumps([{'actor':t['actor_id'],**t['payload']} for t in s['traces'] if t['kind']=='model.decision' and any(abs(t['seq']-x['seq'])<=5 for x in failures)],ensure_ascii=False))
