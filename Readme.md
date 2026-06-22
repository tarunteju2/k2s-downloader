# K2S Downloader

## Environment
Tested with Python 3.12.
Supports `k2s.cc` and `keep2share.cc` file URLs.

## Note
For download corruption checks, `ffmpeg` should be available on your `PATH`.

## Installation
1. Download the repo
2. `pip install -r requirements.txt`

## Usage
```
python main.py <link> --filename <filename> --split-size 20MB
```
