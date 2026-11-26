"""Console visibility into synchronous Detour planning; no movement policy."""
from contextlib import contextmanager
from threading import Event, Thread
from time import monotonic


def xyz(point):
    return f'({point.x:.1f},{point.y:.1f},{point.z:.1f})'


@contextmanager
def planning_status(label, log, *, interval=5.0):
    """Report long native queries even while the calling thread is blocked."""
    started=monotonic()
    stop=Event()
    def heartbeat():
        while not stop.wait(interval):
            log(f'Navigation planning: still waiting for {label}; {monotonic()-started:.1f}s elapsed; movement paused')
    log(f'Navigation planning: {label}; movement paused')
    worker=Thread(target=heartbeat,name='navigation-diagnostic',daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join()
        log(f'Navigation planning: {label} returned after {monotonic()-started:.2f}s')


class DetourDiagnostics:
    def __init__(self,log):
        self.log=log
        self.started=monotonic()
        self.attempts=0
        self.rejections={}

    def reject(self,reason):
        self.rejections[reason]=self.rejections.get(reason,0)+1
        self.log(f'Navigation planning: rejected route: {reason}')

    def candidate(self,point,radius):
        self.attempts+=1
        self.log(f'Navigation planning: detour candidate {self.attempts}, radius {radius:.0f}y, XYZ {xyz(point)}')

    def summary(self,accepted):
        details=', '.join(f'{reason}={count}' for reason,count in self.rejections.items()) or 'none'
        self.log(f'Navigation planning: finished in {monotonic()-self.started:.1f}s; '
                 f'{self.attempts} alternate candidates, {accepted} accepted; rejections: {details}')
