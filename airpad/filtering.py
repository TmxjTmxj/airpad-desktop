"""Scale-independent relative motion and speed-adaptive One Euro filters.

Both filters use the 1€ formulation from Casiez, Roussel & Vogel, "1€ Filter: A Simple
Speed-Based Low-Pass Filter for Noisy Input in Interactive Systems" (CHI 2012): heavy
smoothing when the signal is slow, almost no lag when it moves.
"""
import math


class OneEuro:
    """Scalar 1€ filter for the pinch readout; gesture edges use raw hysteresis."""

    def __init__(self, min_cutoff=1.0, beta=0.5, derivative_cutoff=1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.derivative_cutoff = derivative_cutoff
        self.reset()

    def reset(self):
        self.value = None
        self.previous = None
        self.t = None
        self.derivative = 0.0

    @staticmethod
    def alpha(cutoff, dt):
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def update(self, value, t):
        if self.value is None or self.t is None:
            self.value, self.previous, self.t = value, value, t
            return value
        dt = t - self.t
        if dt <= 0 or dt > .5:
            # A gap means the old history is irrelevant; restart from the new value.
            self.value, self.previous, self.t = value, value, t
            self.derivative = 0.0
            return value
        raw_derivative = (value - self.previous) / dt
        a_d = self.alpha(self.derivative_cutoff, dt)
        self.derivative += a_d * (raw_derivative - self.derivative)
        cutoff = self.min_cutoff + self.beta * abs(self.derivative)
        a = self.alpha(cutoff, dt)
        self.previous = value
        self.t = t
        self.value += a * (value - self.value)
        return self.value


class RelativeMotion:
    def __init__(self,sensitivity=1000.0,min_cutoff=1.5):
        self.sensitivity=sensitivity
        self.min_cutoff=min_cutoff
        self.reset()

    def reset(self):
        self.previous=None
        self.scale=0.
        self.t=None
        self.q=[0.,0.]
        self.filtered=[0.,0.]
        self.stabilized=[0.,0.]
        self.derivative=[0.,0.]
        self.residual=[0.,0.]
        self.speed=0.

    @staticmethod
    def alpha(cutoff,dt):
        return 1/(1+1/(2*math.pi*cutoff*dt))

    def update(self,point,scale,t):
        if self.previous is None:
            self.previous=point; self.scale=scale; self.t=t
            return (0,0)
        dt=t-self.t
        if dt<=0 or dt>.100001 or scale<=.015:
            self.reset()
            self.previous=point; self.scale=scale; self.t=t
            return (0,0)
        s=(scale+self.scale)/2
        delta=[(point[j]-self.previous[j])/s for j in range(2)]
        self.previous=point;self.scale=scale;self.t=t
        speed=math.hypot(*delta)/dt
        if speed>12:
            self.reset()
            return (0,0)
        a_d=self.alpha(1.0,dt)
        for j in range(2):
            self.derivative[j]+=a_d*(delta[j]/dt-self.derivative[j])
            self.q[j]+=delta[j]
        self.speed=math.hypot(*self.derivative)
        cutoff=self.min_cutoff+1.5*self.speed
        alpha=self.alpha(cutoff,dt)
        for j in range(2):
            self.filtered[j]+=alpha*(self.q[j]-self.filtered[j])
        # A radial, anchored stop zone after smoothing. Unlike a per-frame delta
        # cutoff, it retains tiny continuous motion until it leaves the zone.
        # Units are palm lengths, so camera distance/gain do not change hand tolerance.
        error=[self.filtered[j]-self.stabilized[j] for j in range(2)]
        distance=math.hypot(*error)
        radius=.008/(1+3*self.speed)
        if distance<=radius:
            return (0,0)
        fraction=(distance-radius)/distance
        result=[]
        for j in range(2):
            displacement=error[j]*fraction
            self.stabilized[j]+=displacement
            value=displacement*self.sensitivity+self.residual[j]
            integer=math.trunc(value)
            self.residual[j]=value-integer
            result.append(max(-240,min(240,integer)))
        return tuple(result)
