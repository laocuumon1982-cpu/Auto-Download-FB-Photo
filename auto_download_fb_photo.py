# -*- coding: utf-8 -*-
"""
Auto Download - FB Photo v3.1
- Tab "Link": dán link bài viết / album / ảnh -> tải toàn bộ ảnh.
- Tab "Album của trang": dán link trang/hồ sơ -> lấy danh sách album -> tick chọn -> tải nguyên album.
- Chế độ chạy ẩn: khi tải không hiện cửa sổ trình duyệt (chỉ hiện khi đăng nhập).
- Nút Cập nhật: kiểm tra GitHub Releases, tải bản mới và tự thay thế.
"""
import os
import re
import sys
import time
import queue
import threading
import subprocess
import json
import shutil
import zipfile
import tempfile
import urllib.request
from urllib.parse import urlparse, parse_qs

APP_NAME = "Auto Download - FB Photo"
APP_VERSION = "3.1"
GITHUB_REPO = "laocuumon1982-cpu/Auto-Download-FB-Photo"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
EXE_ASSET = "Auto-Download-FB-Photo.exe"

# ---------------------------------------------------------------- đường dẫn
FROZEN = getattr(sys, "frozen", False)
RES_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
if FROZEN and sys.platform == "darwin":
    DATA_DIR = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "AutoDownloadFBPhoto")
elif FROZEN:
    DATA_DIR = os.path.dirname(os.path.abspath(sys.executable))  # portable: dữ liệu nằm cạnh file exe
else:
    DATA_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
os.makedirs(DATA_DIR, exist_ok=True)

PROFILE_DIR = os.path.join(DATA_DIR, "fb_profile")
LOGIN_MARK = os.path.join(PROFILE_DIR, ".logged_in")
BROWSERS_DIR = os.path.join(DATA_DIR, "browsers")
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", BROWSERS_DIR)
DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "Downloads", "Facebook_Photos")
ICON_PNG = os.path.join(RES_DIR, "assets", "icon_256.png")
ICON_ICO = os.path.join(RES_DIR, "assets", "icon.ico")

import tkinter as tk
from tkinter import filedialog, messagebox

try:
    import customtkinter as ctk
except ImportError:
    ctk = None

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

NEXT_LABELS = ["Next photo", "Ảnh tiếp theo"]
MAX_RECOVER = 5

if sys.platform == "darwin":
    REAL_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
else:
    REAL_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

LARGEST_IMG_JS = r"""
() => {
  let best = null, area = 0;
  for (const img of document.images) {
    const s = img.currentSrc || img.src;
    if (!s || !/fbcdn|scontent/.test(s)) continue;
    const r = img.getBoundingClientRect();
    if (r.width < 200 || r.height < 150) continue;
    if (r.bottom < 0 || r.top > window.innerHeight) continue;
    const a = r.width * r.height;
    if (a > area) { area = a; best = s; }
  }
  return best;
}
"""

PHOTO_LINKS_JS = r"""
() => {
  const root = document.querySelector('[role="dialog"]')
            || document.querySelector('[role="main"]')
            || document;
  const out = [];
  for (const a of root.querySelectorAll('a[href]')) {
    const h = a.href;
    if (/\/photo(\.php)?\/?\?/.test(h) || /\/photos\/[^?#]+\/\d+/.test(h)) out.push(h);
  }
  return out;
}
"""

# Link ảnh trong lưới ảnh (album / tab Ảnh của trang)
GRID_LINKS_JS = r"""
() => {
  const root = document.querySelector('[role="main"]') || document;
  const out = [];
  for (const a of root.querySelectorAll('a[href]')) {
    const h = a.href;
    if (/\/photo(\.php)?\/?\?.*fbid=\d+/.test(h) || /\/photos\/[^?#]+\/\d+/.test(h)) out.push(h);
  }
  return out;
}
"""

# Danh sách album trên trang photos_albums
ALBUMS_JS = r"""
() => {
  const root = document.querySelector('[role="main"]') || document;
  const out = [];
  for (const a of root.querySelectorAll('a[href*="set=a."]')) {
    const h = a.href;
    if (!/\/media\/set\/|\/media\/set\?/.test(h) && !/set=a\./.test(h)) continue;
    if (/fbid=/.test(h)) continue;
    let name = (a.innerText || a.getAttribute('aria-label') || '').trim().split('\n')[0];
    if (!name) {
      const img = a.querySelector('img[alt]');
      if (img) name = img.alt.trim();
    }
    let count = '';
    const lines = (a.innerText || '').trim().split('\n').map(s => s.trim()).filter(Boolean);
    for (const l of lines.slice(1)) if (/\d/.test(l)) { count = l; break; }
    out.push({href: h, name: name, count: count});
  }
  return out;
}
"""


# ---------------------------------------------------------------- helpers
def get_fbid(url):
    for pat in (r"[?&]fbid=(\d+)", r"/photos/[^/?#]+/(\d+)", r"/photo/(\d+)"):
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def get_set(url):
    try:
        qs = parse_qs(urlparse(url).query)
        if "set" in qs:
            return qs["set"][0]
    except Exception:
        pass
    m = re.search(r"/media/set/\?set=([\w.\-]+)", url)
    return m.group(1) if m else None


def is_photo_url(url):
    return get_fbid(url) is not None and ("/photo" in url)


def is_album_url(url):
    return "/media/set" in url and not is_photo_url(url)


def is_grid_url(url):
    """Link lưới ảnh: album, tab Ảnh của trang/hồ sơ."""
    if is_album_url(url):
        return True
    p = urlparse(url)
    if re.search(r"/(photos|photos_by|photos_of|photos_all)/?$", p.path):
        return True
    if "sk=photos" in p.query and "albums" not in p.query:
        return True
    return False


def album_key(url):
    s = get_set(url) or ""
    m = re.match(r"a\.(\d+)", s)
    return m.group(1) if m else s


def albums_url(page_url):
    """Từ link trang/hồ sơ -> link trang danh sách album."""
    url = page_url.strip()
    p = urlparse(url)
    if "photos_albums" in url:
        return url
    if p.path.rstrip("/").endswith("profile.php"):
        qs = parse_qs(p.query)
        pid = qs.get("id", [""])[0]
        return f"https://www.facebook.com/profile.php?id={pid}&sk=photos_albums"
    parts = [x for x in p.path.split("/") if x]
    name = parts[0] if parts else ""
    return f"https://www.facebook.com/{name}/photos_albums"


def all_photos_url(page_url):
    p = urlparse(page_url.strip())
    if p.path.rstrip("/").endswith("profile.php"):
        pid = parse_qs(p.query).get("id", [""])[0]
        return f"https://www.facebook.com/profile.php?id={pid}&sk=photos_by"
    parts = [x for x in p.path.split("/") if x]
    name = parts[0] if parts else ""
    return f"https://www.facebook.com/{name}/photos_by"


def page_name(page_url):
    p = urlparse(page_url.strip())
    if p.path.rstrip("/").endswith("profile.php"):
        return "id_" + parse_qs(p.query).get("id", ["page"])[0]
    parts = [x for x in p.path.split("/") if x]
    return parts[0] if parts else "page"


def safe_name(s):
    return re.sub(r"[^\w\-. ]+", "_", s).strip(" _.")[:80] or "fb"


def is_closed_error(e):
    s = str(e).lower()
    return "closed" in s or "disconnected" in s or "crashed" in s


def folder_key(first, url):
    k = get_set(first) or get_set(url)
    if k:
        return k
    m = re.search(r"facebook\.com/([^/?#]+)/posts/([\w]+)", url)
    if m:
        return f"{m.group(1)}_{m.group(2)[:24]}"
    m = re.search(r"facebook\.com/([^/?#]+)/(photos\w*)", url)
    if m:
        return f"{m.group(1)}_{m.group(2)}"
    return time.strftime("fb_%Y%m%d_%H%M%S")


def ext_from(url):
    path = urlparse(url).path.lower()
    for e in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if path.endswith(e):
            return ".jpg" if e == ".jpeg" else e
    return ".jpg"


def dedupe_photos(links, set_filter=None):
    """Giữ thứ tự, loại trùng theo fbid; nếu có set_filter thì ưu tiên link thuộc album đó."""
    seen, out = set(), []
    for h in links:
        f = get_fbid(h)
        if f and f not in seen:
            seen.add(f)
            out.append(h)
    if set_filter:
        pref = [h for h in out if set_filter in h]
        if pref:
            return pref
    return out


def dedupe_albums(items):
    seen, out = set(), []
    for it in items:
        k = album_key(it.get("href", ""))
        if not k or k in seen:
            continue
        seen.add(k)
        name = (it.get("name") or "").strip() or f"Album {k}"
        out.append({"url": f"https://www.facebook.com/media/set/?set={get_set(it['href'])}&type=3",
                    "name": name, "count": (it.get("count") or "").strip()})
    return out


def open_folder(path):
    os.makedirs(path, exist_ok=True)
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif os.name == "nt":
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", path])


# ---------------------------------------------------------------- cập nhật
UA_UPDATER = "Auto-Download-FB-Photo-Updater"
SKIP_ON_UPDATE = {".git", ".github", ".venv", "fb_profile", "browsers", "Portable", "dist", "build", "__pycache__"}


def parse_ver(s):
    nums = re.findall(r"\d+", s or "")
    return tuple(int(x) for x in nums[:3]) or (0,)


def is_newer(latest, current=APP_VERSION):
    a, b = parse_ver(latest), parse_ver(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def fetch_latest():
    req = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": UA_UPDATER})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def download_file(url, dest, progress=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA_UPDATER, "Accept": "application/octet-stream"})
    tmp = dest + ".part"
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    os.replace(tmp, dest)
    return dest


def pick_exe_asset(release):
    assets = release.get("assets") or []
    for a in assets:
        if a.get("name") == EXE_ASSET:
            return a
    for a in assets:
        if a.get("name", "").lower().endswith(".exe"):
            return a
    return None


def update_mode():
    """'exe' = tự thay file exe, 'source' = tự chép code mới, 'manual' = mở trang tải."""
    if FROZEN:
        return "exe" if os.name == "nt" else "manual"
    return "source"


def install_source_zip(zip_path, target_dir):
    """Giải nén mã nguồn bản mới và chép đè (giữ nguyên phiên đăng nhập, .venv...)."""
    tmp = tempfile.mkdtemp(prefix="adfb_")
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        roots = [os.path.join(tmp, d) for d in os.listdir(tmp)]
        root = roots[0] if len(roots) == 1 and os.path.isdir(roots[0]) else tmp
        if not os.path.exists(os.path.join(root, "auto_download_fb_photo.py")):
            raise RuntimeError("Gói cập nhật không hợp lệ (thiếu auto_download_fb_photo.py)")
        for name in os.listdir(root):
            if name in SKIP_ON_UPDATE:
                continue
            src, dst = os.path.join(root, name), os.path.join(target_dir, name)
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def launch_exe_swap(new_exe):
    """Windows: script chờ app thoát -> thay exe -> mở lại app."""
    exe = sys.executable
    bat = os.path.join(tempfile.gettempdir(), "adfb_update.bat")
    script = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "set n=0\r\n"
        ":wait\r\n"
        "timeout /t 1 /nobreak >nul\r\n"
        f'move /y "{new_exe}" "{exe}" >nul 2>&1\r\n'
        "if not errorlevel 1 goto ok\r\n"
        "set /a n+=1\r\n"
        "if %n% lss 60 goto wait\r\n"
        "goto end\r\n"
        ":ok\r\n"
        f'start "" "{exe}"\r\n'
        ":end\r\n"
        'del "%~f0"\r\n'
    )
    with open(bat, "w", encoding="utf-8") as f:
        f.write(script)
    subprocess.Popen(["cmd", "/c", bat], creationflags=0x08000000 | 0x00000200, close_fds=True)


def restart_source():
    main = os.path.join(APP_DIR, "auto_download_fb_photo.py")
    subprocess.Popen([sys.executable, main], cwd=APP_DIR, close_fds=True)


# ---------------------------------------------------------------- core
class Worker:
    def __init__(self, ui_queue, stop_event, headless=True):
        self.q = ui_queue
        self.stop = stop_event
        self.headless = headless
        self.p = self.ctx = self.page = None

    def log(self, msg):
        self.q.put(("log", msg))

    def status(self, msg):
        self.q.put(("status", msg))

    # ---- trình duyệt
    def install_chromium(self):
        self.log("Không thấy Chrome/Edge trên máy -> đang tải Chromium (chỉ lần đầu, ~150MB)...")
        self.status("Đang tải trình duyệt...")
        from playwright._impl._driver import compute_driver_executable, get_driver_env
        drv = compute_driver_executable()
        cmd = list(drv) if isinstance(drv, (tuple, list)) else [str(drv)]
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        subprocess.run(cmd + ["install", "chromium"], env=get_driver_env(), check=True, creationflags=flags)

    def open_context(self, p, headless=None):
        headless = self.headless if headless is None else headless
        os.makedirs(PROFILE_DIR, exist_ok=True)
        opts = dict(headless=headless, viewport={"width": 1280, "height": 900}, locale="vi-VN",
                    args=["--disable-blink-features=AutomationControlled"])
        if headless:
            opts["user_agent"] = REAL_UA  # tránh chuỗi "HeadlessChrome"
        for ch in ("chrome", "msedge"):
            try:
                return p.chromium.launch_persistent_context(PROFILE_DIR, channel=ch, **opts)
            except Exception:
                continue
        try:
            return p.chromium.launch_persistent_context(PROFILE_DIR, **opts)
        except Exception:
            self.install_chromium()
            return p.chromium.launch_persistent_context(PROFILE_DIR, **opts)

    def start_browser(self, p):
        self.p = p
        self.ctx = self.open_context(p)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()

    def close_browser(self):
        try:
            self.ctx.close()
        except Exception:
            pass

    def ensure_page(self):
        try:
            if self.page is not None and not self.page.is_closed():
                return
        except Exception:
            pass
        try:
            self.page = self.ctx.new_page()
            return
        except Exception:
            pass
        self.close_browser()
        self.ctx = self.open_context(self.p)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()

    def check_login_wall(self, page):
        u = page.url
        if "/login" in u or "checkpoint" in u:
            self.log("⚠ Facebook yêu cầu đăng nhập/xác minh. Bấm 'Đăng nhập lại' rồi thử lại.")
            return True
        return False

    # ---- đăng nhập (luôn hiện cửa sổ)
    def login(self):
        self.status("Chờ đăng nhập...")
        logged = False
        with sync_playwright() as p:
            ctx = self.open_context(p, headless=False)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://www.facebook.com/login")
            self.log("Hãy đăng nhập Facebook trong cửa sổ trình duyệt.")
            self.log("Đăng nhập xong thì ĐÓNG cửa sổ trình duyệt đó (phiên được lưu lại).")
            while not self.stop.is_set():
                try:
                    if page.is_closed():
                        break
                    if any(c.get("name") == "c_user" for c in ctx.cookies("https://www.facebook.com")):
                        if not logged:
                            self.log("Đã nhận diện đăng nhập thành công. Bạn có thể đóng trình duyệt.")
                        logged = True
                    page.wait_for_timeout(1000)
                except Exception:
                    break
            try:
                ctx.close()
            except Exception:
                pass
        if logged:
            with open(LOGIN_MARK, "w") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M"))
            self.log("Đã lưu phiên đăng nhập.")
        else:
            self.log("Chưa thấy đăng nhập thành công.")
        self.q.put(("login", logged))

    # ---- lấy danh sách album
    def list_albums(self, page_url):
        url = albums_url(page_url)
        self.status("Đang lấy danh sách album...")
        self.log(f"\nĐang đọc album: {url}")
        albums = []
        with sync_playwright() as p:
            self.start_browser(p)
            try:
                page = self.page
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(4000)
                if not self.check_login_wall(page):
                    raw, stable, last = [], 0, -1
                    while stable < 3 and not self.stop.is_set():
                        raw = page.evaluate(ALBUMS_JS)
                        n = len(dedupe_albums(raw))
                        stable = stable + 1 if n == last else 0
                        last = n
                        self.status(f"Đã thấy {n} album...")
                        page.mouse.wheel(0, 2500)
                        page.wait_for_timeout(1800)
                    albums = dedupe_albums(raw)
            finally:
                self.close_browser()
        self.log(f"Tìm thấy {len(albums)} album.")
        self.q.put(("albums", {"page": page_url, "items": albums}))

    # ---- duyệt ảnh
    def find_first_photo(self, page, url):
        if is_photo_url(url):
            return url
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        if self.check_login_wall(page):
            return None
        set_id = get_set(url)
        links = []
        for _ in range(4):
            links = page.evaluate(PHOTO_LINKS_JS)
            if links:
                break
            page.mouse.wheel(0, 900)
            page.wait_for_timeout(2000)
        if not links:
            return None
        if set_id:
            pref = [l for l in links if set_id in l]
            if pref:
                return pref[0]
        for key in ("set=pcb.", "set=a.", "set=g.", "set=gm."):
            pref = [l for l in links if key in l]
            if pref:
                return pref[0]
        return links[0]

    def wait_img(self, page, prev_src, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end and not self.stop.is_set():
            try:
                src = page.evaluate(LARGEST_IMG_JS)
            except Exception as e:
                if is_closed_error(e):
                    raise
                src = None
            if src and src != prev_src:
                return src
            page.wait_for_timeout(400)
        return None

    def wait_url_change(self, page, old, timeout=6.0):
        end = time.time() + timeout
        while time.time() < end and not self.stop.is_set():
            if page.url != old:
                return True
            page.wait_for_timeout(300)
        return False

    def go_next(self, page):
        old = page.url
        try:
            page.mouse.move(640, 450)
            page.keyboard.press("ArrowRight")
        except Exception as e:
            if is_closed_error(e):
                raise
        if self.wait_url_change(page, old):
            return True
        for label in NEXT_LABELS:
            try:
                loc = page.locator(f'[aria-label="{label}"]')
                if loc.count():
                    loc.first.click(timeout=2000)
                    if self.wait_url_change(page, old):
                        return True
            except Exception as e:
                if is_closed_error(e):
                    raise
        return False

    def save(self, src, fbid, index, folder):
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{index:04d}_{fbid}{ext_from(src)}")
        if os.path.exists(path):
            return True
        try:
            r = self.ctx.request.get(src, headers={"Referer": "https://www.facebook.com/"}, timeout=60000)
            if r.ok:
                with open(path, "wb") as f:
                    f.write(r.body())
                return True
            self.log(f"  lỗi HTTP {r.status} ảnh {fbid}")
        except Exception as e:
            if is_closed_error(e):
                raise
            self.log(f"  lỗi tải ảnh {fbid}: {str(e).splitlines()[0][:150]}")
        return False

    # Cách 1: bài viết -> bấm "ảnh tiếp theo" tới khi vòng lại
    def crawl(self, start_url, max_n, folder):
        seen, prev_src = set(), None
        count = ok = recover = 0
        last_url, need_goto, resume = start_url, True, False
        while not self.stop.is_set():
            try:
                if need_goto:
                    self.ensure_page()
                    self.page.goto(last_url, wait_until="domcontentloaded")
                    self.page.wait_for_timeout(2500)
                    need_goto = False
                    if resume:
                        resume = False
                        if not self.go_next(self.page):
                            self.log("Không còn ảnh tiếp theo.")
                            break
                page = self.page
                fbid = get_fbid(page.url) or page.url
                if fbid in seen:
                    self.log("Đã quay lại ảnh đầu tiên -> hết ảnh.")
                    break
                src = self.wait_img(page, prev_src)
                if src:
                    if self.save(src, fbid, count + 1, folder):
                        ok += 1
                        self.q.put(("saved", 1))
                    count += 1
                    prev_src = src
                    self.log(f"  [{count}] đã tải ảnh {fbid}")
                else:
                    self.log(f"  bỏ qua {fbid} (không thấy ảnh, có thể là video)")
                seen.add(fbid)
                last_url = page.url
                if count >= max_n:
                    self.log("Đạt giới hạn số ảnh.")
                    break
                if not self.go_next(page):
                    self.log("Không còn ảnh tiếp theo.")
                    break
            except Exception as e:
                if self.stop.is_set():
                    break
                recover += 1
                if recover > MAX_RECOVER:
                    self.log(f"Dừng vì lỗi lặp lại nhiều lần: {e}")
                    break
                self.log(f"  Trình duyệt bị đóng/lỗi -> mở lại và chạy tiếp ({recover}/{MAX_RECOVER})")
                need_goto = True
                resume = len(seen) > 0
        return count, ok

    # Cách 2: album / lưới ảnh -> cuộn lấy hết link, rồi mở từng ảnh
    def collect_grid(self, url, max_n):
        self.ensure_page()
        page = self.page
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        if self.check_login_wall(page):
            return []
        set_id = get_set(url)
        links, stable, last = [], 0, -1
        while stable < 4 and not self.stop.is_set():
            links = dedupe_photos(page.evaluate(GRID_LINKS_JS), set_id)
            n = len(links)
            stable = stable + 1 if n == last else 0
            last = n
            self.status(f"Đang quét album: {n} ảnh")
            if n >= max_n:
                break
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(1500)
        return links[:max_n]

    def download_grid(self, url, max_n, folder):
        links = []
        for attempt in range(3):
            try:
                links = self.collect_grid(url, max_n)
                break
            except Exception as e:
                self.log(f"  lỗi quét album, thử lại ({attempt + 1}/3): {e.__class__.__name__}")
        if not links:
            self.log("Không tìm thấy ảnh nào trong album.")
            return 0, 0
        total = len(links)
        self.log(f"Album có {total} ảnh. Bắt đầu tải...")
        self.q.put(("total", total))
        self.status("Đang tải ảnh...")
        ok = 0
        for i, link in enumerate(links, 1):
            if self.stop.is_set():
                break
            fbid = get_fbid(link)
            for attempt in range(3):
                try:
                    self.ensure_page()
                    self.page.goto(link, wait_until="domcontentloaded")
                    src = self.wait_img(self.page, None, timeout=12)
                    if src and self.save(src, fbid, i, folder):
                        ok += 1
                        self.q.put(("saved", 1))
                        self.log(f"  [{i}/{total}] đã tải ảnh {fbid}")
                    else:
                        self.log(f"  [{i}/{total}] bỏ qua {fbid} (không thấy ảnh)")
                    break
                except Exception as e:
                    if self.stop.is_set():
                        break
                    self.log(f"  lỗi ảnh {fbid}, thử lại ({attempt + 1}/3): {e.__class__.__name__}")
            self.q.put(("progress", i))
        return total, ok

    # ---- chạy danh sách công việc
    def run(self, jobs, out_dir, max_n):
        self.status("Đang khởi động" + (" (chạy ẩn)..." if self.headless else "..."))
        with sync_playwright() as p:
            self.start_browser(p)
            try:
                for n, job in enumerate(jobs, 1):
                    if self.stop.is_set():
                        break
                    url = job["url"]
                    self.q.put(("link", (n, len(jobs))))
                    self.q.put(("total", 0))
                    title = job.get("name") or url
                    self.log(f"\n=== {n}/{len(jobs)}: {title}")
                    if job.get("folder"):
                        folder = os.path.join(out_dir, job["folder"])
                    else:
                        folder = os.path.join(out_dir, safe_name(folder_key(url, url)))
                    self.q.put(("folder", folder))

                    if is_grid_url(url):
                        self.log(f"Lưu vào: {folder}")
                        total, ok = self.download_grid(url, max_n, folder)
                        self.log(f"Xong: {ok}/{total} ảnh đã lưu.")
                        continue

                    self.status("Đang tìm ảnh...")
                    first = None
                    for attempt in range(3):
                        try:
                            self.ensure_page()
                            first = self.find_first_photo(self.page, url)
                            break
                        except Exception as e:
                            self.log(f"  lỗi mở link, thử lại ({attempt + 1}/3): {e.__class__.__name__}")
                    if not first:
                        self.log("Không tìm thấy ảnh nào. Kiểm tra đã đăng nhập chưa, hoặc bài không có ảnh.")
                        continue
                    if not job.get("folder"):
                        folder = os.path.join(out_dir, safe_name(folder_key(first, url)))
                        self.q.put(("folder", folder))
                    self.log(f"Lưu vào: {folder}")
                    self.status("Đang tải ảnh...")
                    count, ok = self.crawl(first, max_n, folder)
                    self.log(f"Xong: {ok}/{count} ảnh đã lưu.")
            finally:
                self.close_browser()


# ---------------------------------------------------------------- GUI
COL = {
    "bg": "#0f1117",
    "card": "#181b24",
    "card2": "#1f2330",
    "border": "#2a2f3d",
    "text": "#e8eaf0",
    "muted": "#8b91a3",
    "accent": "#4f7dff",
    "accent_h": "#3d68e6",
    "green": "#18c47c",
    "green_h": "#12a668",
    "red": "#ef4f5f",
    "red_h": "#d33d4c",
}


class App(ctk.CTk if ctk else tk.Tk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title(APP_NAME)
        self.geometry("980x800")
        self.minsize(800, 680)
        self.configure(fg_color=COL["bg"])
        self.set_icon()

        self.q = queue.Queue()
        self.stop = threading.Event()
        self.thread = None
        self.saved_total = 0
        self.last_folder = None
        self.album_vars = []      # [(BooleanVar, job)]
        self.album_page = ""
        self.latest = None
        self.updating = False
        self.is_busy = False

        self.font_title = ctk.CTkFont(size=22, weight="bold")
        self.font_h = ctk.CTkFont(size=14, weight="bold")
        self.font_b = ctk.CTkFont(size=13)
        self.font_s = ctk.CTkFont(size=12)
        self.font_stat = ctk.CTkFont(size=24, weight="bold")
        self.font_mono = ctk.CTkFont(family="Consolas" if os.name == "nt" else "Menlo", size=12)

        self.build_header()
        self.build_tabs()
        self.build_options()
        self.build_actions()
        self.build_stats()
        self.build_log()

        self.refresh_login_badge()
        if sync_playwright is None:
            self.write("⚠ Chưa cài Playwright: pip install playwright")
        self.write(f"{APP_NAME} v{APP_VERSION} sẵn sàng.")
        self.after(100, self.poll)
        self.after(1500, lambda: self.check_update(silent=True))

    # ---------- dựng giao diện
    def set_icon(self):
        try:
            if os.name == "nt" and os.path.exists(ICON_ICO):
                self.iconbitmap(ICON_ICO)
            elif os.path.exists(ICON_PNG):
                self._icon_img = tk.PhotoImage(file=ICON_PNG)
                self.iconphoto(True, self._icon_img)
        except Exception:
            pass

    def card(self, parent, **kw):
        return ctk.CTkFrame(parent, fg_color=COL["card"], corner_radius=14,
                            border_width=1, border_color=COL["border"], **kw)

    def small_btn(self, parent, text, cmd, width=70):
        return ctk.CTkButton(parent, text=text, width=width, height=28, corner_radius=8, font=self.font_s,
                             fg_color=COL["card2"], hover_color=COL["border"], command=cmd)

    def build_header(self):
        h = ctk.CTkFrame(self, fg_color="transparent")
        h.pack(fill="x", padx=20, pady=(18, 6))
        if Image and os.path.exists(ICON_PNG):
            logo = ctk.CTkImage(Image.open(ICON_PNG), size=(48, 48))
            ctk.CTkLabel(h, image=logo, text="").pack(side="left", padx=(0, 12))
        tbox = ctk.CTkFrame(h, fg_color="transparent")
        tbox.pack(side="left")
        ctk.CTkLabel(tbox, text=APP_NAME, font=self.font_title, text_color=COL["text"]).pack(anchor="w")
        ctk.CTkLabel(tbox, text="Tải toàn bộ ảnh từ bài viết & album Facebook",
                     font=self.font_s, text_color=COL["muted"]).pack(anchor="w")

        right = ctk.CTkFrame(h, fg_color="transparent")
        right.pack(side="right")
        self.badge = ctk.CTkLabel(right, text="", font=self.font_s, corner_radius=12,
                                  fg_color=COL["card2"], padx=12, pady=4)
        self.badge.pack(side="left", padx=(0, 10))
        self.b_login = ctk.CTkButton(right, text="Đăng nhập Facebook", width=160, height=36,
                                     corner_radius=10, font=self.font_b,
                                     fg_color=COL["accent"], hover_color=COL["accent_h"],
                                     command=self.do_login)
        self.b_login.pack(side="left")
        self.b_update = ctk.CTkButton(right, text="⟳  Cập nhật", width=120, height=36, corner_radius=10,
                                      font=self.font_b, fg_color=COL["card2"], hover_color=COL["border"],
                                      command=self.do_update)
        self.b_update.pack(side="left", padx=(8, 0))

    def build_tabs(self):
        self.tabs = ctk.CTkTabview(self, height=270, corner_radius=14, fg_color=COL["card"],
                                   border_width=1, border_color=COL["border"],
                                   segmented_button_selected_color=COL["accent"],
                                   segmented_button_selected_hover_color=COL["accent_h"],
                                   segmented_button_unselected_color=COL["card2"])
        self.tabs.pack(fill="x", padx=20, pady=6)
        self.TAB_LINK = "  🔗  Link bài viết / album  "
        self.TAB_ALBUM = "  🗂  Album của trang  "
        t1 = self.tabs.add(self.TAB_LINK)
        t2 = self.tabs.add(self.TAB_ALBUM)

        # --- tab 1: danh sách link
        top = ctk.CTkFrame(t1, fg_color="transparent")
        top.pack(fill="x", padx=6, pady=(2, 4))
        ctk.CTkLabel(top, text="Mỗi dòng 1 link: bài viết, album (media/set/?set=a.…), tab Ảnh của trang hoặc link ảnh.",
                     font=self.font_s, text_color=COL["muted"]).pack(side="left")
        self.small_btn(top, "Xóa", lambda: self.links.delete("1.0", "end"), 60).pack(side="right")
        self.small_btn(top, "Dán", self.paste, 60).pack(side="right", padx=6)
        self.links = ctk.CTkTextbox(t1, corner_radius=10, font=self.font_b,
                                    fg_color=COL["bg"], border_width=1, border_color=COL["border"])
        self.links.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        # --- tab 2: album của trang
        row = ctk.CTkFrame(t2, fg_color="transparent")
        row.pack(fill="x", padx=6, pady=(2, 6))
        self.page_url = tk.StringVar()
        ctk.CTkEntry(row, textvariable=self.page_url, height=34, corner_radius=8, font=self.font_b,
                     fg_color=COL["bg"], border_color=COL["border"],
                     placeholder_text="Link trang / hồ sơ, vd: https://www.facebook.com/TheLuckyArtAi"
                     ).pack(side="left", fill="x", expand=True)
        self.b_albums = ctk.CTkButton(row, text="🔍  Lấy danh sách album", width=190, height=34, corner_radius=8,
                                      font=self.font_b, fg_color=COL["accent"], hover_color=COL["accent_h"],
                                      command=self.do_list_albums)
        self.b_albums.pack(side="left", padx=(8, 0))

        bar = ctk.CTkFrame(t2, fg_color="transparent")
        bar.pack(fill="x", padx=6)
        self.album_info = ctk.CTkLabel(bar, text="Chưa có danh sách album.", font=self.font_s,
                                       text_color=COL["muted"])
        self.album_info.pack(side="left")
        self.small_btn(bar, "Bỏ chọn", lambda: self.set_all_albums(False), 80).pack(side="right")
        self.small_btn(bar, "Chọn tất cả", lambda: self.set_all_albums(True), 90).pack(side="right", padx=6)

        self.album_list = ctk.CTkScrollableFrame(t2, corner_radius=10, fg_color=COL["bg"],
                                                 border_width=1, border_color=COL["border"])
        self.album_list.pack(fill="both", expand=True, padx=6, pady=6)

    def build_options(self):
        c = self.card(self)
        c.pack(fill="x", padx=20, pady=6)
        row = ctk.CTkFrame(c, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(12, 6))
        ctk.CTkLabel(row, text="Lưu vào", font=self.font_b, text_color=COL["muted"]).pack(side="left")
        self.out = tk.StringVar(value=DEFAULT_OUT)
        ctk.CTkEntry(row, textvariable=self.out, height=34, corner_radius=8, font=self.font_b,
                     fg_color=COL["bg"], border_color=COL["border"]).pack(side="left", fill="x", expand=True, padx=10)
        ctk.CTkButton(row, text="Chọn…", width=70, height=34, corner_radius=8, font=self.font_b,
                      fg_color=COL["card2"], hover_color=COL["border"], command=self.pick).pack(side="left")

        row2 = ctk.CTkFrame(c, fg_color="transparent")
        row2.pack(fill="x", padx=16, pady=(0, 12))
        self.headless = tk.BooleanVar(value=True)
        ctk.CTkSwitch(row2, text="Chạy ẩn (không hiện trình duyệt khi tải)", variable=self.headless,
                      font=self.font_b, progress_color=COL["green"]).pack(side="left")
        self.maxn = tk.StringVar(value="5000")
        ctk.CTkEntry(row2, textvariable=self.maxn, width=70, height=30, corner_radius=8, font=self.font_b,
                     fg_color=COL["bg"], border_color=COL["border"], justify="center").pack(side="right")
        ctk.CTkLabel(row2, text="Tối đa ảnh / mục", font=self.font_b,
                     text_color=COL["muted"]).pack(side="right", padx=6)

    def build_actions(self):
        a = ctk.CTkFrame(self, fg_color="transparent")
        a.pack(fill="x", padx=20, pady=6)
        self.b_start = ctk.CTkButton(a, text="▶  Bắt đầu tải", height=44, corner_radius=12,
                                     font=ctk.CTkFont(size=15, weight="bold"),
                                     fg_color=COL["green"], hover_color=COL["green_h"], command=self.do_start)
        self.b_start.pack(side="left", fill="x", expand=True)
        self.b_stop = ctk.CTkButton(a, text="■  Dừng", width=120, height=44, corner_radius=12, font=self.font_h,
                                    fg_color=COL["red"], hover_color=COL["red_h"], state="disabled",
                                    command=self.do_stop)
        self.b_stop.pack(side="left", padx=10)
        ctk.CTkButton(a, text="📁  Mở thư mục", width=150, height=44, corner_radius=12, font=self.font_h,
                      fg_color=COL["card2"], hover_color=COL["border"],
                      command=self.open_out).pack(side="left")

    def stat_tile(self, parent, title, value):
        t = self.card(parent)
        ctk.CTkLabel(t, text=title, font=self.font_s, text_color=COL["muted"]).pack(anchor="w", padx=14, pady=(8, 0))
        v = ctk.CTkLabel(t, text=value, font=self.font_stat, text_color=COL["text"])
        v.pack(anchor="w", padx=14, pady=(0, 8))
        return t, v

    def build_stats(self):
        s = ctk.CTkFrame(self, fg_color="transparent")
        s.pack(fill="x", padx=20, pady=4)
        s.grid_columnconfigure((0, 1, 2), weight=1, uniform="s")
        t1, self.v_link = self.stat_tile(s, "Mục", "0 / 0")
        t2, self.v_saved = self.stat_tile(s, "Ảnh đã tải", "0")
        t3, self.v_status = self.stat_tile(s, "Trạng thái", "Sẵn sàng")
        self.v_status.configure(font=ctk.CTkFont(size=16, weight="bold"))
        for i, t in enumerate((t1, t2, t3)):
            t.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 0 if i == 2 else 6))
        self.pbar = ctk.CTkProgressBar(self, mode="determinate", height=6, corner_radius=3,
                                       progress_color=COL["accent"], fg_color=COL["card2"])
        self.pbar.pack(fill="x", padx=20, pady=(8, 4))
        self.pbar.set(0)
        self.grid_total = 0

    def build_log(self):
        c = self.card(self)
        c.pack(fill="both", expand=True, padx=20, pady=(6, 8))
        ctk.CTkLabel(c, text="Nhật ký", font=self.font_h, text_color=COL["text"]).pack(anchor="w", padx=16, pady=(10, 4))
        self.logbox = ctk.CTkTextbox(c, font=self.font_mono, corner_radius=10, fg_color=COL["bg"],
                                     text_color="#b9c0d4", border_width=0, wrap="word")
        self.logbox.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        self.logbox.configure(state="disabled")
        ctk.CTkLabel(self, text=f"v{APP_VERSION}  •  Dữ liệu & phiên đăng nhập: {DATA_DIR}",
                     font=ctk.CTkFont(size=11), text_color=COL["muted"]).pack(anchor="w", padx=22, pady=(0, 10))

    # ---------- album list
    def fill_albums(self, page_url, items):
        for w in self.album_list.winfo_children():
            w.destroy()
        self.album_vars = []
        self.album_page = page_url
        pname = safe_name(page_name(page_url))

        def add_row(text, sub, job, checked):
            var = tk.BooleanVar(value=checked)
            r = ctk.CTkFrame(self.album_list, fg_color="transparent")
            r.pack(fill="x", padx=4, pady=2)
            ctk.CTkCheckBox(r, text=text, variable=var, font=self.font_b, checkbox_width=20, checkbox_height=20,
                            fg_color=COL["accent"], hover_color=COL["accent_h"]).pack(side="left")
            if sub:
                ctk.CTkLabel(r, text=sub, font=self.font_s, text_color=COL["muted"]).pack(side="right", padx=6)
            self.album_vars.append((var, job))

        add_row("⭐ Tất cả ảnh của trang (tab Ảnh)", "",
                {"url": all_photos_url(page_url), "name": f"{pname} - Tất cả ảnh",
                 "folder": os.path.join(pname, "_Tat_ca_anh")}, False)
        for it in items:
            add_row(it["name"], it.get("count", ""),
                    {"url": it["url"], "name": it["name"],
                     "folder": os.path.join(pname, safe_name(it["name"]))}, False)
        self.album_info.configure(text=f"{pname}: {len(items)} album — tick những album muốn tải.")

    def set_all_albums(self, v):
        for var, _ in self.album_vars:
            var.set(v)

    # ---------- tiện ích
    def refresh_login_badge(self):
        if os.path.exists(LOGIN_MARK):
            self.badge.configure(text="● Đã đăng nhập", text_color=COL["green"])
            self.b_login.configure(text="Đăng nhập lại")
        else:
            self.badge.configure(text="● Chưa đăng nhập", text_color="#f5a524")

    def paste(self):
        try:
            txt = self.clipboard_get()
        except Exception:
            return
        cur = self.links.get("1.0", "end").strip()
        self.links.insert("end", ("\n" if cur else "") + txt.strip())

    def pick(self):
        d = filedialog.askdirectory(initialdir=self.out.get())
        if d:
            self.out.set(d)

    def open_out(self):
        open_folder(self.last_folder or self.out.get())

    def write(self, msg):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", msg + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def busy(self, on):
        self.is_busy = on
        st = "disabled" if on else "normal"
        for b in (self.b_login, self.b_start, self.b_albums):
            b.configure(state=st)
        self.b_stop.configure(state="normal" if on else "disabled")
        if on:
            self.set_indeterminate()
        else:
            self.pbar.stop()
            self.pbar.configure(mode="determinate")
            self.pbar.set(0)

    def set_indeterminate(self):
        self.pbar.configure(mode="indeterminate")
        self.pbar.start()

    def launch(self, fn, *args, headless=True):
        if sync_playwright is None:
            messagebox.showerror("Thiếu thư viện", "Chưa cài Playwright.")
            return
        self.stop.clear()
        self.busy(True)
        w = Worker(self.q, self.stop, headless=headless)

        def job():
            try:
                getattr(w, fn)(*args)
            except Exception as e:
                self.q.put(("log", f"LỖI: {e}"))
            finally:
                self.q.put(("done", None))

        self.thread = threading.Thread(target=job, daemon=True)
        self.thread.start()

    def need_login_ok(self):
        if os.path.exists(LOGIN_MARK):
            return True
        return messagebox.askyesno("Chưa đăng nhập",
                                   "Bạn chưa đăng nhập Facebook trong tool.\nNhiều ảnh sẽ không xem được. Vẫn tiếp tục?")

    # ---------- hành động
    def do_login(self):
        self.launch("login", headless=False)

    def do_stop(self):
        self.stop.set()
        self.v_status.configure(text="Đang dừng...")

    def do_list_albums(self):
        url = self.page_url.get().strip()
        if not url.startswith("http"):
            messagebox.showwarning("Thiếu link", "Hãy nhập link trang hoặc hồ sơ Facebook.")
            return
        if not self.need_login_ok():
            return
        self.launch("list_albums", url, headless=self.headless.get())

    def collect_jobs(self):
        if self.tabs.get() == self.TAB_ALBUM:
            jobs = [job for var, job in self.album_vars if var.get()]
            if not jobs:
                messagebox.showwarning("Chưa chọn album", "Hãy lấy danh sách album và tick ít nhất 1 album.")
            return jobs
        links = [l.strip() for l in self.links.get("1.0", "end").splitlines() if l.strip().startswith("http")]
        if not links:
            messagebox.showwarning("Thiếu link", "Hãy dán ít nhất 1 link Facebook.")
        return [{"url": u} for u in links]

    def do_start(self):
        jobs = self.collect_jobs()
        if not jobs or not self.need_login_ok():
            return
        try:
            max_n = max(1, int(self.maxn.get()))
        except ValueError:
            max_n = 5000
        self.saved_total = 0
        self.v_saved.configure(text="0")
        self.launch("run", jobs, self.out.get(), max_n, headless=self.headless.get())

    # ---------- cập nhật
    def check_update(self, silent=False):
        def job():
            try:
                rel = fetch_latest()
                self.q.put(("update_info", (rel, silent)))
            except Exception as e:
                if not silent:
                    self.q.put(("update_err", f"Không kiểm tra được cập nhật: {e}"))
        threading.Thread(target=job, daemon=True).start()

    def on_update_info(self, rel, silent):
        tag = rel.get("tag_name", "")
        if is_newer(tag):
            self.latest = rel
            self.b_update.configure(text=f"⬆  Có bản {tag}", fg_color="#f5a524", hover_color="#d98f12",
                                    text_color="#111")
            self.write(f"🔔 Đã có bản mới {tag}. Bấm nút 'Có bản {tag}' để cập nhật.")
            if not silent:
                self.ask_update()
        else:
            self.latest = None
            if not silent:
                messagebox.showinfo(APP_NAME, f"Bạn đang dùng bản mới nhất (v{APP_VERSION}).")

    def do_update(self):
        if self.updating:
            return
        if self.latest:
            self.ask_update()
        else:
            self.b_update.configure(text="Đang kiểm tra…")
            self.after(3000, lambda: self.latest or self.b_update.configure(text="⟳  Cập nhật"))
            self.check_update(silent=False)

    def ask_update(self):
        rel = self.latest
        tag = rel.get("tag_name", "")
        notes = (rel.get("body") or "").strip()
        if len(notes) > 700:
            notes = notes[:700] + "…"
        if self.is_busy:
            messagebox.showwarning(APP_NAME, "Tool đang chạy. Hãy dừng hoặc chờ xong rồi cập nhật.")
            return
        mode = update_mode()
        if mode == "exe" and not pick_exe_asset(rel):
            mode = "manual"
        msg = f"Có bản mới {tag} (đang dùng v{APP_VERSION}).\n\n{notes}\n\n"
        msg += "Mở trang tải bản mới?" if mode == "manual" else "Tải và cập nhật ngay? Tool sẽ tự khởi động lại."
        if not messagebox.askyesno("Cập nhật", msg):
            return
        if mode == "manual":
            import webbrowser
            webbrowser.open(rel.get("html_url") or RELEASES_URL)
            return
        self.start_update(rel, mode)

    def start_update(self, rel, mode):
        self.updating = True
        self.b_update.configure(state="disabled", text="Đang tải 0%")
        self.write(f"Đang tải bản cập nhật {rel.get('tag_name')}...")

        def prog(done, total):
            self.q.put(("update_progress", int(done * 100 / total) if total else -1))

        def job():
            try:
                if mode == "exe":
                    a = pick_exe_asset(rel)
                    dest = sys.executable + ".new"
                    download_file(a["browser_download_url"], dest, prog)
                    if a.get("size") and os.path.getsize(dest) != a["size"]:
                        raise RuntimeError("File tải về bị thiếu, thử lại sau.")
                    self.q.put(("update_ready", ("exe", dest)))
                else:
                    z = os.path.join(tempfile.gettempdir(), "adfb_update.zip")
                    download_file(rel["zipball_url"], z, prog)
                    install_source_zip(z, APP_DIR)
                    os.remove(z)
                    self.q.put(("update_ready", ("source", None)))
            except Exception as e:
                self.q.put(("update_err", f"Cập nhật thất bại: {e}"))
        threading.Thread(target=job, daemon=True).start()

    def finish_update(self, mode, path):
        self.write("Cập nhật xong, đang khởi động lại...")
        try:
            if mode == "exe":
                launch_exe_swap(path)
            else:
                restart_source()
        except Exception as e:
            self.updating = False
            self.b_update.configure(state="normal", text="⟳  Cập nhật")
            messagebox.showerror(APP_NAME, f"Không khởi động lại được: {e}\nHãy tự mở lại tool.")
            return
        self.after(300, lambda: os._exit(0))

    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.write(val)
                elif kind == "status":
                    self.v_status.configure(text=val)
                elif kind == "saved":
                    self.saved_total += val
                    self.v_saved.configure(text=str(self.saved_total))
                elif kind == "link":
                    self.v_link.configure(text=f"{val[0]} / {val[1]}")
                elif kind == "total":
                    self.grid_total = val
                    if val:
                        self.pbar.stop()
                        self.pbar.configure(mode="determinate")
                        self.pbar.set(0)
                    else:
                        self.set_indeterminate()
                elif kind == "progress":
                    if self.grid_total:
                        self.pbar.set(val / self.grid_total)
                elif kind == "folder":
                    self.last_folder = val
                elif kind == "albums":
                    self.fill_albums(val["page"], val["items"])
                elif kind == "update_info":
                    self.on_update_info(*val)
                elif kind == "update_progress":
                    self.b_update.configure(text="Đang tải…" if val < 0 else f"Đang tải {val}%")
                elif kind == "update_ready":
                    self.finish_update(*val)
                elif kind == "update_err":
                    self.updating = False
                    self.b_update.configure(state="normal", text="⟳  Cập nhật")
                    self.write(val)
                    messagebox.showerror(APP_NAME, val)
                elif kind == "login":
                    self.refresh_login_badge()
                elif kind == "done":
                    self.busy(False)
                    self.v_status.configure(text="Hoàn tất")
                    self.write("─── Hoàn tất ───")
        except queue.Empty:
            pass
        self.after(100, self.poll)


if __name__ == "__main__":
    if ctk is None:
        r = tk.Tk(); r.withdraw()
        messagebox.showerror(APP_NAME, "Thiếu thư viện customtkinter.\nChạy: pip install customtkinter pillow playwright")
        sys.exit(1)
    App().mainloop()
