"""当直表のデータ・検証・最適化。UIに依存しない。"""
from __future__ import annotations
import calendar
import copy
import datetime as dt
import hashlib
import io
import json
import re
import uuid
from collections import Counter

import jpholiday
import pandas as pd
from ortools.sat.python import cp_model

NIGHT = ['A宿直', 'B宿直', '外来宿直']
DAY = ['A日直', 'B日直', '外来日直']
SLOTS = NIGHT + DAY
WEEK = '月火水木金土日'
ALIASES = {'宿直A':'A宿直','宿直B':'B宿直','A当直':'A宿直','B当直':'B宿直','日直A':'A日直','日直B':'B日直'}


def new_staff(name=''):
    return dict(id=uuid.uuid4().hex[:12], name=name, group='', color='#DCEAF5', duty='', gap=5,
                target=3, maximum=5, holiday_max=2, **{s:2 for s in SLOTS})


def new_month():
    return dict(holidays=[], overrides=[], ng=[], wishes=[], fixed=[], history=[], exceptions=[],
                schedule=[], generated_for=None, status='', note='', seed=42)


def new_project():
    return dict(schema=1, staff=[], months={})


def dates(key):
    y,m=map(int,key.split('-'))
    return [dt.date(y,m,d).isoformat() for d in range(1,calendar.monthrange(y,m)[1]+1)]


def holiday(date, month):
    d=dt.date.fromisoformat(date)
    return d.weekday()>=5 or jpholiday.is_holiday(d) or date in month['holidays']


def requirements(key, month):
    out={(d,s):1 for d in dates(key) for s in (SLOTS if holiday(d,month) else NIGHT)}
    for r in month['overrides']:
        out[(r['date'],r['slot'])]=int(r['count'])
    return out


def exception(month, doctor, date, rule, other=''):
    for e in month['exceptions']:
        if e['doctor']!=doctor or e['rule']!=rule: continue
        if rule=='間隔':
            if {e['date'],e.get('other','')}=={date,other}: return True
        elif e['date']==date: return True
    return False


def unavailable(staff, date, slot, month):
    reasons=[]
    if not exception(month,staff['id'],date,'NG'):
        for r in month['ng']:
            if r['doctor']==staff['id'] and r['date']==date:
                if r['kind']=='全NG' or r['kind']=='宿NG' and slot in NIGHT or r['kind']=='日NG' and slot in DAY:
                    reasons.append(r['kind'])
    d=dt.date.fromisoformat(date)
    nxt=d+dt.timedelta(days=1)
    if slot in NIGHT and WEEK[nxt.weekday()] in staff['duty'] and not holiday(nxt.isoformat(),month):
        if not exception(month,staff['id'],date,'曜日'):
            reasons.append('翌日PM duty')
    return reasons


def signature(project,key):
    m=copy.deepcopy(project['months'][key])
    for k in ['schedule','generated_for','status','note']: m.pop(k,None)
    raw=json.dumps([project['staff'],key,m],ensure_ascii=False,sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def dump(project):
    return json.dumps(project,ensure_ascii=False,indent=2).encode('utf-8')


def load(data):
    p=json.loads(data)
    if not isinstance(p,dict) or p.get('schema')!=1 or not isinstance(p.get('staff'),list) or not isinstance(p.get('months'),dict):
        raise ValueError('このアプリの全体保存JSONではありません。')
    required=set(new_staff())
    if any(not required<=set(s) for s in p['staff']): raise ValueError('医師データの必須項目が不足しています。')
    for key,m in p['months'].items():
        dates(key)
        if not set(new_month())<=set(m): raise ValueError(f'{key} の保存項目が不足しています。')
        errors=input_errors(p,key)
        if not p['staff'] and not any(m[f] for f in ['ng','wishes','fixed','history','exceptions','schedule']):
            errors=[e for e in errors if e!='医師を登録してください。']
        if errors: raise ValueError(f'{key}: '+ ' / '.join(errors[:5]))
        ids={s['id'] for s in p['staff']}
        req=requirements(key,m)
        for r in m['schedule']:
            if r['doctor'] not in ids or (r['date'],r['slot']) not in req:
                raise ValueError('保存されたシフトに不明な医師・日付・枠があります。')
    return p


def input_errors(p,key):
    errors=[]
    staff=p['staff']; m=p['months'][key]; valid_dates=set(dates(key)); ids={s['id'] for s in staff}
    names=[s['name'].strip() for s in staff]
    if not staff: errors.append('医師を登録してください。')
    if any(not n for n in names): errors.append('医師名が空欄です。')
    if len(set(names))!=len(names) or len(ids)!=len(staff): errors.append('医師名またはIDが重複しています。')
    for s in staff:
        for f in ['gap','target','maximum','holiday_max']+SLOTS:
            v=s[f]
            if isinstance(v,bool) or not isinstance(v,(int,float)) or int(v)!=v or not 0<=v<=366:
                errors.append(f"{s['name']}: {f} は0〜366の整数にしてください。")
        if s['target']>s['maximum']: errors.append(f"{s['name']}: 目標回数が月間上限を超えています。")
        if any(c not in WEEK+',、 /' for c in s['duty']): errors.append(f"{s['name']}: duty曜日は「木,金」の形式にしてください。")
    for d in m['holidays']:
        try: dt.date.fromisoformat(d)
        except (ValueError,TypeError): errors.append('特別休日の日付が不正です。')
    seen=set()
    for r in m['overrides']:
        pair=(r['date'],r['slot'])
        if pair in seen: errors.append('必要人数に同じ日付・枠が重複しています。')
        seen.add(pair)
        if r['date'] not in valid_dates or r['slot'] not in SLOTS: errors.append('必要人数の日付・枠が不正です。')
        if not isinstance(r['count'],(int,float)) or int(r['count'])!=r['count'] or not 0<=r['count']<=20:
            errors.append('必要人数は0〜20の整数にしてください。')
    for field in ['ng','wishes','fixed','history','exceptions']:
        for r in m[field]:
            if r['doctor'] not in ids: errors.append(f'{field}: 未登録の医師があります。')
            try: dt.date.fromisoformat(r['date'])
            except (ValueError,TypeError): errors.append(f'{field}: 日付が不正です。'); continue
            if field!='history' and r['date'] not in valid_dates: errors.append(f'{field}: 対象月外の日付があります。')
            if field=='history' and r['date'] in valid_dates: errors.append('月外勤務に対象月の日付があります。')
            if field in ['fixed','wishes'] and r['slot'] not in SLOTS+(['指定なし'] if field=='wishes' else []): errors.append('不明な勤務枠です。')
            if field=='ng' and r['kind'] not in ['全NG','日NG','宿NG']: errors.append('不明なNG区分です。')
            if field=='exceptions':
                if r['rule'] not in ['NG','曜日','間隔','同日複数']: errors.append('不明な例外区分です。')
                if r['rule']=='間隔':
                    try:
                        dt.date.fromisoformat(r['other'])
                        if r['other']==r['date']: errors.append('間隔の例外には異なる2日を指定してください。')
                    except (ValueError,TypeError,KeyError): errors.append('間隔の例外には相手の日付が必要です。')
    return list(dict.fromkeys(errors))


def validate(p,key,schedule,complete=True):
    """最適化とは独立した出力検証。固定、月外勤務、例外も照合する。"""
    issues=[]; m=p['months'][key]; req=requirements(key,m); staff={s['id']:s for s in p['staff']}
    count=Counter(); by_doc={s:[] for s in staff}; seen=set()
    def add(level,text): issues.append({'区分':level,'内容':text})
    for r in schedule:
        token=(r['date'],r['slot'],r['doctor'])
        if token in seen: add('違反','同じ勤務が重複しています。'); continue
        seen.add(token)
        pair=token[:2]
        if pair not in req or r['doctor'] not in staff: add('違反','対象外の勤務・医師があります。'); continue
        count[pair]+=1; by_doc[r['doctor']].append(r)
        for reason in unavailable(staff[r['doctor']],r['date'],r['slot'],m):
            add('違反',f"{r['date']} {r['slot']} {staff[r['doctor']]['name']}: {reason}")
    for pair,n in req.items():
        if count[pair]>n: add('違反',f'{pair[0]} {pair[1]}: 必要{n}名に対し{count[pair]}名')
        if complete and count[pair]<n: add('不足',f'{pair[0]} {pair[1]}: {n-count[pair]}名不足')
    for r in m['fixed']:
        if (r['date'],r['slot'],r['doctor']) not in seen: add('違反','確定勤務が未反映: '+r['date']+' '+r['slot'])
    for doc,rows in by_doc.items():
        s=staff[doc]; name=s['name']; c=Counter(r['slot'] for r in rows)
        if len(rows)>s['maximum']: add('違反',f'{name}: 月間上限超過')
        if complete and len(rows)<s['target']: add('目標未達',f"{name}: 目標{s['target']}回に対し{len(rows)}回")
        if sum(holiday(r['date'],m) for r in rows)>s['holiday_max']: add('違反',f'{name}: 休日上限超過')
        for slot,n in c.items():
            if n>s[slot]: add('違反',f'{name}: {slot}上限超過')
        dc=Counter(r['date'] for r in rows)
        for d,n in dc.items():
            if n>1 and not exception(m,doc,d,'同日複数'): add('違反',f'{name}: {d} 同日複数勤務')
        current=set(dc); outside={r['date'] for r in m['history'] if r['doctor']==doc}
        all_days=sorted(current|outside)
        for i,a in enumerate(all_days):
            for b in all_days[i+1:]:
                gap=(dt.date.fromisoformat(b)-dt.date.fromisoformat(a)).days-1
                if gap>=s['gap']: break
                if (a in current or b in current) and not exception(m,doc,a,'間隔',b):
                    add('違反',f"{name}: {a}〜{b} は中{gap}日（設定{s['gap']}日）")
    for e in m['exceptions']:
        add('例外',f"{staff.get(e['doctor'],{}).get('name','不明')}: {e['date']} {e['rule']} {e.get('other','')} / {e.get('reason','')}")
    return issues


def solve(p,key,seconds=30):
    errors=input_errors(p,key)
    if errors: return {'ok':False,'message':' / '.join(errors)}
    m=p['months'][key]; req=requirements(key,m); staff=p['staff']; ds=dates(key)
    fixed={(r['date'],r['slot'],r['doctor']) for r in m['fixed']}
    if any((d,s) not in req for d,s,_ in fixed): return {'ok':False,'message':'確定勤務の枠がありません。必要人数設定でその枠を追加してください。'}
    fixed_rows=[dict(date=d,slot=s,doctor=doc) for d,s,doc in fixed]
    fixed_errors=[i['内容'] for i in validate(p,key,fixed_rows,False) if i['区分']=='違反']
    if fixed_errors: return {'ok':False,'message':'確定勤務の条件が衝突しています。\n'+'\n'.join(fixed_errors)}
    model=cp_model.CpModel(); x={}; work={}; missing={}; soft=[]
    for d,s in req:
        for doc in staff: x[d,s,doc['id']]=model.NewBoolVar(f"x_{d}_{s}_{doc['id']}")
        missing[d,s]=model.NewIntVar(0,req[d,s],f'missing_{d}_{s}')
        model.Add(sum(x[d,s,doc['id']] for doc in staff)+missing[d,s]==req[d,s])
    for doc in staff:
        id=doc['id']; allx=[]; hx=[]
        for d in ds:
            daily=[x[d,s,id] for dd,s in req if dd==d]
            work[d,id]=model.NewBoolVar(f'w_{d}_{id}')
            model.AddMaxEquality(work[d,id],daily)
            if not exception(m,id,d,'同日複数'): model.Add(sum(daily)<=1)
            for dd,s in req:
                if dd!=d: continue
                v=x[d,s,id]; allx.append(v)
                if holiday(d,m): hx.append(v)
                if unavailable(doc,d,s,m): model.Add(v==0)
                if (d,s,id) in fixed: model.Add(v==1)
        model.Add(sum(allx)<=doc['maximum']); model.Add(sum(hx)<=doc['holiday_max'])
        for s in SLOTS: model.Add(sum(v for (d,ss,i),v in x.items() if ss==s and i==id)<=doc[s])
        for ai,a in enumerate(ds):
            for b in ds[ai+1:]:
                if (dt.date.fromisoformat(b)-dt.date.fromisoformat(a)).days>doc['gap']: break
                if not exception(m,id,a,'間隔',b): model.Add(work[a,id]+work[b,id]<=1)
            for h in {r['date'] for r in m['history'] if r['doctor']==id}:
                if abs((dt.date.fromisoformat(h)-dt.date.fromisoformat(a)).days)<=doc['gap'] and not exception(m,id,a,'間隔',h): model.Add(work[a,id]==0)
        dev=model.NewIntVar(0,366,'dev_'+id); model.AddAbsEquality(dev,sum(allx)-doc['target']); soft.append(10*dev)
        for r in m['wishes']:
            if r['doctor']!=id: continue
            v=work[r['date'],id] if r['slot']=='指定なし' else x.get((r['date'],r['slot'],id))
            if v is not None: soft.append(-5*v)
    maxhol=model.NewIntVar(0,366,'maxhol')
    for doc in staff: model.Add(sum(v for (d,s,i),v in x.items() if i==doc['id'] and holiday(d,m))<=maxhol)
    soft.append(2*maxhol)
    previous={(r['date'],r['slot'],r['doctor']) for r in m['schedule']}
    changes=sum((1-v if k in previous else v) for k,v in x.items())
    stages=[('不足最小化',sum(missing.values()))]
    if previous: stages.append(('変更最小化',changes))
    stages.append(('目標・希望調整',sum(soft)))
    solver=cp_model.CpSolver(); solver.parameters.max_time_in_seconds=max(0.1,seconds/len(stages))
    solver.parameters.random_seed=int(m['seed']); solver.parameters.num_search_workers=1
    best=None; log=[]
    for label,obj in stages:
        model.Minimize(obj); status=solver.Solve(model)
        if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE):
            if best is None: return {'ok':False,'message':'条件が矛盾しています。' if status==cp_model.INFEASIBLE else '制限時間内に案が見つかりませんでした。計算時間を延ばしてください。'}
            break
        best=[dict(date=d,slot=s,doctor=i) for (d,s,i),v in x.items() if solver.Value(v)]
        value=int(round(solver.ObjectiveValue())); log.append(label+('：最適性確認' if status==cp_model.OPTIMAL else '：暫定解'))
        model.Add(obj<=value)
    issues=validate(p,key,best)
    if any(i['区分']=='違反' for i in issues): return {'ok':False,'message':'検証で違反を検出しました。結果は採用していません。','issues':issues}
    return dict(ok=True,schedule=best,issues=issues,message=' / '.join(log),missing=sum(i['区分']=='不足' for i in issues))


def summary(p,key):
    m=p['months'][key]; result=[]
    for s in p['staff']:
        rows=[r for r in m['schedule'] if r['doctor']==s['id']]
        c=Counter(r['slot'] for r in rows); worked=sorted({r['date'] for r in rows}|{r['date'] for r in m['history'] if r['doctor']==s['id']})
        gaps=[(dt.date.fromisoformat(b)-dt.date.fromisoformat(a)).days-1 for a,b in zip(worked,worked[1:]) if a.startswith(key) or b.startswith(key)]
        wishes=[r for r in m['wishes'] if r['doctor']==s['id']]
        granted=sum(any(a['date']==w['date'] and (w['slot']=='指定なし' or a['slot']==w['slot']) for a in rows) for w in wishes)
        result.append({'医師':s['name'],'所属':s['group'],**{sl:c[sl] for sl in SLOTS},'合計':len(rows),'目標':s['target'],'休日':sum(holiday(r['date'],m) for r in rows),'最小空き日数':min(gaps) if gaps else None,'希望達成':f'{granted}/{len(wishes)}'})
    return pd.DataFrame(result)


def schedule_table(p,key):
    m=p['months'][key]; req=requirements(key,m); names={s['id']:s['name'] for s in p['staff']}; rows=[]
    for d in dates(key):
        row={'日付':d,'曜日':WEEK[dt.date.fromisoformat(d).weekday()],'区分':'休日' if holiday(d,m) else '平日'}
        for s in SLOTS:
            assigned=[names.get(r['doctor'],'不明') for r in m['schedule'] if r['date']==d and r['slot']==s]
            lack=req.get((d,s),0)-len(assigned)
            row[s]='、'.join(assigned+([f'不足{lack}名'] if lack>0 else [])) or '—'
        rows.append(row)
    return pd.DataFrame(rows)


def read_csv(data):
    for enc in ['utf-8-sig','cp932','shift_jis']:
        try: return pd.read_csv(io.BytesIO(data),encoding=enc).fillna('')
        except UnicodeDecodeError: pass
    raise ValueError('UTF-8またはShift-JISのCSVを指定してください。')


def import_staff(data,key):
    df=read_csv(data); m=new_month(); staff=[]; warnings=[]
    for old,new in ALIASES.items():
        if old+'上限' in df and new+'上限' not in df: df=df.rename(columns={old+'上限':new+'上限'})
    if '先生の名前' not in df: raise ValueError('「先生の名前」列がありません。')
    for _,row in df.iterrows():
        if not str(row['先生の名前']).strip(): continue
        s=new_staff(str(row['先生の名前']).strip()); id=s['id']
        for old,new in [('最低空ける日数','gap'),('月間最小回数','target'),('月間最大回数','maximum'),('休日最大回数','holiday_max')]+[(x+'上限',x) for x in SLOTS]:
            if row.get(old,'')!='':
                v=float(row[old])
                if not v.is_integer(): raise ValueError(f'{s["name"]}: {old} は整数にしてください。')
                s[new]=int(v)
        oldd=str(row.get('翌日PM duty',row.get('原則、宿直を外す曜日',row.get('入れない曜日(半角カンマ区切り)',row.get('入れない曜日','')))))
        s['duty']=','.join(WEEK[(i+1)%7] for i,w in enumerate(WEEK) if w in oldd)
        def tokens(col):
            return [t.strip() for t in str(row.get(col,'')).translate(str.maketrans('０１２３４５６７８９，：','0123456789,:')).split(',') if t.strip()]
        for item in tokens('NG日(半角カンマ区切り)'):
            a,*b=item.split(':'); date=f'{key}-{int(a):02d}'; dt.date.fromisoformat(date)
            kind=b[0] if b else '全NG'
            if kind not in ['全NG','宿NG','日NG']: raise ValueError('不明なNG指定: '+item)
            m['ng'].append(dict(doctor=id,date=date,kind=kind))
        priority=float(row.get('希望優先度(数字が大きいほど優先)',1) or 1)
        for item in tokens('希望日(半角カンマ区切り)'):
            a,*b=item.split(':'); date=f'{key}-{int(a):02d}'; dt.date.fromisoformat(date)
            slot=ALIASES.get(b[0],b[0]) if b else '指定なし'
            if slot not in SLOTS+['指定なし']: raise ValueError('不明な希望枠: '+slot)
            if priority>=100 and slot!='指定なし': m['fixed'].append(dict(doctor=id,date=date,slot=slot))
            else:
                m['wishes'].append(dict(doctor=id,date=date,slot=slot))
                if priority>=100: warnings.append(f'{s["name"]} {date}: 枠未指定の確定希望は、希望として読み込みました。確定する枠を選んでください。')
        staff.append(s)
    warnings.append('旧CSVの曜日は実際の翌日PM duty曜日へ変換しました。優先度1〜99は同じ重みの希望になります。確定勤務によるNG・上限・間隔の自動解除は行いません。')
    p=dict(schema=1,staff=staff,months={key:m}); errors=input_errors(p,key)
    if errors: raise ValueError(' / '.join(errors))
    return p,warnings


def excel(p,key):
    from openpyxl.styles import PatternFill,Font,Alignment
    data=io.BytesIO()
    with pd.ExcelWriter(data,engine='openpyxl') as writer:
        schedule_table(p,key).to_excel(writer,sheet_name='勤務表',index=False)
        summary(p,key).to_excel(writer,sheet_name='回数集計',index=False)
        pd.DataFrame(validate(p,key,p['months'][key]['schedule']),columns=['区分','内容']).to_excel(writer,sheet_name='条件チェック',index=False)
        for ws in writer.book:
            ws.freeze_panes='D2' if ws.title=='勤務表' else 'B2'; ws.auto_filter.ref=ws.dimensions
            for cell in ws[1]: cell.fill=PatternFill('solid',fgColor='234E62'); cell.font=Font(color='FFFFFF',bold=True)
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width= min(70,max(12,max(len(str(c.value or '')) for c in col)*1.7))
            for row in ws.iter_rows(min_row=2):
                for cell in row: cell.alignment=Alignment(vertical='center',wrap_text=True)
            ws.sheet_properties.pageSetUpPr.fitToPage=True
            ws.page_setup.orientation='landscape'; ws.page_setup.paperSize=ws.PAPERSIZE_A4; ws.page_setup.fitToWidth=1; ws.page_setup.fitToHeight=0
        ws=writer.book['勤務表']
        colors={s['name']:s['color'].lstrip('#') for s in p['staff'] if re.fullmatch(r'#[0-9A-Fa-f]{6}',s['color'])}
        for row in ws.iter_rows(min_row=2):
            for cell in row[3:]:
                if '不足' in str(cell.value): cell.fill=PatternFill('solid',fgColor='FBDDDD')
                elif cell.value in colors: cell.fill=PatternFill('solid',fgColor=colors[cell.value])
    return data.getvalue()


def import_fixed(data,p,key):
    df=read_csv(data).rename(columns=ALIASES)
    if '日付' not in df: raise ValueError('「日付」列がありません。')
    y,m=map(int,key.split('-')); ids={s['name']:s['id'] for s in p['staff']}; fixed=[]; history=[]; warnings=[]
    for _,row in df.iterrows():
        text=str(row['日付']).strip()
        if not text: continue
        full=re.match(r'^(\d{4})[年/.-](\d{1,2})[月/.-](\d{1,2})',text)
        short=re.match(r'^(\d{1,2})[月/.-](\d{1,2})',text)
        if full: date=dt.date(*map(int,full.groups()))
        elif short:
            mm,dd=map(int,short.groups()); yy=y
            if m==1 and mm==12: yy-=1
            if m==12 and mm==1: yy+=1
            date=dt.date(yy,mm,dd)
        else: raise ValueError('日付を読み取れません：'+text)
        for slot in SLOTS:
            for name in re.split('[、,]',str(row.get(slot,''))):
                name=name.strip()
                if name in ['','-','—']: continue
                if name not in ids: raise ValueError(f'{text}: 未登録の医師「{name}」。氏名は空白も含めて一致させてください。')
                if date.strftime('%Y-%m')==key: fixed.append(dict(date=date.isoformat(),slot=slot,doctor=ids[name]))
                else: history.append(dict(date=date.isoformat(),doctor=ids[name]))
    return fixed,history,warnings
