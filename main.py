import os
import io
import re
import json
import math
import time
import random
import pathlib
import argparse
import threading
import contextlib
import subprocess
from shutil import which
from typing import Dict, List, Optional

import requests
from tqdm import tqdm

import k2s
from utils import clear_console, get_working_proxies

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36"
)
FILE_URL_RE = re.compile(r"https://(?:k2s\.cc|keep2share\.cc)/file/([^?/]+)(?:[/?].*)?$")
MIN_SPLIT_SIZE = 1024 * 1024 * 20
TMP_DIR = pathlib.Path("tmp")
URLS_CACHE_PATH = pathlib.Path("urls.json")

WORKING_PROXY_LIST = []
PROXIES = []
PROXIES_LOCK = []

URL_LOCKS = None
START_TIME = time.time()

BYTES_PER_SPLIT = 1024 * 1024 * 16
BLOCK_SIZE = 1024 * 32

def parse_size(size: str) -> int:
    units = {
        "": 1,
        "B": 1,
        "KB": 10**3,
        "MB": 10**6,
        "GB": 10**9,
        "TB": 10**12,
        "KIB": 2**10,
        "MIB": 2**20,
        "GIB": 2**30,
        "TIB": 2**40,
    }
    m = re.match(r'^([\d\.]+)\s*([a-zA-Z]{0,3})$', str(size).strip())
    if not m:
        raise ValueError(f"Invalid size value: {size}")

    number, unit = float(m.group(1)), m.group(2).upper()
    if unit not in units:
        raise ValueError(f"Unsupported size unit: {unit}")

    return int(number * units[unit])

def human_readable_bytes(num: int) -> str:
    for x in ['bytes', 'KB', 'MB', 'GB', 'TB']:
        if num < 1024.0:
            return "%3.3f %s" % (num, x)
        num /= 1024.0

    return "%3.3f %s" % (num, "PB")
def extract_file_id(url: str) -> Optional[str]:
    match = FILE_URL_RE.fullmatch(url.strip())
    if not match:
        return None

    return match.group(1)

def buildRange(value: int, numsplits: int) -> Dict:

    range_dict = {}
    for i in range(numsplits):
        range_dict.update({
            str(i): {
                "inUse": False,
                "downloaded": False,
                "range": '%s-%s' % (int(round(1 + i * value/(numsplits*1.0), 0)), int(round(1 + i * value/(numsplits*1.0) + value/(numsplits*1.0)-1, 0))),
                "bytes": (int(round(1 + i * value/(numsplits*1.0) + value/(numsplits*1.0)-1, 0)) - int(round(1 + i * value/(numsplits*1.0),0)) + 1)
            }
        })

    range_dict["0"]["range"] = "0-" + str(int(range_dict["0"]["range"].split("-")[1]))
    range_dict["0"]["bytes"] = int(range_dict["0"]["bytes"]) + 1

    return range_dict


def main(urls: List[str], filename: str) -> None:
    
    if not urls:
        print("Please Enter some url to begin download.")
        return

    headers = {"User-Agent": DEFAULT_USER_AGENT}
    stop = False
    done_count = 0

    sizeInBytes = requests.head(
        urls[-1],
        allow_redirects=True,
        headers=headers,
        timeout=20,
    ).headers.get('Content-Length', None)
    if not sizeInBytes:
        print("Size cannot be determined.")
        return

    print(f"{human_readable_bytes(int(sizeInBytes))} to download.")

    # Split total num bytes into ranges
    splitBy = math.ceil(int(sizeInBytes) / BYTES_PER_SPLIT)
    ranges = buildRange(int(sizeInBytes), splitBy)
    sizePerRange = int(round(1 + 0 * int(sizeInBytes)/(splitBy*1.0) + int(sizeInBytes)/(splitBy*1.0)-1, 0))
    total_iter = tqdm(desc=f"[{done_count}/{len(ranges)}] Downloaded", total=int(sizeInBytes), unit='iB', unit_scale=True, unit_divisor=1024)
    
    def downloadChunk(idx, irange, th_idx):

        nonlocal done_count
        chunk_start_time = time.time()
        total_size_in_bytes= int(sizePerRange)
        tmp_filename = TMP_DIR / f"{filename}.part{str(idx).zfill(len(str(splitBy)))}"
        str_range = "-".join([human_readable_bytes(int(bytes)) for bytes in irange.split('-')])
        f = io.BytesIO()
        proxy_idx = 0

        for i in WORKING_PROXY_LIST:
            if not PROXIES_LOCK[i].locked():
                proxy_idx = i
                break
            else:
                proxy_idx = random.randint(0, len(PROXIES) - 1)

        while PROXIES_LOCK[proxy_idx].locked():
            proxy_idx = random.randint(0, len(PROXIES) - 1)

        PROXIES_LOCK[proxy_idx].acquire()

        if PROXIES[proxy_idx]:
            prox = {'https': f'http://{PROXIES[proxy_idx]}'}
            prefix = f"[{PROXIES[proxy_idx]}]"
        else:
            prox = None
            prefix = "[LOCAL]"
        # progress_bar = tqdm(desc=f"{prefix} {str_range}", total=total_size_in_bytes, unit='iB', unit_scale=True, unit_divisor=1024, leave=False)

        with contextlib.suppress(Exception):
            req = requests.get(
                urls[th_idx],
                headers={"Range": f"bytes={irange}", "User-Agent": headers["User-Agent"]},
                stream=True,
                proxies=prox,
                timeout=20,
            )

            for data in req.iter_content(BLOCK_SIZE):
                if stop: break
                if chunk_start_time + 20 < time.time(): break
                chunk_start_time = time.time()
                # progress_bar.update(len(data))
                total_iter.update(len(data))
                f.write(data)

        if not math.isclose(len(f.getvalue()), ranges[idx]["bytes"], abs_tol=1):
            # progress_bar.close()
            total_iter.update(-len(f.getvalue()))
            ranges[idx]["inUse"] = False
            URL_LOCKS[th_idx].release()
            PROXIES_LOCK[proxy_idx].release()
            return

        with open(tmp_filename, "wb") as fr:
            fr.write(f.getvalue())

        if proxy_idx not in WORKING_PROXY_LIST:
            WORKING_PROXY_LIST.append(proxy_idx)
        # progress_bar.close()
        ranges[idx]["inUse"] = False
        ranges[idx]["downloaded"] = True
        done_count += 1
        total_iter.desc = f"[{done_count}/{len(ranges)}] Downloaded"
        URL_LOCKS[th_idx].release()
        PROXIES_LOCK[proxy_idx].release()

    try:
        while done_count < len(ranges):
            for idx, irange in ranges.items():
                if irange["inUse"] or irange["downloaded"]:
                    continue

                tmp_filename = TMP_DIR / f"{filename}.part{str(idx).zfill(len(str(splitBy)))}"
                if tmp_filename.exists():
                    with open(tmp_filename, "rb") as downloaded_chunk:
                        og_data = downloaded_chunk.read()

                    if math.isclose(len(og_data), ranges[idx]["bytes"], abs_tol=1):
                        if not irange["downloaded"]:
                            total_iter.update(ranges[idx]["bytes"])
                            done_count += 1
                            total_iter.desc = f"[{done_count}/{len(ranges)}] Downloaded"
                            irange["downloaded"] = True
                            continue
                    else:
                        tmp_filename.unlink()

                for th_idx in range(batch_count):
                    if URL_LOCKS[th_idx].locked():
                        continue

                    URL_LOCKS[th_idx].acquire()
                    irange["inUse"] = True
                    threading.Thread(target=downloadChunk, args=(idx, irange["range"], th_idx), daemon=True).start()
                    break

    except KeyboardInterrupt:
        stop = True
        clear_console()
        print("Download Stopped")
        return

    for lock in URL_LOCKS:
        while lock.locked():
            lock.release()

    for lock in PROXIES_LOCK:
        while lock.locked():
            lock.release()

    total_iter.close()
    print("--- %s seconds ---" % int(time.time() - START_TIME))

    if os.path.exists(filename):
        os.remove(filename)

    # Reassemble file in correct order
    with open(filename, 'wb') as fh:
        for idx in range(len(ranges)):
            tmp_filename = TMP_DIR / f"{filename}.part{str(idx).zfill(len(str(splitBy)))}"
            with open(tmp_filename, "rb") as fr:
                fh.write(fr.read())
            tmp_filename.unlink()

    print("Finished Writing file %s" % filename)
    print('File Size: {} bytes'.format(human_readable_bytes(os.path.getsize(filename))))

def check_vid(video_path: pathlib.Path) -> bool:
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "warning",
            "-i",
            str(video_path),
            "-c",
            "copy",
            "-f",
            "null",
            "-",
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    return not bool(result.stderr.strip())


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='K2S Downloader')
    parser.add_argument('url', help='k2s url to download', action='store')
    parser.add_argument('--filename', type=str,
                        help='filename to save as',
                        action='store', dest='filename')
    parser.add_argument('--threads', dest='batch_count', action='store',
                        help='number of connections to use (default 20)', default=20)
    parser.add_argument('--split-size', dest='size', action='store',
                        help='Size to split at (default 20M)', default=1024 * 1024 * 20)

    args = parser.parse_args()

    file_id = extract_file_id(args.url)
    if not file_id:
        print("Invalid URL")
        exit()

    TMP_DIR.mkdir(parents=True, exist_ok=True)

    try:
        split_size = parse_size(args.size)
    except ValueError as exc:
        print(exc)
        exit()

    if split_size < MIN_SPLIT_SIZE:
        print("Split size must be at least 20M")
        exit()

    if not args.filename:
        file_name = k2s.get_name(file_id)
    else:
        file_name = args.filename
    batch_count = int(args.batch_count)
    BYTES_PER_SPLIT = split_size
    PROXIES = get_working_proxies()
    PROXIES_LOCK = [threading.Lock() for _ in range(len(PROXIES))]

    if not URLS_CACHE_PATH.exists():
        with open(URLS_CACHE_PATH, "w") as f:
            json.dump({}, f)

    with open(URLS_CACHE_PATH, "r") as f:
        past_urls = json.load(f)

    urls = []
    if file_id in past_urls:
        urls = past_urls[file_id]

    if len(urls) < batch_count:
        urls = k2s.generate_download_urls(file_id, batch_count)

    past_urls[file_id] = urls
    with open(URLS_CACHE_PATH, "w") as f:
        json.dump(past_urls, f, indent=4)

    URL_LOCKS = [threading.Lock() for _ in range(batch_count)]
    START_TIME = time.time()
    redownloaded = False

    while True:
        main(urls, file_name)
        if which("ffmpeg"):
            if not check_vid(pathlib.Path(file_name)):
                if not redownloaded:
                    print("Video is corrupted. Redownloading with a larger chunk size.")
                    redownloaded = True
                    BYTES_PER_SPLIT *= 2
                    continue
                else:
                    print("Video is still corrupted. Skipping.")
        break
