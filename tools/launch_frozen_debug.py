"""Debug entry for the frozen build: same as launch_frozen plus autostart tracing."""
import multiprocessing
import sys
import traceback

TRACE = r'D:\codex\反代\.tmp-trace3.txt'

if __name__ == '__main__':
    multiprocessing.freeze_support()
    import airpad.__main__ as m

    original = m.start_session

    def traced_start_session(controller, args):
        with open(TRACE, 'a', encoding='utf-8') as f:
            f.write(f'start_session entered: practice={args.practice} calibrate={args.calibrate}\n')
        try:
            original(controller, args)
            with open(TRACE, 'a', encoding='utf-8') as f:
                f.write('start_session completed normally\n')
        except Exception:
            with open(TRACE, 'a', encoding='utf-8') as f:
                f.write(traceback.format_exc() + '\n')
            raise

    m.start_session = traced_start_session
    raise SystemExit(m.main(sys.argv[1:]))
