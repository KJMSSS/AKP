"""STAGE 1.2 — 일괄 구조 검증 + fix dry-run."""
import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from src.common.hwpx_validator import fix_hwpx, validate_hwpx

prod = Path("samples/11b_production")
targets = sorted(prod.glob("*_v5.hwpx"))
targets.append(prod / "2025_1_1_b_공수1_광주고_v17.hwpx")

# 광주고 v17 sha 확인
v17 = prod / "2025_1_1_b_공수1_광주고_v17.hwpx"
full_sha = hashlib.sha1(v17.read_bytes()).hexdigest()
print(f"광주고 v17 sha1: {full_sha}")
print(f"  앞 12자: {full_sha[:12]}  (이전 baseline 기록: e1c77bae1921)")
print()

print(f"{'='*60}")
print(f"STAGE 1.2 — 구조 검증 ({len(targets)}개)")
print(f"{'='*60}")

pass_list, fail_list = [], []
for hwpx in targets:
    sha12 = hashlib.sha1(hwpx.read_bytes()).hexdigest()[:12]
    errs = validate_hwpx(str(hwpx))
    school = hwpx.name.replace("2025_1_1_b_공수1_", "").replace(".hwpx", "")
    if errs:
        fail_list.append((school, sha12, errs))
        print(f"FAIL  {school:<32} sha={sha12}")
        for e in errs[:3]:
            print(f"      - {e}")
        if len(errs) > 3:
            print(f"      ... +{len(errs)-3}건")
    else:
        pass_list.append((school, sha12))
        print(f"PASS  {school:<32} sha={sha12}")

print()
print(f"결과: PASS {len(pass_list)}개 / FAIL {len(fail_list)}개")

print()
print(f"{'='*60}")
print("fix dry-run (임시 복사본 대상, 원본 변경 없음)")
print(f"{'='*60}")

total_escape = total_addr = total_zorder = 0
changed_schools = []
for hwpx in targets:
    school = hwpx.name.replace("2025_1_1_b_공수1_", "").replace(".hwpx", "")
    with tempfile.NamedTemporaryFile(suffix=".hwpx", delete=False) as tmp:
        tmp_path = tmp.name
    shutil.copy2(str(hwpx), tmp_path)
    try:
        result = fix_hwpx(tmp_path)
        if any(result.values()):
            changed_schools.append((school, result))
            print(
                f"  변경 {school}: escape={result['escape']} "
                f"celladdr={result['celladdr']} zorder={result['zorder']}"
            )
        total_escape += result["escape"]
        total_addr += result["celladdr"]
        total_zorder += result["zorder"]
    finally:
        os.unlink(tmp_path)

if not changed_schools:
    print("  전 학교: 수정 대상 없음 (모두 정상)")
print(f"합계: escape={total_escape} celladdr={total_addr} zorder={total_zorder}")
print()
if not changed_schools:
    print("→ --fix 활성화 시 원본 변경 없음. 현재 AKP 출력물 구조 이상 없음.")
else:
    print(f"→ {len(changed_schools)}개 학교에서 수정 가능 이슈 발견. 학원장 승인 후 fix 활성화 고려.")
