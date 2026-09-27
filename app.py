"""実行: streamlit run app.py"""
import calendar
import copy
import datetime as dt
import json
import uuid
import pandas as pd
import streamlit as st
import core as c

st.set_page_config(page_title='当直表ノート',page_icon='📅',layout='wide')
st.markdown('''<style>.block-container{padding-top:2rem;max-width:1500px}h1{color:#234e62}div[data-testid="stMetric"]{background:#f1f6f7;padding:14px;border-radius:12px}section[data-testid="stSidebar"]{background:#eff4f5}</style>''',unsafe_allow_html=True)
if 'project' not in st.session_state: st.session_state.project=c.new_project()
if 'revision' not in st.session_state: st.session_state.revision=0
p=st.session_state.project

def reset(project):
    st.session_state.project=project
    if project['months'] and st.session_state.get('target_month') not in project['months']:
        st.session_state['pending_month']=sorted(project['months'])[-1]
    st.session_state.revision+=1
    st.rerun()

rev=st.session_state.revision
now=dt.date.today()
default=(now.replace(day=28)+dt.timedelta(days=4)).strftime('%Y-%m')
if 'pending_month' in st.session_state:
    st.session_state.target_month=st.session_state.pop('pending_month')
with st.sidebar:
    st.title('当直表ノート')
    st.caption('条件をそろえる。作る。調整する。')
    key=st.text_input('対象月（YYYY-MM）',value=default,key='target_month')
    try: days=c.dates(key)
    except Exception: st.error('例：2026-10 の形式で入力してください。'); st.stop()
    if len(key)!=7: st.error('月は2桁で入力してください。'); st.stop()
    if key not in p['months']: p['months'][key]=c.new_month()
    m=p['months'][key]
    st.caption('保存済みの月：'+(' / '.join(sorted(p['months']))))
    page=st.radio('作業', ['1  医師の基本設定','2  今月の条件','3  シフト作成・調整','4  確認・出力'],label_visibility='collapsed')
    st.divider()
    st.caption('画面での変更は現在のセッションに反映されます。終了前に全体保存をダウンロードしてください。')
    upload=st.file_uploader('全体保存を開く',type=['json'],key=f'load_{rev}')
    if st.button('保存データを復元',disabled=upload is None):
        try: reset(c.load(upload.getvalue()))
        except Exception as e: st.error(str(e))
    save_area=st.container()
    st.caption('復元すると現在の作業全体を置き換えます。必要な作業は先に保存してください。')

st.title(f'{key.replace("-","年")}月の当直表')
st.caption('A・B・外来の日直／宿直  ｜  管理者用')
if 'notice' in st.session_state: st.info(st.session_state.pop('notice'))

names={s['id']:s['name'] for s in p['staff']}
ids={v:k for k,v in names.items()}

def edit_records(field, columns, config=None, caption=None):
    if caption: st.caption(caption)
    rows=copy.deepcopy(m[field])
    for r in rows:
        if 'doctor' in r: r['doctor']=names.get(r['doctor'],r['doctor'])
    df=pd.DataFrame(rows,columns=columns)
    for col in ['date','other']:
        if col in df: df[col]=pd.to_datetime(df[col],errors='coerce').dt.date
    cfg={'doctor':st.column_config.SelectboxColumn('医師',options=list(ids),required=True),
         'date':st.column_config.DateColumn('日付',required=True),
         'slot':st.column_config.SelectboxColumn('枠',options=c.SLOTS,required=True),
         'other':st.column_config.DateColumn('相手の日付（間隔のみ）'),
         'reason':st.column_config.TextColumn('例外の理由',required=True)}
    cfg.update(config or {})
    with st.form(f'form_{field}_{key}_{rev}'):
        edited=st.data_editor(df,num_rows='dynamic',hide_index=True,width='stretch',column_config=cfg,key=f'{field}_{key}_{rev}')
        applied=st.form_submit_button('この表を反映')
    if not applied: return
    result=[]; incomplete=0
    for row in edited.to_dict('records'):
        r={k:('' if pd.isna(v) else v) for k,v in row.items()}
        if not r.get('date') or ('doctor' in columns and r.get('doctor') not in ids) or ('slot' in columns and not r.get('slot')):
            incomplete+=1; continue
        r['date']=r['date'].isoformat() if hasattr(r['date'],'isoformat') else str(r['date'])
        if 'other' in r: r['other']=r['other'].isoformat() if hasattr(r['other'],'isoformat') else str(r['other'])
        if 'doctor' in r: r['doctor']=ids[r['doctor']]
        if 'count' in r:
            if r['count']=='': incomplete+=1; continue
            r['count']=int(r['count'])
        if 'rule' in r and (not r['rule'] or not r.get('reason') or r['rule']=='間隔' and not r['other']):
            incomplete+=1; continue
        result.append(r)
    if incomplete:
        st.error(f'未完成の{incomplete}行があります。必須欄を入力して再度反映してください。表全体の変更はまだ反映していません。')
        return
    candidate=copy.deepcopy(p); candidate['months'][key][field]=result
    errors=c.input_errors(candidate,key)
    if errors:
        st.error(' / '.join(errors)); return
    reset(candidate)

if page.startswith('1'):
    st.subheader('毎月使う基本条件')
    st.caption('表を編集したら「基本条件を反映」を押してください。')
    st.caption('基本条件の変更は保存中の全月に適用されます。変更後の過去の表は条件変更ありと表示します。目標はできるだけ近づける回数、上限は超えない回数です。勤務間隔5日は、10日の次が16日以降という意味です。')
    base=pd.DataFrame(p['staff'],columns=list(c.new_staff()))
    cfg={'id':None,'name':st.column_config.TextColumn('医師名',required=True),'group':st.column_config.TextColumn('所属'),
         'color':st.column_config.TextColumn('色（例 #DCEAF5）'),
         'duty':st.column_config.TextColumn('PM dutyがある曜日',help='木曜PMにdutyがあるなら「木」。前日水曜の宿直を避けます。木曜が休日なら対象外。'),
         'gap':st.column_config.NumberColumn('最低空ける日数',min_value=0,max_value=31,step=1),
         'target':st.column_config.NumberColumn('月間目標',min_value=0,max_value=186,step=1),
         'maximum':st.column_config.NumberColumn('月間上限',min_value=0,max_value=186,step=1),
         'holiday_max':st.column_config.NumberColumn('休日上限',min_value=0,max_value=186,step=1)}
    cfg.update({s:st.column_config.NumberColumn(s+'上限',min_value=0,max_value=62,step=1) for s in c.SLOTS})
    with st.form('staff_form'):
        df=st.data_editor(base,num_rows='dynamic',hide_index=True,width='stretch',column_config=cfg,key=f'staff_{rev}')
        apply=st.form_submit_button('基本条件を反映',type='primary')
    if apply:
        new=[]
        for row in df.to_dict('records'):
            if pd.isna(row.get('name')) or not str(row.get('name','')).strip(): continue
            s=c.new_staff(str(row['name']).strip())
            for k,v in row.items():
                if pd.notna(v): s[k]=v
            for k in ['gap','target','maximum','holiday_max']+c.SLOTS: s[k]=int(s[k])
            new.append(s)
        oldids={s['id'] for s in p['staff']}; newids={s['id'] for s in new}; removed=oldids-newids
        used={r['doctor'] for mm in p['months'].values() for f in ['ng','wishes','fixed','history','exceptions','schedule'] for r in mm[f]}
        if removed & used: st.error('月別データに使用中の医師は削除できません。該当する勤務・条件を先に削除してください。')
        else:
            candidate=copy.deepcopy(p); candidate['staff']=new
            errors=c.input_errors(candidate,key)
            if errors: st.error('\n'.join(errors))
            else: reset(candidate)
    if not p['staff']:
        if st.button('架空の30名で試す'):
            p['staff']=[dict(c.new_staff(f'医師{i+1:02d}'),gap=4,maximum=6,holiday_max=3,target=4) for i in range(30)]
            reset(p)
    with st.expander('以前のアプリの医師条件CSVを取り込む'):
        st.write('医師条件・NG・希望を読み込み、新しい作業として開きます。現在の作業は先に全体保存してください。')
        legacy=st.file_uploader('医師条件CSV',type='csv')
        if st.button('CSVから作業を開始',disabled=legacy is None):
            try:
                imported,warnings=c.import_staff(legacy.getvalue(),key)
                st.session_state.notice='\n'.join(warnings)
                reset(imported)
            except Exception as e: st.error(str(e))

elif page.startswith('2'):
    if not names: st.info('先に「医師の基本設定」で登録してください。')
    else:
        st.caption('表を編集したら、その表の「この表を反映」を押してください。NGカレンダー・休日選択・メモは即時反映です。')
        tab1,tab2,tab3,tab4=st.tabs(['休日・人数','NG・希望','確定・月外勤務','例外設定'])
        with tab1:
            st.subheader('特別休日')
            st.caption('土日祝は自動判定。平日を追加すると日直が設けられ、休日回数に含まれます。')
            options=[d for d in days if dt.date.fromisoformat(d).weekday()<5 and not c.jpholiday.is_holiday(dt.date.fromisoformat(d))]
            selected=st.multiselect('休日として扱う平日',options=options,default=[d for d in m['holidays'] if d in options],key=f'hol_{key}_{rev}')
            m['holidays']=[d for d in m['holidays'] if not d.startswith(key)]+selected
            st.subheader('日付・枠ごとの必要人数')
            edit_records('overrides',['date','slot','count'],{'count':st.column_config.NumberColumn('合計人数',min_value=0,max_value=20,step=1,required=True)},'通常は各枠1名。2名への増員、0名への変更、平日の日直だけ追加もできます。休日区分とは独立しています。')
            req=c.requirements(key,m)
            st.metric('今月の必要勤務数',sum(req.values()))
        with tab2:
            docname=st.selectbox('NG日を入力する医師',list(ids))
            doc=ids[docname]
            st.caption('選択すると現在の作業に反映されます。平日でも日NG・全NGを保存でき、休日設定を変えても失われません。')
            ngmap={r['date']:r['kind'] for r in m['ng'] if r['doctor']==doc}
            y,mo=map(int,key.split('-'))
            for week in calendar.monthcalendar(y,mo):
                cols=st.columns(7)
                for idx,d in enumerate(week):
                    if not d: continue
                    date=f'{key}-{d:02d}'
                    with cols[idx]:
                        choices=['OK','全NG','日NG','宿NG']
                        value=st.selectbox(f'{d}日（{c.WEEK[idx]}）'+('・休' if c.holiday(date,m) else ''),choices,index=choices.index(ngmap.get(date,'OK')),key=f'ng_{key}_{doc}_{d}_{rev}')
                        ngmap[date]=value
            m['ng']=[r for r in m['ng'] if r['doctor']!=doc]+[dict(doctor=doc,date=d,kind=k) for d,k in ngmap.items() if k!='OK']
            a,b=st.columns(2)
            if a.button('この医師を全日NGにする'):
                m['ng']=[r for r in m['ng'] if r['doctor']!=doc]+[dict(doctor=doc,date=d,kind='全NG') for d in days]; reset(p)
            if b.button('この医師のNGをすべて解除'):
                m['ng']=[r for r in m['ng'] if r['doctor']!=doc]; reset(p)
            st.subheader('できれば入りたい勤務')
            edit_records('wishes',['date','slot','doctor'],{'slot':st.column_config.SelectboxColumn('希望枠',options=['指定なし']+c.SLOTS,required=True)},'希望はNG・上限・勤務間隔を守る範囲で採用します。確定勤務は次のタブで指定します。')
        with tab3:
            st.subheader('今月の確定勤務')
            edit_records('fixed',['date','slot','doctor'],caption='ここに指定した担当者は再計算しても変わりません。NGや間隔などの条件は引き続き適用されます。')
            st.subheader('先月末・翌月初の勤務')
            edit_records('history',['date','doctor'],caption='月初・月末の勤務間隔に使います。同じ日の複数枠は1行で構いません。対象月外の日付を指定してください。')
            othermonths=[k for k in p['months'] if k!=key and p['months'][k]['schedule']]
            if othermonths and st.button('保存中の他の月の勤務表から月外勤務を取り込む'):
                records={(r['date'],r['doctor']) for r in m['history'] if r['date'][:7] not in othermonths}
                records.update((r['date'],r['doctor']) for k in othermonths for r in p['months'][k]['schedule'])
                m['history']=[dict(date=d,doctor=i) for d,i in sorted(records)]
                m['holidays']=sorted({d for d in m['holidays'] if d[:7] not in othermonths}|{d for k in p['months'] if k!=key for d in p['months'][k]['holidays']})
                reset(p)
            st.caption('月外勤務は取り込んだ時点の内容です。他月の表を修正した場合は、月外勤務も更新してください。')
            with st.expander('従来の確定勤務CSVを取り込む'):
                uf=st.file_uploader('確定勤務CSV',type='csv')
                if st.button('確定勤務CSVを追加',disabled=uf is None):
                    try:
                        fixed,history,warnings=c.import_fixed(uf.getvalue(),p,key)
                        m['fixed']=list({(r['date'],r['slot'],r['doctor']):r for r in m['fixed']+fixed}.values())
                        m['history']=list({(r['date'],r['doctor']):r for r in m['history']+history}.values())
                        st.session_state.notice='読み込みました。'+' / '.join(warnings); reset(p)
                    except Exception as e: st.error(str(e))
        with tab4:
            st.subheader('必要な勤務だけ例外にする')
            st.write('NG・曜日は指定日だけ、間隔は指定した2日間だけを例外にします。同日複数は指定日に複数枠を許可します。回数上限は基本設定で明示的に変更してください。')
            edit_records('exceptions',['date','doctor','rule','other','reason'],{'rule':st.column_config.SelectboxColumn('例外にする条件',options=['NG','曜日','間隔','同日複数'],required=True)})
        m['note']=st.text_area('今月の引き継ぎメモ',value=m['note'],key=f'note_{key}_{rev}')

elif page.startswith('3'):
    errors=c.input_errors(p,key)
    for e in errors: st.error(e)
    a,b,d=st.columns(3)
    req=c.requirements(key,m) if not errors else {}
    a.metric('必要勤務数',sum(req.values()))
    b.metric('月間上限の合計',sum(s['maximum'] for s in p['staff']))
    d.metric('確定勤務',len(m['fixed']))
    st.caption('不足数を優先して減らし、既存の案があれば変更を最小化した後、目標回数・希望・休日の偏りを調整します。')
    seconds=st.select_slider('計算時間の上限（秒）',options=[10,30,60,120],value=30)
    if st.button('シフト案を作成／固定を残して再計算',type='primary',disabled=bool(errors)):
        with st.spinner('条件を確認し、シフト案を計算しています…'):
            result=c.solve(p,key,seconds)
        if result['ok']:
            st.session_state[f'undo_{key}']=copy.deepcopy(m)
            m['schedule']=result['schedule']; m['generated_for']=c.signature(p,key); m['status']=result['message']
            st.session_state.notice='シフト案を更新しました。不足枠・目標未達・例外は確認画面に表示します。'; reset(p)
        else: st.error(result['message'])
    if m['generated_for'] is not None:
        if m['generated_for']!=c.signature(p,key): st.warning('作成時から条件が変わっています。条件チェックを確認するか再計算してください。')
        st.dataframe(c.schedule_table(p,key),hide_index=True,width='stretch',height=600)
        st.subheader('担当者を修正・固定する')
        st.caption('表の担当者を選び直して「修正を反映」を押します。固定にチェックした勤務だけ、再計算後も必ず残ります。')
        fixed={(r['date'],r['slot'],r['doctor']) for r in m['fixed']}
        editable=[dict(date=r['date'],slot=r['slot'],doctor=names[r['doctor']],locked=(r['date'],r['slot'],r['doctor']) in fixed) for r in m['schedule']]
        with st.form('result_edit'):
            ed=st.data_editor(pd.DataFrame(editable,columns=['date','slot','doctor','locked']),hide_index=True,num_rows='dynamic',width='stretch',
                column_config={'date':st.column_config.SelectboxColumn('日付',options=days,required=True),
                'slot':st.column_config.SelectboxColumn('枠',options=c.SLOTS,required=True),
                'doctor':st.column_config.SelectboxColumn('担当医師',options=list(ids),required=True),
                'locked':st.column_config.CheckboxColumn('固定',default=False)},key=f'result_{key}_{rev}')
            submit=st.form_submit_button('修正を反映')
        if submit:
            new=[]; locks=[]; invalid=False
            for r in ed.to_dict('records'):
                if r.get('doctor') not in ids or r.get('date') not in days or r.get('slot') not in c.SLOTS: invalid=True; continue
                item=dict(date=r['date'],slot=r['slot'],doctor=ids[r['doctor']]); new.append(item)
                if r.get('locked') is True: locks.append(item.copy())
            candidate=copy.deepcopy(p); cm=candidate['months'][key]; cm['schedule']=new; cm['fixed']=locks
            issues=c.validate(candidate,key,new)
            violations=[i['内容'] for i in issues if i['区分']=='違反']
            if invalid or violations: st.error('変更を反映できません。'+('未完成の行があります。' if invalid else '')+'\n'+'\n'.join(violations))
            else:
                st.session_state[f'undo_{key}']=copy.deepcopy(m); cm['generated_for']=c.signature(candidate,key); cm['status']='手動修正済み'; reset(candidate)
        a,b=st.columns(2)
        if a.button('現在の勤務をすべて固定'):
            st.session_state[f'undo_{key}']=copy.deepcopy(m); m['fixed']=copy.deepcopy(m['schedule']); reset(p)
        if b.button('直前の修正・計算を取り消す',disabled=f'undo_{key}' not in st.session_state):
            p['months'][key]=st.session_state.pop(f'undo_{key}'); reset(p)
        with st.expander('枠ごとの候補を調べる'):
            date=st.selectbox('候補を調べる日',days)
            slot=st.selectbox('候補を調べる枠',c.SLOTS)
            replace=st.selectbox('交代する担当者', ['空き枠へ追加']+[names[r['doctor']] for r in m['schedule'] if r['date']==date and r['slot']==slot])
            if st.button('候補と条件の衝突を確認'):
                rows=[]
                for doc in p['staff']:
                    candidate=[r.copy() for r in m['schedule'] if not (r['date']==date and r['slot']==slot and names[r['doctor']]==replace)]
                    candidate.append(dict(date=date,slot=slot,doctor=doc['id']))
                    problems=[i['内容'] for i in c.validate(p,key,candidate,False) if i['区分']=='違反']
                    rows.append({'医師':doc['name'],'判定':'変更可能' if not problems else '要調整','理由':' / '.join(problems)})
                st.dataframe(pd.DataFrame(rows),hide_index=True,width='stretch')
            st.caption('他の勤務を維持した場合の判定です。固定された担当を交代するには、先に固定を解除します。')

else:
    st.subheader('勤務表と条件チェック')
    if m['generated_for'] is None: st.info('シフト案を作成すると、集計と出力が使えます。')
    else:
        errors=c.input_errors(p,key)
        if errors:
            st.error('入力条件を修正してください：'+' / '.join(errors))
        else:
            if m['generated_for']!=c.signature(p,key): st.warning('条件変更後の確認用出力です。必要に応じて再計算してください。')
            st.caption(m['status'])
            issues=c.validate(p,key,m['schedule'])
            counts={kind:sum(i['区分']==kind for i in issues) for kind in ['違反','不足','目標未達','例外']}
            cols=st.columns(4)
            for col,(kind,n) in zip(cols,counts.items()): col.metric(kind,f'{n}件')
            if counts['違反']: st.error('条件違反があります。条件または勤務を修正してください。')
            elif counts['不足']: st.warning('不足枠を含む案です。')
            else: st.success('必要人数を満たし、指定条件の違反はありません。')
            if issues: st.dataframe(pd.DataFrame(issues),hide_index=True,width='stretch')
            st.dataframe(c.summary(p,key),hide_index=True,width='stretch')
            st.dataframe(c.schedule_table(p,key),hide_index=True,width='stretch')
            a,b=st.columns(2)
            a.download_button('Excelをダウンロード（勤務表・集計・確認）',c.excel(p,key),file_name=f'shift_{key}.xlsx',mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            b.download_button('勤務表CSVをダウンロード',c.schedule_table(p,key).to_csv(index=False).encode('utf-8-sig'),file_name=f'shift_{key}.csv',mime='text/csv')
            st.caption('不足や例外がある場合も確認用として出力できます。配布前に確認してください。複数医師のセルには単一の所属色を付けません。')

with save_area:
    st.download_button('全体保存をダウンロード',c.dump(p),file_name=f'shift_project_{key}.json',mime='application/json',width='stretch')
