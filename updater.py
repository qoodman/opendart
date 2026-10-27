# -*- coding: utf-8 -*-
"""
GitHub Releases에서 새 버전을 확인하고 앱을 교체하는 기능.

- 확인: https://api.github.com/repos/<REPO>/releases/latest (로그인 불필요, 공개 저장소)
- Windows: 실행 중인 .exe 이름을 .old로 바꾸고 새 .exe를 그 자리에 둔 뒤 다시 실행
- Mac: 앱을 끈 뒤 작은 스크립트가 .app 묶음을 새 것으로 바꾸고 다시 엶
- 설치본이 아닌 경우(파이썬으로 직접 실행)는 Releases 페이지를 엽니다.
"""
import os, re, sys, shutil, platform, subprocess, tempfile
from pathlib import Path

import requests

REPO = "qoodman/opendart"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")
FROZEN = getattr(sys, "frozen", False)


class UpdateError(Exception):
    pass


def parse_ver(s):
    nums = [int(x) for x in re.findall(r"\d+", s or "")[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def asset_key():
    if IS_WIN:
        return "windows"
    if IS_MAC:
        return "mac-apple-silicon" if platform.machine() == "arm64" else "mac-intel"
    return None


def pick_asset(assets):
    key = asset_key()
    if not key:
        return None
    for a in assets or []:
        n = a.get("name", "")
        if n.endswith(f"-{key}.exe") or n.endswith(f"-{key}.zip"):
            return a
    return None


def check(current, timeout=10):
    """새 버전이 있으면 정보 dict, 없으면 None. 네트워크 오류는 UpdateError."""
    try:
        r = requests.get(LATEST_API, timeout=timeout,
                         headers={"Accept": "application/vnd.github+json", "User-Agent": "DartSimpleInvest"})
    except requests.exceptions.RequestException as e:
        raise UpdateError(f"업데이트 서버에 연결할 수 없습니다({type(e).__name__}).")
    if r.status_code == 404:
        return None                       # 아직 Release가 없음
    if r.status_code != 200:
        raise UpdateError(f"업데이트 확인 실패 (HTTP {r.status_code})")
    rel = r.json()
    tag = rel.get("tag_name", "")
    if parse_ver(tag) <= parse_ver(current):
        return None
    a = pick_asset(rel.get("assets"))
    return dict(version=tag.lstrip("vV"), notes=(rel.get("body") or "").strip(),
                page=rel.get("html_url") or RELEASES_PAGE,
                asset_url=a and a.get("browser_download_url"), asset_name=a and a.get("name"),
                size=a and a.get("size"))


def can_self_update():
    return FROZEN and (IS_WIN or IS_MAC)


def current_target():
    """교체할 대상: Windows는 .exe 파일, Mac은 .app 폴더"""
    exe = Path(sys.executable).resolve()
    if IS_MAC:
        for p in exe.parents:
            if p.suffix == ".app":
                return p
        raise UpdateError("앱 위치를 찾을 수 없습니다.")
    return exe


def preflight():
    """교체가 가능한 위치인지 미리 확인. 문제가 있으면 UpdateError."""
    t = current_target()
    if IS_MAC and "AppTranslocation" in str(t):
        raise UpdateError("앱을 '응용 프로그램' 폴더로 옮긴 뒤 다시 열어야 자동 업데이트가 됩니다.")
    if not os.access(t.parent, os.W_OK):
        raise UpdateError(f"이 폴더에는 파일을 바꿀 권한이 없습니다:\n{t.parent}\n"
                          "앱을 내 폴더(예: 바탕화면, 응용 프로그램)로 옮기거나 직접 받아 주세요.")
    return t


def download(info, progress=None):
    """새 버전 파일을 받아 경로를 돌려줌. progress(받은바이트, 전체바이트)"""
    if not info.get("asset_url"):
        raise UpdateError("이 컴퓨터에 맞는 파일이 Release에 없습니다.")
    t = preflight()
    # Windows는 같은 폴더(같은 드라이브)에 받아야 이름 바꾸기로 교체 가능
    dest = Path(str(t) + ".new") if IS_WIN else Path(tempfile.mkdtemp(prefix="dart_update_")) / info["asset_name"]
    try:
        with requests.get(info["asset_url"], stream=True, timeout=30,
                          headers={"User-Agent": "DartSimpleInvest"}) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or info.get("size") or 0)
            got = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(256 * 1024):
                    f.write(chunk)
                    got += len(chunk)
                    if progress:
                        progress(got, total)
    except (requests.exceptions.RequestException, OSError) as e:
        try:
            dest.unlink()
        except OSError:
            pass
        raise UpdateError(f"새 버전을 받지 못했습니다({type(e).__name__}).")
    if total and dest.stat().st_size != total:
        dest.unlink()
        raise UpdateError("받은 파일 크기가 맞지 않습니다. 다시 시도해 주세요.")
    return dest


def install_and_restart(downloaded):
    """새 버전으로 바꾸고 새로 실행. 성공하면 호출한 쪽은 바로 종료해야 함."""
    t = current_target()
    if IS_WIN:
        old = Path(str(t) + ".old")
        try:
            if old.exists():
                old.unlink()
        except OSError:
            pass
        try:
            os.replace(t, old)              # 실행 중인 exe도 이름 바꾸기는 됨
        except OSError as e:
            raise UpdateError(f"교체 실패: {e}")
        try:
            os.replace(downloaded, t)
        except OSError as e:
            os.replace(old, t)              # 되돌리기
            raise UpdateError(f"교체 실패: {e}")
        env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
        subprocess.Popen([str(t)], env=env, close_fds=True,
                         creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        return
    if IS_MAC:
        work = Path(downloaded).parent
        unz = work / "unzipped"
        r = subprocess.run(["ditto", "-x", "-k", str(downloaded), str(unz)], capture_output=True)
        if r.returncode != 0:
            raise UpdateError("받은 파일의 압축을 풀지 못했습니다.")
        apps = list(unz.glob("*.app"))
        if not apps:
            raise UpdateError("받은 파일에 앱이 없습니다.")
        script = work / "update.sh"
        script.write_text(f"""#!/bin/sh
while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.5; done
T={_q(t)}
N={_q(apps[0])}
rm -rf "$T.old"
if mv "$T" "$T.old" && mv "$N" "$T"; then
  rm -rf "$T.old"
else
  [ -d "$T" ] || mv "$T.old" "$T"
fi
xattr -dr com.apple.quarantine "$T" 2>/dev/null
open "$T"
rm -rf {_q(work)}
""", encoding="utf-8")
        subprocess.Popen(["/bin/sh", str(script)], start_new_session=True, close_fds=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    raise UpdateError("이 운영체제는 자동 업데이트를 지원하지 않습니다.")


def _q(p):
    return "'" + str(p).replace("'", "'\\''") + "'"


def cleanup_old():
    """지난 업데이트에서 남은 파일 정리 (앱 시작 시 호출)"""
    if not (FROZEN and IS_WIN):
        return
    for suffix in (".old", ".new"):
        try:
            Path(sys.executable + suffix).unlink()
        except OSError:
            pass
