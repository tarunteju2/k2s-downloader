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
from typing import Dict, List

import requests
from tqdm import tqdm

import k2s
from utils import clear_screen, get_working_proxies

WORKING_PROXY_LIST = []
PROXIES = []
PROXIES_LOCK = []

URL_LOCKS = None
START_TIME = time.time()
BATCH_COUNT = 0

BYTES_PER_SPLIT = 1024 * 1024 * 16
BLOCK_SIZE = 1024 * 32
FFMPEG_NULL_OUTPUT = "NUL" if os.name == "nt" else "-"
DOWNLOAD_DESC = "[{done_count}/{total_ranges}] Downloaded"

def parse_size(size: str) -> int:
    units = {
        "B": 1,
        # Keep legacy KB/MB/GB/TB inputs binary-sized for backward compatibility.
        "KB": 2**10,
        "MB": 2**20,
        "GB": 2**30,
        "TB": 2**40,
        "": 1,
        "KIB": 2**10,
        "MIB": 2**20,
        "GIB": 2**30,
        "TIB": 2**40,
    }
    m = re.match(r'^([\d\.]+)\s*([a-zA-Z]{0,3})$', str(size).strip())
    if not m:
        raise ValueError(f"Invalid size: {size}")
    number, unit = float(m.group(1)), m.group(2).upper()
    if unit not in units:
        raise ValueError(f"Unsupported size unit: {unit}")
    return int(number*units[unit])

def human_readable_bytes(num: int) -> str:
    for x in ['bytes', 'KB', 'MB', 'GB', 'TB']:
        if num < 1024.0:
            return "%3.3f %s" % (num, x)
        num /= 1024.0

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

    headers = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36"}
    stop = False
    done_count = 0

    sizeInBytes = requests.head(urls[-1], allow_redirects=True, headers=headers).headers.get('Content-Length', None)
    if not sizeInBytes:
        print("Size cannot be determined.")
        return

    print(f"{human_readable_bytes(int(sizeInBytes))} to download.")

    # Split total num bytes into ranges
    splitBy = math.ceil(int(sizeInBytes) / BYTES_PER_SPLIT)
    ranges = buildRange(int(sizeInBytes), splitBy)
    total_iter = tqdm(
        desc=DOWNLOAD_DESC.format(done_count=done_count, total_ranges=len(ranges)),
        total=int(sizeInBytes),
        unit='iB',
        unit_scale=True,
        unit_divisor=1024,
    )

    def update_progress() -> None:
        total_iter.set_description_str(DOWNLOAD_DESC.format(done_count=done_count, total_ranges=len(ranges)))
    
    def downloadChunk(idx, irange, th_idx):

        nonlocal done_count
        chunk_start_time = time.time()
        tmp_filename = os.path.join("tmp", f"{filename}.part{str(idx).zfill(len(str(splitBy)))}")
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
        else:
            prox = None

        with contextlib.suppress(Exception):
            req = requests.get(
                urls[th_idx],
                headers={"Range": f"bytes={irange}", "User-Agent": headers["User-Agent"]},
                stream=True,
                proxies=prox,
                timeout=20
            )

            for data in req.iter_content(BLOCK_SIZE):
                if stop: break
                if chunk_start_time + 20 < time.time(): break
                chunk_start_time = time.time()
                # progress_bar.update(len(data))
                total_iter.update(len(data))
                f.write(data)

        if not math.isclose(len(f.getvalue()), ranges[idx]["bytes"], abs_tol=1):
            total_iter.update(-len(f.getvalue()))
            ranges[idx]["inUse"] = False
            URL_LOCKS[th_idx].release()
            PROXIES_LOCK[proxy_idx].release()
            return

        with open(tmp_filename, "wb") as fr:
            fr.write(f.getvalue())

        if proxy_idx not in WORKING_PROXY_LIST:
            WORKING_PROXY_LIST.append(proxy_idx)
        ranges[idx]["inUse"] = False
        ranges[idx]["downloaded"] = True
        done_count += 1
        update_progress()
        URL_LOCKS[th_idx].release()
        PROXIES_LOCK[proxy_idx].release()

    try:
        while done_count < len(ranges):
            for idx, irange in ranges.items():
                if irange["inUse"] or irange["downloaded"]:
                    continue

                tmp_filename = os.path.join("tmp", f"{filename}.part{str(idx).zfill(len(str(splitBy)))}")
                if os.path.exists(tmp_filename):
                    og_data = open(tmp_filename, "rb").read()
                    if math.isclose(len(og_data), ranges[idx]["bytes"], abs_tol=1):
                        if not irange["downloaded"]:
                            total_iter.update(ranges[idx]["bytes"])
                            done_count += 1
                            update_progress()
                            irange["downloaded"] = True
                            continue
                    else:
                        os.remove(tmp_filename)

                for th_idx in range(BATCH_COUNT):
                    if URL_LOCKS[th_idx].locked():
                        continue

                    URL_LOCKS[th_idx].acquire()
                    irange["inUse"] = True
                    threading.Thread(target=downloadChunk, args=(idx, irange["range"], th_idx), daemon=True).start()
                    break

    except KeyboardInterrupt:
        stop = True
        clear_screen()
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
            tmp_filename = os.path.join("tmp", f"{filename}.part{str(idx).zfill(len(str(splitBy)))}")
            with open(tmp_filename, "rb") as fr:
                fh.write(fr.read())
            os.remove(tmp_filename)

    print("Finished Writing file %s" % filename)
    print('File Size: {} bytes'.format(human_readable_bytes(os.path.getsize(filename))))

def check_vid(video_path: pathlib.Path) -> bool:
    result = subprocess.run(
        ["ffmpeg", "-v", "warning", "-i", str(video_path), "-c", "copy", "-f", "null", FFMPEG_NULL_OUTPUT],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    return not bool(result.stderr)

def is_supported_url(url: str) -> bool:
    return bool(re.match(r"^https://(k2s\.cc|keep2share\.cc)/file/", url))


def extract_file_id(url: str) -> str:
    file_id = re.findall(r"https://(k2s\.cc|keep2share\.cc)/file/(.*?)(\?|/|$)", url)
    if not file_id:
        raise ValueError("Invalid URL")
    return file_id[0][1]


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='K2S Downloader')
    parser.add_argument('url', help='k2s url to download', action='store')
    parser.add_argument('--filename', type=str,
                        help='filename to save as',
                        action='store', dest='filename')
    parser.add_argument('--threads', dest='batch_count', action='store',
                        help='number of connections to use (default 20)', default=20)
    parser.add_argument('--split-size', dest='size', action='store',
                        help='Size to split at (default 20M)', default=1024 * 1024 * 20)
    return parser


def run_cli(argv=None) -> int:
    global URL_LOCKS, START_TIME, BYTES_PER_SPLIT, PROXIES, PROXIES_LOCK, BATCH_COUNT

    parser = create_parser()
    args = parser.parse_args(argv)

    if not is_supported_url(args.url):
        print("Invalid URL")
        return 1

    try:
        split_size = parse_size(args.size)
        file_id = extract_file_id(args.url)
    except ValueError as exc:
        print(exc)
        return 1

    if split_size < 1024 * 1024 * 20:
        print("Split size must be at least 20M")
        return 1

    pathlib.Path("tmp").mkdir(parents=True, exist_ok=True)

    batch_count = int(args.batch_count)
    BATCH_COUNT = batch_count
    PROXIES = get_working_proxies()
    PROXIES_LOCK = [threading.Lock() for _ in range(len(PROXIES))]

    if not PROXIES:
        PROXIES = [None]
        PROXIES_LOCK = [threading.Lock()]

    if not pathlib.Path("urls.json").exists():
        with open("urls.json", "w") as f:
            json.dump({}, f)

    with open("urls.json", "r") as f:
        past_urls = json.load(f)

    urls = []
    if file_id in past_urls:
        urls = past_urls[file_id]

    if len(urls) < batch_count:
        urls = k2s.generate_download_urls(file_id, batch_count)

    if not args.filename:
        file_name = k2s.get_name(file_id)
    else:
        file_name = args.filename

    past_urls[file_id] = urls
    with open("urls.json", "w") as f:
        json.dump(past_urls, f, indent=4)

    URL_LOCKS = [threading.Lock() for _ in range(batch_count)]
    START_TIME = time.time()
    BYTES_PER_SPLIT = split_size
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

    return 0


if __name__ == '__main__':
    raise SystemExit(run_cli())
