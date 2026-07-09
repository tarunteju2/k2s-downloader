# K2S Downloader

## Environment
Tested on Python 3.10+.

## Note
For download corruption check to work, you should have ffmpeg available in environmental path.
If proxy discovery is unavailable, the downloader falls back to a direct connection automatically.

## Installation
1. Download the repo
2. pip install -r requirements.txt

## Usage
```
main.py <link> --filename <filename> --split-size 20mb
```

Both `https://k2s.cc/file/...` and `https://keep2share.cc/file/...` links are supported.

## Validation
```
python -m unittest discover -s tests -v
python -m compileall .
```
