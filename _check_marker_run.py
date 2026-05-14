"""HWPX section0.xml에서 마커 주변 XML 구조 확인."""
import sys, zipfile, re
sys.stdout.reconfigure(encoding='utf-8')

HWPX = r'd:\f1\AKP\samples\eval_set\(광주)_2026_공수1\output_v3.hwpx'
with zipfile.ZipFile(HWPX) as z:
    xml = z.read('Contents/section0.xml').decode('utf-8')

# "본문 손상" 주변 hp:t 노드 컨텍스트
for tag in ['본문 손상', '확인 필요']:
    print(f'\n[검색: "{tag}"]')
    idx = 0
    found = 0
    while True:
        i = xml.find(tag, idx)
        if i < 0:
            break
        found += 1
        # 가장 가까운 <hp:t...> 와 </hp:t> 위치 찾기
        run_start = xml.rfind('<hp:run', 0, i)
        run_end   = xml.find('</hp:run>', i) + len('</hp:run>')
        run_xml   = xml[run_start:run_end]
        t_count   = len(re.findall(r'<hp:t[\s>]', run_xml))
        # 이 marker 텍스트 노드만 단독으로 추출
        t_left  = xml.rfind('<hp:t', 0, i)
        t_right = xml.find('</hp:t>', i)
        node = xml[t_left:t_right + len('</hp:t>')]
        print(f'  #{found} run의 hp:t 개수={t_count}, 이 마커가 들어있는 hp:t:')
        print(f'    {node[:150]}{"..." if len(node) > 150 else ""}')
        idx = i + 1
    if found == 0:
        print('  (없음)')
