#!/usr/bin/env python3
import os
import sys
import json
import time
import asyncio
import unicodedata
import re
import argparse
from typing import List, Dict, Any, Optional
from pathlib import Path
import requests
import aiohttp

PAUSE_FLAG = Path("pause.flag")

async def wait_if_paused():
    """Boucle de pause globale : si pause.flag existe, on se met en attente."""
    if not PAUSE_FLAG.exists():
        return
    print(color("[INFO] Pause active…", C.YELLOW))
    # On reste bloqué tant que le flag existe
    while PAUSE_FLAG.exists():
        await asyncio.sleep(1.0)
    print(color("[INFO] Reprise des téléchargements.", C.YELLOW))


class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"

    RED     = "\033[31m"
    GREEN   = "\033[32m"
    YELLOW  = "\033[33m"
    BLUE    = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN    = "\033[36m"

def color(txt, col):
    return f"{col}{txt}{C.RESET}"

# CONFIG & CONSTANTES

STEP = 50
VIDEO_EXTS = (".mp4", ".webm", ".mov", ".mkv", ".m4v")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
MEDIA_VIDEO = "video"
MEDIA_IMAGE = "image"
MEDIA_OTHER = "other"

def media_type_from_url(url: str) -> str:
    u = (url or "").lower().split("?", 1)[0]
    if u.endswith(VIDEO_EXTS):
        return MEDIA_VIDEO
    if u.endswith(IMAGE_EXTS):
        return MEDIA_IMAGE
    return MEDIA_OTHER

def split_counts_by_url(items: List[dict]) -> dict:
    c = {"video": 0, "image": 0, "other": 0, "total": 0}
    for it in items:
        url = it.get("url") or ""
        t = media_type_from_url(url)
        if t == "video":
            c["video"] += 1
        elif t == "image":
            c["image"] += 1
        else:
            c["other"] += 1
        c["total"] += 1
    return c

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/css",
    "Referer": "https://coomer.st/",
}

TOTAL_DOWNLOADS = 0
PROGRESS_DONE = 0
PROGRESS_OK = 0
PROGRESS_FAIL = 0
PROGRESS_SKIPPED = 0
PROGRESS_OK_BY_TYPE = {MEDIA_VIDEO: 0, MEDIA_IMAGE: 0, MEDIA_OTHER: 0}
PROGRESS_FAIL_BY_TYPE = {MEDIA_VIDEO: 0, MEDIA_IMAGE: 0, MEDIA_OTHER: 0}
PROGRESS_SKIP_BY_TYPE = {MEDIA_VIDEO: 0, MEDIA_IMAGE: 0, MEDIA_OTHER: 0}

FAILED_URLS: List[str] = []
SKIPPED_FILES: List[str] = []
OK_FILES: List[str] = []

# ARGS
def parse_args():
    parser = argparse.ArgumentParser(
        description="Downloader OF/Fansly → Coomer (async + resume + retry + colors)",
        formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument("-s", "--service", choices=["onlyfans", "fansly"])
    parser.add_argument("-u", "--user", required=True)
    parser.add_argument("-d", "--download", action="store_true")
    parser.add_argument("-c", "--max-concurrent", type=int, default=8)
    parser.add_argument("-r", "--max-retries", type=int, default=3)
    parser.add_argument("-R", "--retry-forever", action="store_true")
    parser.add_argument("-F", "--only-failed", action="store_true")
    parser.add_argument("--preview", action="store_true", help="Afficher les posts sans télécharger")
    parser.add_argument("--throttle-delay", type=float, default=0.0, help="Temps (en secondes) à attendre après chaque téléchargement (0 = désactivé).")
    parser.add_argument("--sort", choices=["id", "title", "published"], default="published")
    parser.add_argument("--reverse", action="store_true")
    parser.add_argument("--only-posts", type=str, default=None, help="Liste de post_id séparés par des virgules à télécharger uniquement.")
    parser.add_argument("--media", choices=["videos", "images", "all"], default="videos", help="Type de médias à récupérer: videos (défaut), images, ou all.")
    
    # Shortcuts
    argv = sys.argv[1:]
    shortcuts = {
        "-dl": "--download",
        "-mc": "--max-concurrent",
        "-rf": "--retry-forever",
        "-md": "--media",
        "-ofail": "--only-failed",
    }
    service_alias = {
        "of": "onlyfans",
        "f": "fansly",
    }

    # Expand shortcuts BEFORE argparse processes them
    expanded = []
    skip_next = False
    for i, arg in enumerate(argv):
        if skip_next:
            skip_next = False
            continue

        if arg in shortcuts:
            expanded.append(shortcuts[arg])
            continue

        if arg == "-s" and i+1 < len(argv):
            nxt = argv[i+1]
            if nxt in service_alias:
                expanded.append("-s")
                expanded.append(service_alias[nxt])
                skip_next = True
                continue

        if arg == "-mc":
            expanded.append("--max-concurrent")
            skip_next = False
            continue
        expanded.append(arg)

    args = parser.parse_args(expanded)
    return args

# UTILITAIRES

def slugify(v: str, max_len=60):
    v = unicodedata.normalize("NFKD", v)
    v = v.encode("ascii", "ignore").decode("ascii")
    v = re.sub(r"[^a-zA-Z0-9]+", "-", v)
    v = re.sub(r"-{2,}", "-", v).strip("-")
    return v.lower()[:max_len] or "no-title"


def is_video(entry):
    if not entry:
        return False
    name = (entry.get("name") or "").lower()
    mime = (entry.get("mimetype") or "").lower()
    if mime.startswith("video/"):
        return True
    return any(name.endswith(ext) for ext in VIDEO_EXTS)

def is_image(entry):
    if not entry:
        return False
    name = (entry.get("name") or "").lower()
    mime = (entry.get("mimetype") or "").lower()
    if mime.startswith("image/"):
        return True
    return any(name.endswith(ext) for ext in IMAGE_EXTS)

def build_url(path: str):
    return "https://coomer.st" + path


def failed_file_path(state_file: str):
    return state_file.replace("state_", "failed_").replace(".json", ".txt")


def save_failed_list(state_file, lst):
    p = failed_file_path(state_file)
    with open(p, "w", encoding="utf-8") as f:
        for u in lst:
            f.write(u + "\n")


def load_failed_list(state_file):
    p = failed_file_path(state_file)
    if not os.path.exists(p):
        return []
    with open(p, "r", encoding="utf-8") as f:
        return [x.strip() for x in f if x.strip()]


def delete_failed_file_if_exists(state_file):
    p = failed_file_path(state_file)
    if os.path.exists(p):
        os.remove(p)


def print_progress(status: str, filename: str):
    if TOTAL_DOWNLOADS == 0:
        return

    pct = (PROGRESS_DONE / TOTAL_DOWNLOADS) * 100

    if status.startswith("OK"):
        st = color(status, C.GREEN)
    elif status.startswith("FAIL"):
        st = color(status, C.RED)
    elif status.startswith("SKIP"):
        st = color(status, C.YELLOW)
    else:
        st = color(status, C.CYAN)

    print(
        f"{color('[PROGRESS]', C.BLUE)} "
        f"{PROGRESS_DONE}/{TOTAL_DOWNLOADS} ({pct:5.1f}%) - "
        f"{st}: {filename}"
    )
# EXTRACTION DES MEDIAS

def extract_media_from_post(post, media_mode: str):
    """
    media_mode: 'videos' | 'images' | 'all'
    Retourne une liste d'items (1 par fichier) avec base {post_id, published, title, url, index}.
    """
    out = []
    base = {
        "post_id": post.get("id"),
        "published": post.get("published") or "",
        "title": post.get("title") or "",
    }

    def accept(entry) -> bool:
        if media_mode == "videos":
            return is_video(entry)
        if media_mode == "images":
            return is_image(entry)
        # all
        return is_video(entry) or is_image(entry)

    def push(entry, idx):
        out.append({**base, "url": build_url(entry["path"]), "index": idx})

    idx = 0

    # fichier principal
    f0 = post.get("file")
    if f0 and f0.get("path") and accept(f0):
        push(f0, idx)
        idx += 1

    # attachments
    for att in post.get("attachments") or []:
        if att and att.get("path") and accept(att):
            push(att, idx)
            idx += 1

    return out

# FILENAME LOGIC

def compute_filename(item):
    url = item["url"]
    pub = item.get("published", "")
    post_id = item.get("post_id", "noid")
    idx = item.get("index", 0)
    title = slugify(item.get("title", ""))

    # timestamp
    ts = pub.replace(":", "").replace("-", "").replace("T", "_")
    ts = ts or "00000000_000000"

    # extension
    base = url.split("?")[0].split("/")[-1]
    _, ext = os.path.splitext(base)
    if not ext:
        ext = ".bin"

    return f"{ts}_{post_id}_{idx:02d}_{title}{ext}"


# CHARGER/SAVE ÉTAT

def load_state(path: str):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(path: str, state: dict):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# TRIS DES POSTS

def sort_key_fn(item, key):
    if key == "id":
        return (str(item.get("post_id")), item.get("published"))
    if key == "title":
        return ((item.get("title") or "").lower(), item.get("post_id"))
    # default: published
    return (item.get("published"), item.get("post_id"))


# FETCH PROFILE

def fetch_account_label(service, user):
    url = f"https://coomer.st/api/v1/{service}/user/{user}/profile"
    print(color(f"[PROFILE] {url}", C.CYAN))

    try:
        r = requests.get(url, headers=HEADERS)
        print("  -> status:", r.status_code)
        if r.status_code != 200:
            return slugify(user)

        data = r.json()
        for k in ("user", "username", "name"):
            if data.get(k):
                print("  -> Profil name:", data[k])
                return slugify(data[k], 40)

        return slugify(str(data.get("id", user)), 40)

    except Exception as e:
        print(color(f"[PROFILE] Erreur: {e}", C.RED))
        return slugify(user)


# COLLECTE DES POSTS

def collect_all(service, user, media_mode: str):
    base = f"https://coomer.st/api/v1/{service}/user/{user}/posts?o={{}}"

    print(color("=== TEST PAGE 0 ===", C.BLUE))
    r0 = requests.get(base.format(0), headers=HEADERS)
    r0.raise_for_status()

    try:
        first = r0.json()
    except Exception:
        print(color("[ERR] Impossible de parser la première page", C.RED))
        return []

    print(f"Première page : {len(first)} posts")

    all_items = []
    print(color("\n=== DÉBUT COLLECTE ===\n", C.BLUE))

    offset = 0
    while True:
        url = base.format(offset)
        print(color(f"[API] {url}", C.CYAN))
        r = requests.get(url, headers=HEADERS)
        print("  -> status:", r.status_code)

        if r.status_code != 200:
            print("  -> break")
            break

        try:
            posts = r.json()
        except:
            print("  -> JSON error, break")
            break

        if not posts:
            break

        print(f"  -> {len(posts)} posts")

        for p in posts:
            items = extract_media_from_post(p, media_mode)
            all_items.extend(items)

        offset += STEP
        time.sleep(0.4)

    label = "médias" if media_mode == "all" else ("images" if media_mode == "images" else "vidéos")
    counts = split_counts_by_url(all_items)
    print(color(f"\n[INFO] Total médias trouvés : {counts['total']} "f"(vidéos: {counts['video']}, images: {counts['image']}" + (f", autres: {counts['other']}" if counts["other"] else "") + ")", C.GREEN))
    
    return all_items



# ASYNC DL

async def download_one(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    item: dict,
    state: dict,
    state_path: str,
    retry_forever: bool,
    max_retries: int,
    retry_sleep: float,
):
    global PROGRESS_DONE, PROGRESS_OK, PROGRESS_FAIL, PROGRESS_SKIPPED

    url = item["url"]
    filename = item["filename"]
    mtype = media_type_from_url(url)
    dest = os.path.join(item["download_dir"], filename)
    tmp = dest + ".part"

    # Déjà présent = SKIP
    if os.path.exists(dest):
        state[url]["downloaded"] = True
        save_state(state_path, state)
        PROGRESS_DONE += 1
        PROGRESS_SKIPPED += 1
        PROGRESS_SKIP_BY_TYPE[mtype] += 1
        SKIPPED_FILES.append(filename)
        print_progress("SKIP", filename)
        return

    attempt = 0

    while True:
        attempt += 1

        async with sem:
            tag = f"{attempt}" if not retry_forever else f"{attempt} (∞)"
            print(
                f"{color('[DL]', C.CYAN)} "
                f"{color(f'({tag})', C.MAGENTA)} {url}"
            )

            try:
                async with session.get(url, headers=HEADERS) as resp:

                    if resp.status == 404:
                        print(color("[ERR] 404 permanent", C.RED))
                        PROGRESS_DONE += 1
                        PROGRESS_FAIL += 1
                        PROGRESS_FAIL_BY_TYPE[mtype] += 1
                        FAILED_URLS.append(url)
                        print_progress("FAIL404", filename)
                        return

                    if resp.status >= 500:
                        raise RuntimeError(f"HTTP {resp.status}")

                    # Write body
                    with open(tmp, "wb") as fh:
                        async for chunk in resp.content.iter_chunked(8192):
                            if chunk:
                                fh.write(chunk)

                # move
                os.replace(tmp, dest)

                # mark OK
                state[url]["downloaded"] = True
                save_state(state_path, state)

                PROGRESS_DONE += 1
                PROGRESS_OK += 1
                PROGRESS_OK_BY_TYPE[mtype] += 1
                OK_FILES.append(filename)
                print_progress("OK", filename)
                return

            except Exception as e:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except:
                        pass

                print(color(f"[ERR] Tentative {attempt} échouée : {e}", C.RED))

        # Retry
        if not retry_forever:
            if attempt >= max_retries:
                print(color("[FAIL] Abandon après retries", C.RED))
                PROGRESS_DONE += 1
                PROGRESS_FAIL += 1
                PROGRESS_FAIL_BY_TYPE[mtype] += 1
                FAILED_URLS.append(url)
                print_progress("FAIL", filename)
                return

        sleep_for = retry_sleep * attempt
        print(color(f"[INFO] Retry dans {sleep_for:.1f}s…", C.YELLOW))
        await asyncio.sleep(sleep_for)

async def guarded_download_one(
    session,
    sem,
    it,
    state,
    state_path,
    retry_forever,
    max_retries,
    retry_sleep,
):
    await wait_if_paused()

    return await download_one(
        session,
        sem,
        it,
        state,
        state_path,
        retry_forever,
        max_retries,
        retry_sleep,
    )

async def download_all(
    items: List[dict],
    state: dict,
    state_path: str,
    max_conc: int,
    retry_forever: bool,
    max_retries: int,
    retry_sleep: float,
):
    global TOTAL_DOWNLOADS

    sem = asyncio.Semaphore(max_conc)
    connector = aiohttp.TCPConnector(limit=max_conc)
    timeout = aiohttp.ClientTimeout(total=None)

    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout
    ) as session:

        tasks = []
        for it in items:
            if not state.get(it["url"], {}).get("downloaded", False):
                tasks.append(
                    asyncio.create_task(
                        guarded_download_one(
                            session,
                            sem,
                            it,
                            state,
                            state_path,
                            retry_forever,
                            max_retries,
                            retry_sleep,
                        )
                    )
                )

        TOTAL_DOWNLOADS = len(tasks)
        print(color(f"[INFO] Téléchargements nécessaires : {TOTAL_DOWNLOADS}", C.GREEN))

        if not tasks:
            return

        await asyncio.gather(*tasks)
# MAIN

def main():
    global TOTAL_DOWNLOADS, PROGRESS_DONE, PROGRESS_OK, PROGRESS_FAIL, PROGRESS_SKIPPED
    global FAILED_URLS, SKIPPED_FILES, OK_FILES

    args = parse_args()

    SERVICE = args.service
    USER = args.user
    MEDIA_MODE = args.media

    SORT_KEY = args.sort
    SORT_REVERSE = args.reverse

    DO_DOWNLOAD = args.download
    MAX_CONC = args.max_concurrent
    RETRY_FOREVER = args.retry_forever
    MAX_RETRIES = max(1, args.max_retries)
    RETRY_SLEEP = 3.0

    ONLY_FAILED_MODE = args.only_failed

    # AFFICHAGE DE LA CONFIG

    print(color("=== CONFIG ===", C.BLUE))
    print("Service :", SERVICE)
    print("User    :", USER)
    print("Download:", DO_DOWNLOAD)
    print("Media   :", MEDIA_MODE)
    print("Retries :", "∞" if RETRY_FOREVER else MAX_RETRIES)
    print("MaxConc :", MAX_CONC)
    print("Sort    :", SORT_KEY)
    print("Reverse :", SORT_REVERSE)
    print("OnlyFailed:", ONLY_FAILED_MODE)
    print("================\n")

    # LABEL (nom du compte) + PATHS

    label = fetch_account_label(SERVICE, USER) or slugify(USER)
    DOWNLOAD_DIR = f"media_{SERVICE}_{label}_{MEDIA_MODE}"
    STATE_FILE = f"state_{SERVICE}_{label}_{MEDIA_MODE}.json"

    print("Dossier :", DOWNLOAD_DIR)
    print("State   :", STATE_FILE)
    print()

    # Charger état
    state = load_state(STATE_FILE)

    # MODE ONLY-FAILED

    if ONLY_FAILED_MODE:
        print(color("=== MODE ONLY-FAILED ===", C.BLUE))

        failed = load_failed_list(STATE_FILE)
        if not failed:
            print(color("Aucune URL dans failed_*.txt → rien à faire", C.YELLOW))
            return

        print(color(f"{len(failed)} URLs à retenter.\n", C.GREEN))

        items = []
        for url in failed:
            entry = state.get(url)
            if not entry:
                print(color(f"[WARN] URL manquante dans state : {url}", C.YELLOW))
                continue

            items.append({
                "url": url,
                "filename": entry["filename"],
                "post_id": entry.get("post_id"),
                "published": entry.get("published"),
                "title": entry.get("title"),
                "index": 0,
                "download_dir": DOWNLOAD_DIR,
            })

        # Reset stats
        TOTAL_DOWNLOADS = 0
        PROGRESS_DONE = PROGRESS_OK = PROGRESS_FAIL = PROGRESS_SKIPPED = 0
        PROGRESS_OK_BY_TYPE.update({MEDIA_VIDEO: 0, MEDIA_IMAGE: 0, MEDIA_OTHER: 0})
        PROGRESS_FAIL_BY_TYPE.update({MEDIA_VIDEO: 0, MEDIA_IMAGE: 0, MEDIA_OTHER: 0})
        PROGRESS_SKIP_BY_TYPE.update({MEDIA_VIDEO: 0, MEDIA_IMAGE: 0, MEDIA_OTHER: 0})
        FAILED_URLS.clear()
        SKIPPED_FILES.clear()
        OK_FILES.clear()

        os.makedirs(DOWNLOAD_DIR, exist_ok=True)

        asyncio.run(
            download_all(
                items, state, STATE_FILE,
                MAX_CONC, RETRY_FOREVER, MAX_RETRIES, RETRY_SLEEP
            )
        )

        # RÉSUMÉ ONLY-FAILED
        print(color("\n=== RÉSUMÉ ONLY-FAILED ===", C.BOLD))
        print("OK     :", color(PROGRESS_OK, C.GREEN))
        print("Fail   :", color(PROGRESS_FAIL, C.RED))
        print("Skip   :", color(PROGRESS_SKIPPED, C.YELLOW))
        print(color("\n--- DÉTAIL PAR TYPE ---", C.BOLD))
        print("Videos  :", color(PROGRESS_OK_BY_TYPE[MEDIA_VIDEO], C.GREEN), "OK /", color(PROGRESS_FAIL_BY_TYPE[MEDIA_VIDEO], C.RED), "Fail /", color(PROGRESS_SKIP_BY_TYPE[MEDIA_VIDEO], C.YELLOW), "Skip")
        print("Images  :", color(PROGRESS_OK_BY_TYPE[MEDIA_IMAGE], C.GREEN), "OK /", color(PROGRESS_FAIL_BY_TYPE[MEDIA_IMAGE], C.RED), "Fail /", color(PROGRESS_SKIP_BY_TYPE[MEDIA_IMAGE], C.YELLOW), "Skip")
        if PROGRESS_OK_BY_TYPE[MEDIA_OTHER] or PROGRESS_FAIL_BY_TYPE[MEDIA_OTHER] or PROGRESS_SKIP_BY_TYPE[MEDIA_OTHER]:
            print("Other   :", color(PROGRESS_OK_BY_TYPE[MEDIA_OTHER], C.GREEN), "OK /", color(PROGRESS_FAIL_BY_TYPE[MEDIA_OTHER], C.RED), "Fail /", color(PROGRESS_SKIP_BY_TYPE[MEDIA_OTHER], C.YELLOW), "Skip")

        if FAILED_URLS:
            save_failed_list(STATE_FILE, FAILED_URLS)
            print(color("[INFO] failed.txt mis à jour", C.YELLOW))
        else:
            delete_failed_file_if_exists(STATE_FILE)
            print(color("[INFO] Tous les fails ont été récupérés → failed.txt supprimé", C.GREEN))

        print()
        return

    # MODE NORMAL : collecte complète
    all_items = collect_all(SERVICE, USER, MEDIA_MODE)

    # Mode preview 
    if args.preview:
        print("__PREVIEW_JSON_START__")
        import json
        print(json.dumps(all_items, ensure_ascii=False))
        print("__PREVIEW_JSON_END__")
        return
    
    # Filtrage éventuellement par liste de posts
    if args.only_posts:
        wanted = set(p.strip() for p in args.only_posts.split(",") if p.strip())
        if wanted:
            def _get_post_id(it):
                return str(it.get("post_id") or it.get("id") or "")
            all_items = [it for it in all_items if _get_post_id(it) in wanted]

    # tri
    all_items.sort(key=lambda x: sort_key_fn(x, SORT_KEY), reverse=SORT_REVERSE)

    # préparer items avec filename
    for it in all_items:
        url = it["url"]
        if url not in state:
            filename = compute_filename(it)
            state[url] = {
                "filename": filename,
                "post_id": it.get("post_id"),
                "published": it.get("published"),
                "title": it.get("title"),
                "downloaded": False,
            }

        it["filename"] = state[url]["filename"]
        it["download_dir"] = DOWNLOAD_DIR

    save_state(STATE_FILE, state)

    print(color(f"[INFO] État sauvegardé ({len(state)} entrées)\n", C.GREEN))

    if not DO_DOWNLOAD:
        print(color("Dry-run → aucun téléchargement.\n", C.YELLOW))
        return

    # Reset stats
    TOTAL_DOWNLOADS = 0
    PROGRESS_DONE = PROGRESS_OK = PROGRESS_FAIL = PROGRESS_SKIPPED = 0
    PROGRESS_OK_BY_TYPE.update({MEDIA_VIDEO: 0, MEDIA_IMAGE: 0, MEDIA_OTHER: 0})
    PROGRESS_FAIL_BY_TYPE.update({MEDIA_VIDEO: 0, MEDIA_IMAGE: 0, MEDIA_OTHER: 0})
    PROGRESS_SKIP_BY_TYPE.update({MEDIA_VIDEO: 0, MEDIA_IMAGE: 0, MEDIA_OTHER: 0})
    FAILED_URLS.clear()
    SKIPPED_FILES.clear()
    OK_FILES.clear()

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    asyncio.run(
        download_all(
            all_items, state, STATE_FILE,
            MAX_CONC, RETRY_FOREVER, MAX_RETRIES, RETRY_SLEEP
        )
    )

    # RÉSUMÉ FINAL
    print(color("\n=== RÉSUMÉ ===", C.BOLD))
    print("OK     :", color(PROGRESS_OK, C.GREEN))
    print("Fail   :", color(PROGRESS_FAIL, C.RED))
    print("Skip   :", color(PROGRESS_SKIPPED, C.YELLOW))
    print(color("\n--- DÉTAIL PAR TYPE ---", C.BOLD))
    print("Videos  :", color(PROGRESS_OK_BY_TYPE[MEDIA_VIDEO], C.GREEN), "OK /", color(PROGRESS_FAIL_BY_TYPE[MEDIA_VIDEO], C.RED), "Fail /", color(PROGRESS_SKIP_BY_TYPE[MEDIA_VIDEO], C.YELLOW), "Skip")
    print("Images  :", color(PROGRESS_OK_BY_TYPE[MEDIA_IMAGE], C.GREEN), "OK /", color(PROGRESS_FAIL_BY_TYPE[MEDIA_IMAGE], C.RED), "Fail /", color(PROGRESS_SKIP_BY_TYPE[MEDIA_IMAGE], C.YELLOW), "Skip")
    if PROGRESS_OK_BY_TYPE[MEDIA_OTHER] or PROGRESS_FAIL_BY_TYPE[MEDIA_OTHER] or PROGRESS_SKIP_BY_TYPE[MEDIA_OTHER]:
        print("Other   :", color(PROGRESS_OK_BY_TYPE[MEDIA_OTHER], C.GREEN), "OK /", color(PROGRESS_FAIL_BY_TYPE[MEDIA_OTHER], C.RED), "Fail /", color(PROGRESS_SKIP_BY_TYPE[MEDIA_OTHER], C.YELLOW), "Skip")

    # Gestion du failed.txt
    if FAILED_URLS:
        save_failed_list(STATE_FILE, FAILED_URLS)
        print(color("[INFO] failed.txt mis à jour\n", C.YELLOW))
    else:
        delete_failed_file_if_exists(STATE_FILE)
        print(color("[INFO] Aucun fail restant → failed.txt supprimé\n", C.GREEN))



if __name__ == "__main__":
    main()

