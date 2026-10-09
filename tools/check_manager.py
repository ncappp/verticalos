import os,re,subprocess,time
from pathlib import Path
ROOT=Path.home()/'Downloads/faxclip-telegram-bridge-v14';ADB=str(ROOT/'platform-tools/adb')
LOGS=Path.home()/'Library/Logs/FaxClip'
def run(a,t=30):
    try:r=subprocess.run(a,capture_output=True,text=True,timeout=t);return r.returncode,r.stdout+r.stderr
    except Exception as e:return -1,type(e).__name__
SECRET=re.compile(r'(?i)(token|secret|authorization|bearer|init_?data|hash|code)([\"\'=:\s]+)[^\s\"\',}]+')
def clean(line,serials):
    line=SECRET.sub(lambda m:m.group(1)+m.group(2)+'***',line)
    line=re.sub(r'\b[A-Za-z0-9_\-]{32,}\b','***',line)
    for s in serials:line=line.replace(s,'<телефон>')
    return line.rstrip()[:220]
print('== Служба менеджера')
c,o=run(['launchctl','print','gui/%d/com.faxclip.devices'%os.getuid()])
pid=re.search(r'(?m)^\s*pid\s*=\s*(\d+)',o);st=re.search(r'(?m)^\s*state\s*=\s*(\S+)',o);ex=re.search(r'(?m)last exit code\s*=\s*(.+)$',o)
print('найдена' if c==0 else 'НЕ НАЙДЕНА','| pid',pid.group(1) if pid else 'нет','| state',st.group(1) if st else '?','| last exit',ex.group(1).strip() if ex else '?')
stop=list((ROOT/'.faxclip-cloud').glob('*stop*'))
print('метка остановки менеджера:', 'ЕСТЬ '+', '.join(p.name for p in stop) if stop else 'нет')
print('== Телефон')
c,o=run([ADB,'devices']);serials=[l.split()[0] for l in o.splitlines()[1:] if l.strip()]
print('ADB:',', '.join(l.split()[1] for l in o.splitlines()[1:] if len(l.split())>1) or 'телефонов нет')
cfg=list((ROOT/'.faxclip-cloud/devices').glob('*.json'));print('сохранённых подключений:',len(cfg))
ok=[s for s in serials if 'device' in o]
if len(serials)==1:
    c,b=run([ADB,'-s',serials[0],'shell','am','broadcast','-a','com.faxclip.access.CONTROL','-p','com.faxclip.access','--es','cmd','status'])
    print('помощник:',next((t for t in ('SERVICE_CONNECTED_V14','SERVICE_NOT_CONNECTED') if t in b),'NO_HELPER_RESPONSE'))
    c,w=run([ADB,'-s',serials[0],'shell','dumpsys','window','policy'])
    print('экран заблокирован:', 'да' if re.search(r'(?:isStatusBarKeyguard|mShowingLockscreen|showing)=true',w) else 'нет')
work=ROOT/'.faxclip-cloud/work'
if work.exists():
    files=sorted(work.rglob('*'),key=lambda p:p.stat().st_mtime if p.is_file() else 0)[-3:]
    for p in files:
        if p.is_file():print('последний файл работы:',p.name[:60],time.strftime('%H:%M:%S',time.localtime(p.stat().st_mtime)))
for name in ('manager.log','manager-error.log'):
    p=LOGS/name;print('==',name)
    if not p.exists():print('нет файла');continue
    print('изменён',time.strftime('%d.%m %H:%M:%S',time.localtime(p.stat().st_mtime)),'| сейчас',time.strftime('%H:%M:%S'))
    for l in p.read_text(errors='replace').splitlines()[-25:]:print(clean(l,serials))
