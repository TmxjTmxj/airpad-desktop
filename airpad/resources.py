"""Pinned local model asset. Downloads only happen in an explicit setup command."""
from pathlib import Path
import hashlib
import os
import sys

MODEL_SHA256='fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1'
MODEL_URL='https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task'
MAX_MODEL_BYTES=20_000_000


def data_path(explicit=None):
    """Keep installed binaries read-only and preserve explicit profile locations."""
    if explicit:
        return Path(explicit)
    if os.environ.get('AIRPAD_DATA_DIR'):
        return Path(os.environ['AIRPAD_DATA_DIR'])
    if getattr(sys, 'frozen', False):
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'AIRPAD'
    return Path(__file__).resolve().parent.parent / 'runtime-data'


def model_path():
    return Path(__file__).resolve().parent.parent/'models'/'hand_landmarker.task'


def verify_model(path=None):
    path=Path(path) if path is not None else model_path()
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==MODEL_SHA256


def install_model():
    import urllib.request
    import os
    path=model_path()
    if verify_model(path):return path
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.download')
    try:
        with urllib.request.urlopen(MODEL_URL,timeout=60) as response,temporary.open('wb') as output:
            total=0
            while block:=response.read(128*1024):
                total+=len(block)
                if total>MAX_MODEL_BYTES:raise ValueError('Model download exceeds expected size')
                output.write(block)
        if not verify_model(temporary):
            raise ValueError('Model hash mismatch; download rejected')
    except BaseException:
        # Every rejection path - oversize, wrong hash, dropped connection - must leave no
        # partial file behind for the next run to trip over.
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary,path)
    return path
