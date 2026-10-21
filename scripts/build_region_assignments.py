import json, math, collections
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
features=json.loads((DATA/'sigungu.geojson').read_text(encoding='utf-8'))['features']
pops=json.loads((DATA/'population_source.json').read_text(encoding='utf-8-sig'))
BRANCHES=['서울서부','서울동부','수원','천안','광주','부산','대구']
ALIASES={'서울':'서울특별시','서울시':'서울특별시','서울특별시':'서울특별시','부산':'부산광역시','부산시':'부산광역시','부산광역시':'부산광역시','대구':'대구광역시','대구시':'대구광역시','대구광역시':'대구광역시','인천':'인천광역시','인천시':'인천광역시','인천광역시':'인천광역시','광주':'광주광역시','광주시':'광주광역시','광주광역시':'광주광역시','대전':'대전광역시','대전시':'대전광역시','대전광역시':'대전광역시','울산':'울산광역시','울산시':'울산광역시','울산광역시':'울산광역시','세종':'세종특별자치시','세종시':'세종특별자치시','세종특별자치시':'세종특별자치시','경기':'경기도','경기도':'경기도','강원':'강원특별자치도','강원도':'강원특별자치도','강원특별자치도':'강원특별자치도','충북':'충청북도','충청북도':'충청북도','충남':'충청남도','충청남도':'충청남도','전북':'전북특별자치도','전라북도':'전북특별자치도','전북특별자치도':'전북특별자치도','전남':'전라남도','전라남도':'전라남도','경북':'경상북도','경상북도':'경상북도','경남':'경상남도','경상남도':'경상남도','제주':'제주특별자치도','제주도':'제주특별자치도','제주특별자치도':'제주특별자치도'}
def province(x): return ALIASES.get(x,x)
def polygon_centroid(poly):
    ring=poly[0]; cross_sum=cx=cy=0.0
    for (x1,y1),(x2,y2) in zip(ring,ring[1:]):
        c=x1*y2-x2*y1; cross_sum+=c; cx+=(x1+x2)*c; cy+=(y1+y2)*c
    if abs(cross_sum)<1e-12: return (sum(x for x,y in ring)/len(ring),sum(y for x,y in ring)/len(ring),1)
    return cx/(3*cross_sum),cy/(3*cross_sum),abs(cross_sum/2)
def geometry_center(g):
    polys=g['coordinates'] if g['type']=='MultiPolygon' else [g['coordinates']]
    pcs=[polygon_centroid(p) for p in polys]
    area=sum(p[2] for p in pcs) or len(pcs)
    return sum(p[0]*p[2] for p in pcs)/area,sum(p[1]*p[2] for p in pcs)/area
regions=[]
for f in features:
    pr=f['properties']; sid=province(pr['SIDO_NM']); dist=pr['SIG_KOR_NM'].strip()
    lon,lat=geometry_center(f['geometry'])
    regions.append({'key':f'{sid}|{dist}','province':sid,'district':dist,'lon':lon,'lat':lat,'geometry':f['geometry']})
# Population estimates: use the latest available monthly SGG series; sum Sejong's 읍·면·동;
# where an SGG table reports a parent city while the map subdivides it into general districts,
# split the city total equally across those map districts.
pop_weights={}
sejong=sum(pops.get('세종특별자치시',{}).values())
for r in regions:
    vals=pops.get(r['province'],{})
    d=r['district']
    if d in vals: pop_weights[r['key']]=vals[d]; continue
    if r['province']=='세종특별자치시': pop_weights[r['key']]=sejong; continue
    parents=[name for name in vals if d.startswith(name+' ')]
    if parents:
        parent=max(parents,key=len)
        children=[x for x in regions if x['province']==r['province'] and x['district'].startswith(parent+' ')]
        pop_weights[r['key']]=round(vals[parent]/max(1,len(children)))
    else:
        pop_weights[r['key']]=0
unweighted=[r['key'] for r in regions if not pop_weights.get(r['key'])]
if unweighted: raise SystemExit('Population weights missing: '+', '.join(unweighted))
# Build a shared-boundary graph from matching simplified GeoJSON edges.
edge_owners=collections.defaultdict(set)
def add_edges(idx,geom):
    polys=geom['coordinates'] if geom['type']=='MultiPolygon' else [geom['coordinates']]
    for poly in polys:
        for ring in poly:
            for a,b in zip(ring,ring[1:]):
                aa=(round(a[0],5),round(a[1],5)); bb=(round(b[0],5),round(b[1],5))
                if aa!=bb: edge_owners[tuple(sorted((aa,bb)))].add(idx)
for i,r in enumerate(regions): add_edges(i,r['geometry'])
adj=[set() for _ in regions]
for owners in edge_owners.values():
    if len(owners)>1:
        for i in owners:
            adj[i].update(owners-{i})
# Geographic anchor Voronoi as the primary rule.
anchors=[(126.8,37.55),(127.08,37.55),(127.0,37.26),(127.15,36.82),(126.85,35.16),(129.07,35.18),(128.6,35.87)]
def km(i,b):
    r=regions[i]; x,y=anchors[b]
    return math.hypot((r['lon']-x)*math.cos(math.radians(r['lat']))*111.32,(r['lat']-y)*111.32)
labels=[min(range(len(BRANCHES)),key=lambda b:km(i,b)) for i in range(len(regions))]
def components(branch, skipped=None):
    nodes={i for i,x in enumerate(labels) if x==branch and i!=skipped}
    count=0
    while nodes:
        count+=1; stack=[nodes.pop()]
        while stack:
            for j in adj[stack.pop()] & nodes: nodes.remove(j); stack.append(j)
    return count
loads=[sum(pop_weights[regions[i]['key']] for i,x in enumerate(labels) if x==b) for b in range(7)]
counts=[sum(1 for x in labels if x==b) for b in range(7)]
target=sum(loads)/7
target_count=len(regions)/7
before=loads[:]
before_counts=counts[:]
def objective(branches):
    return sum(((loads[b]/target)-1)**2 + ((counts[b]/target_count)-1)**2 for b in branches)
# Geography is primary: consider only a neighboring territory whose alternative branch is at most 40 km farther.
# Population and region-count balance then select among those local boundary moves.
for _ in range(300):
    best=None
    for i,old in enumerate(labels):
        if not adj[i] or components(old,skipped=i)>components(old): continue
        pop=pop_weights[regions[i]['key']]
        for new in {labels[j] for j in adj[i]}-{old}:
            if km(i,new)-km(i,old)>40: continue
            loads[old]-=pop; loads[new]+=pop; counts[old]-=1; counts[new]+=1
            gain=objective(range(7))
            loads[old]+=pop; loads[new]-=pop; counts[old]+=1; counts[new]-=1
            current=objective(range(7))
            gain=current-gain
            if gain>1e-10 and (best is None or gain>best[0]): best=(gain,i,old,new,pop)
    if best is None: break
    _,i,old,new,pop=best; labels[i]=new; loads[old]-=pop; loads[new]+=pop; counts[old]-=1; counts[new]+=1
assign={r['key']:BRANCHES[labels[i]] for i,r in enumerate(regions)}
(DATA/'region_assignments.json').write_text(json.dumps(assign,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
meta={'source':'행정안전부 주민등록 인구통계(2026-09), GData 지역별 표', 'source_url':'https://jumin.mois.go.kr/','data_month':'2026-09','notes':'2026년 9월 자료 기준. 읍·면·동만 제공되는 세종은 하위지역 인구 합계. 시군구가 일반구로 나뉜 지역은 해당 시 인구를 지도상의 일반구 수로 균등 추정.','population':pop_weights}
(DATA/'region_population.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('regions',len(assign),'missing',len(set(r['key'] for r in regions)-set(assign)))
print('branch district counts',dict(collections.Counter(assign.values())))
print('branch pop',dict(zip(BRANCHES,loads)))
print('before pop',dict(zip(BRANCHES,before)))
print('graph edges',sum(map(len,adj))//2,'isolates',sum(not x for x in adj),'components',dict((BRANCHES[b],components(b)) for b in range(7)))
print('population source entries',sum(len(x) for x in pops.values()))

