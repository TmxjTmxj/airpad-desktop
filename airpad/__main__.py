"""AIRPAD GUI and reproducible offline diagnostics."""
import argparse
from dataclasses import asdict
import importlib.metadata
import json
import multiprocessing
import os
from pathlib import Path
import statistics
import sys
import time


def write_report(result, output=None):
    document=json.dumps(result,ensure_ascii=True,indent=2)
    if output:
        target=Path(output);target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(document+'\n',encoding='utf-8')
    if sys.stdout is not None:print(document)


def doctor(output=None):
    from .resources import verify_model,model_path
    versions={}
    for name in ('mediapipe','numpy','opencv-contrib-python','PySide6'):
        try:versions[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:versions[name]=None
    result={'python':sys.version.split()[0],'platform':sys.platform,'dependencies':versions,
            'model_ok':verify_model(),'model_path':str(model_path()),'camera_opened':False}
    write_report(result,output)
    return 0 if result['model_ok'] and all(versions.values()) else 1


def replay(path,experimental=False):
    from .config import Profile
    from .core import GestureEngine
    from .geometry import FingerStability,HandSelector,extract_sample
    from .types import HandSample
    profile=Profile(two_finger_scroll=True,three_finger_enabled=True) if experimental else Profile()
    engine=GestureEngine(profile);selector=HandSelector(profile.dominant_hand)
    # Replay must exercise the same hysteresis the live path uses, or it validates a
    # pipeline the application never runs.
    stability=FingerStability();trace=[]
    width,height=640,480
    with Path(path).open(encoding='utf-8-sig') as stream:
        for line in stream:
            if not line.strip():continue
            row=json.loads(line)
            kind=row.get('type')
            if kind=='header':
                width=int(row.get('width',width));height=int(row.get('height',height))
                continue
            if kind=='lost':
                actions=engine.step(None,float(row['t']))
            elif kind=='landmarks':
                # Observation before the algorithm: run the real gate and selector so a
                # replay validates the recognition chain instead of asserting its output.
                t=float(row['t'])
                hands=[{'landmarks':row['landmarks'],
                        'handedness':row.get('handedness','Unknown'),
                        'handedness_score':row.get('handedness_score',0.)}]
                chosen=selector.select(hands,t);sample=None
                if chosen is not None:
                    sample,_=extract_sample(chosen,t,int(row.get('width',width)),
                                            int(row.get('height',height)),selector.track_id,stability)
                actions=engine.step(sample,t)
            else:
                sample=HandSample(**{k:v for k,v in row.items() if k!='type'})
                actions=engine.step(sample,sample.t)
            trace.append({'t':row['t'],'state':engine.state,'hand':selector.reason,
                          'actions':[asdict(a) for a in actions]})
    print(json.dumps({'trace':trace},ensure_ascii=True))
    return 0


def benchmark():
    from .config import Profile
    from .core import GestureEngine
    from .types import HandSample
    engine=GestureEngine(Profile());durations=[];events=0
    for i in range(10000):
        sample=HandSample(i/30,(.5+(i%60)*.0005,.3),(.5,.6),.2,.8,'open' if i<8 else 'pointer')
        start=time.perf_counter_ns();actions=engine.step(sample,sample.t)
        durations.append((time.perf_counter_ns()-start)/1000);events+=len(actions)
    durations.sort()
    result={'kind':'synthetic core-only replay; NOT camera-to-pointer latency','samples':len(durations),
        'step_us_p50':statistics.median(durations),'step_us_p95':durations[int(len(durations)*.95)],
        'action_count':events,'camera_opened':False,'os_input':False}
    print(json.dumps(result,indent=2))
    return 0


def start_session(controller,args):
    if args.calibrate:
        controller.start_calibration()
        return
    controller.set_practice(args.practice)
    controller.start_camera()
    controller.set_enabled(True)


def run_gui(args):
    from PySide6.QtCore import QLockFile,QTimer
    from PySide6.QtGui import QKeySequence,QShortcut
    from PySide6.QtWidgets import QApplication,QMessageBox
    from .runtime import AirpadController
    from .ui import MainWindow
    app=QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('AIRPAD');app.setOrganizationName('AIRPAD')
    from .resources import data_path
    data_dir=data_path(args.data_dir)
    data_dir.mkdir(parents=True,exist_ok=True)
    lock=QLockFile(str(data_dir/'airpad.lock'))
    if not lock.tryLock(0):
        if args.smoke:
            print('AIRPAD already running',file=sys.stderr);return 2
        QMessageBox.information(None,'AIRPAD','AIRPAD is already running. Open it from the system tray.')
        return 0
    controller=AirpadController(data_dir)
    window=MainWindow(controller)
    pause=QShortcut(QKeySequence('Esc'),window)
    pause.activated.connect(lambda:controller.set_enabled(False))
    app.aboutToQuit.connect(controller.shutdown)
    window.show()

    if args.calibrate or args.practice or not args.no_autostart:
        QTimer.singleShot(150,lambda:start_session(controller,args))
    if args.screenshot:
        def screenshot():
            path=Path(args.screenshot);path.parent.mkdir(parents=True,exist_ok=True)
            window.grab().save(str(path))
        QTimer.singleShot(500,screenshot)
    if args.smoke:QTimer.singleShot(int(args.smoke*1000),app.quit)
    try:return app.exec()
    finally:controller.shutdown();lock.unlock()


def main(argv=None):
    parser=argparse.ArgumentParser(description='AIRPAD - hand tracking that controls the Windows desktop.')
    parser.add_argument('command',nargs='?',default='run',choices=['run','doctor','replay','benchmark','install-model','calibrate','self-check'])
    parser.add_argument('path',nargs='?',help='JSONL input for replay')
    parser.add_argument('--data-dir',help='Local profile and log directory')
    parser.add_argument('--output',help='JSON report path for doctor or self-check (recommended for EXE)')
    parser.add_argument('--practice',action='store_true',help='Start in practice mode: never touches the desktop')
    parser.add_argument('--experimental',action='store_true',
                        help='For replay: enable the opt-in gestures (two-finger scroll, three-finger)')
    parser.add_argument('--no-autostart',action='store_true',help='Do not open the camera or take control automatically')
    parser.add_argument('--start-camera',action='store_true',help='Deprecated alias: the camera starts automatically')
    parser.add_argument('--calibrate',action='store_true',help='Open calibration on launch')
    parser.add_argument('--smoke',type=float,default=0,metavar='SECONDS',help='Close GUI after a bounded smoke test')
    parser.add_argument('--screenshot',help='Explicitly save this app window screenshot')
    args=parser.parse_args(argv)
    try:
        if args.command=='doctor':return doctor(args.output)
        if args.command=='self-check':
            from .self_check import self_check
            from .resources import data_path
            output=args.output or data_path(args.data_dir)/'self-check.json'
            result=self_check();write_report(result,output)
            return 0 if result['ok'] else 1
        if args.command=='replay':
            if not args.path:parser.error('replay requires a JSONL path')
            return replay(args.path,args.experimental)
        if args.command=='benchmark':return benchmark()
        if args.command=='install-model':
            from .resources import install_model
            print(install_model());return 0
        if args.command=='calibrate':args.calibrate=True
        return run_gui(args)
    except Exception as exc:
        if sys.stderr is not None:print(f'AIRPAD: {exc}',file=sys.stderr)
        return 1


if __name__=='__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
