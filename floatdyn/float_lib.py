from math import atan
import sys
import numpy as np
from scipy.interpolate import interp1d
from scipy.optimize import fsolve, brentq
from netCDF4 import Dataset
import gsw

import matplotlib.pyplot as plt
import cartopy.crs as ccrs


# useful parameters
g=9.81
watth = 1e-6/3.6

#------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------


#
class autonomous_float():

    def __init__(self,**kwargs):
        ''' Float constructor, the float is assumed to be cylinder-shaped

        Parameters
        ----------
            m : float mass [kg]
            r : float radius [m]
            L : float length [m]
            V : float volume [m^3]
            gamma : mechanical compressibility [1/dbar]
            gammaV: compressibility times float volume [m^3/m]
            alpha : thermal compressibility [1/degC]
            temp0: reference temperature used for thermal compressibility [degC]
            a : float added mass [no dimension]
            c0, c1 : float drag parameters [no dimension]

            All parameters are optional, default values are :
            'r': 0.05
            'L': 0.4
            'gamma': 2.e-6
            'alpha': 7.e-5
            'temp0': 15
            'a': 1
            'c0': 0
            'c1': 1
            

        '''
        # default parameters
        params = {'r': 0.05, 'L': 0.4, 'gamma': 2.e-6, 'alpha': 7.e-5, 'temp0': 15., 'a': 1., 'c0': 0., 'c1': 1.}
        params['m']= 1000. * np.pi * params['r']**2 * params['L']
        #
        if 'model' in kwargs:
            if kwargs['model'] == 'ENSTA': #warning: gamma is unknown : it is the one of IFREMER
                params = {'r': 0.06, 'L': 0.5, 'gamma': 9.30e-5, 'alpha': 0., 'temp0': 0., 'a': 1., 'c0': 0., 'c1': 1.}
                params['m'] = 9.045 #1000. * np.pi * params['r'] ** 2 * params['L']
            elif kwargs['model'] == 'IFREMER':
                params = {'r': 0.07, 'L': 0.8278, 'gamma': 3.78039e-06, 'alpha': 0., 'temp0': 0., 'a': 1., 'c0': 0., 'c1': 1.}
                params['m'] = 11.630 #avec 16 piles : 11.630, avec 32 piles : 13.315
        #old compressibility value for ifremer : 3.94819e-06
        #values coming from Paul Troadec's report
        #
        self.model = kwargs['model']
        params.update(kwargs)
        for key,val in params.items():
            setattr(self,key,val)
        # compute the volume of the float:
        if 'V' not in params:
            self.V = np.pi*self.r**2*self.L

        #auxiliary parameters
        self.rho_cte= self.m / self.V #kg.m^-3
        self.gammaV = self.gamma*self.V #m^2

    def __repr__(self):
        strout='Float parameters: \n'
        strout+='  L     = %.2f m      - float length\n'%(self.L)
        strout+='  r     = %.2f m      - float radius\n'%(self.r)
        strout+='  m     = %.2f kg     - float mass\n'%(self.m)
        strout+='  V     = %.2e cm^3   - float volume\n'%(self.V*1.e6)
        strout+='  rho_cte     = %.2e kg.cm^3   - float constant density\n'%(self.m/self.V*1.e6)
        strout+='  gamma = %.2e /dbar  - mechanical compressibility\n'%(self.gamma)
        strout+='  alpha = %.2e /degC  - thermal compressibility\n'%(self.alpha)
        strout+='  temp0 = %.2e  degC  - reference temperature\n'%(self.temp0)
        strout+='  a = %.2e  (no dimension)  - float added mass\n'%(self.a)
        strout+='  c0 = %.2e  (no dimension)  - float drag parameter 0\n'%(self.c0)
        strout+='  c1 = %.2e  (no dimension)  - float drag parameter 1\n'%(self.c1)
        if hasattr(self,'piston'):
            strout+=str(self.piston)
        return strout

    def rho(self, p=None, temp=None, v=None, z=None, waterp=None):
        ''' Returns float density i.e. mass over volume
        '''
        if v is None:
            if hasattr(self,'v'):
                v = self.v
            else:
                v = 0.
        if p is not None and temp is not None:
            return self.m/(self.V*(1.-self.gamma*p+self.alpha*(temp-self.temp0))+v)
        elif z is not None and waterp is not None:
            # assumes thermal equilibrium
            p, tempw = waterp.get_p(z), waterp.get_temp(z)
            return self.rho(p=p, temp=tempw, v=v)
        else:
            print('You need to provide p/temp or z/waterp')

    def volume(self, **kwargs):
        ''' Returns float volume (V+v)
        '''
        return self.m/self.rho(**kwargs)

    def volume4equilibrium(self, p_eq, temp_eq, rho_eq):
        ''' Find volume that needs to be added in order to be at equilibrium
            prescribed pressure, temperature, density
        '''
        v = fsolve(lambda x: rho_eq - self.rho(p=p_eq, temp=temp_eq, v=x),0.)[0]
        return v

    def z4equilibrium(self, waterp):
        ''' Find depth that where float is at equilibrium
        '''
        z = fsolve(lambda z: waterp.get_rho(z) - self.rho(z=z, waterp=waterp), 0.)[0]
        return z

    def adjust_m(self, p_eq, temp_eq, rho_eq):
        ''' Find mass that needs to be added in order to be at equilibrium
            prescribed pressure, temperature, density
        '''
        def func(m):
            self.m = m
            return rho_eq - self.rho(p=p_eq, temp=temp_eq)
        m0=self.m
        self.m = fsolve(func,self.m)[0]
        print('%.1f g were added to the float in order to be at equilibrium at %.0f dbar \n'%((self.m-m0)*1.e3,p_eq))

    def init_piston(self,**kwargs):
        self.piston = piston(self.model, **kwargs)

    def piston_update_vol(self, vol=None):
        if vol is None:
            vol = self.piston.vol
        else:
            self.piston.update_vol(vol)
        self.v = vol

    def set_piston4equilibrium(self, p_eq, temp_eq, rho_eq):
        ''' Adjust piston to be at equilibrium at a given pressure, temperature and density
        '''
        self.v = self.piston.vol
        #
        def func(vol):
            self.piston_update_vol(vol)
            return rho_eq - self.rho(p=p_eq, temp=temp_eq)
        vol = brentq(func, self.piston.vol_min, self.piston.vol_max)
        self.piston_update_vol(vol)
        print('Piston reset : vol=%.1e cm^3  ' % (vol*1e6))
        return vol

    def _f(self, z, waterp, Lv, v=None, w=None):
        ''' Compute the vertical force exterted on the float
        '''
        p, tempw = waterp.get_p(z), waterp.get_temp(z)
        rhow = waterp.get_rho(z)
        rhof = self.rho(p=p,temp=tempw,v=v)
        #
        f = -self.m*g
        f += self.m*rhow/rhof*g # we ignore DwDt terms for now
        #
        if self.c0 != 0:
            print(' !!! linear drag coefficient not implemented yet')
            return None

        if w is None:
            w = self.w
        f += -self.m*self.c1/(2*Lv) * np.abs(w - waterp.detadt) * (w - waterp.detadt) #
        return f

    def _df(self,z,waterp,Lv):
        ''' Compute gradients of the vertical force exterted on the float
        '''
        df1 = ( self._f(z+5.e-2,waterp,Lv) - self._f(z-5.e-2,waterp,Lv) ) /1.e-1
        df2 = ( self._f(z,waterp,Lv,w=self.w+5.e-3) - self._f(z,waterp,Lv,w=self.w-5.e-3) ) /1.e-2
        df3 = ( self._f(z,waterp,Lv,v=self.v+5.e-5) - self._f(z,waterp,Lv,v=self.v-5.e-5) ) /1.e-4
        return df1, df2, df3

    def compute_bounds(self,waterp,zmin,zmax=0.):
        ''' Compute approximate bounds on velocity and acceleration
        '''
        z=zmax
        if hasattr(self,'piston'):
            v=self.piston.vol_max
        else:
            v=None
        fmax=self._f(z,waterp,self.L,v=v,w=0.) # upward force
        #
        z=zmin
        if hasattr(self,'piston'):
            v=self.piston.vol_min
        else:
            v=None
        fmin=self._f(z,waterp,self.L,v=v,w=0.) # downward force
        #
        afmax = np.amax((np.abs(fmax),np.abs(fmin)))
        wmax = np.sqrt( afmax * self.m*2*self.L / self.c1)
        print('Acceleration and velocity bounds (zmin=%.0fm,zmax=%.0fm):' %(zmin,zmax))
        print('fmax/m=%.1e m^2/s, fmin/m= %.1e m^2/s, wmax= %.1f cm/s' %(fmax/self.m,fmin/self.m,wmax*100.) )
        print('For accelerations, equivalent speed reached in 1min:')
        print('  fmax %.1e cm/s, fmin/m= %.1e cm/s' %(fmax/self.m*60.*100.,fmin/self.m*60.*100.) )
        #
        #if hasattr(self,'piston'):
        #    dv = self.piston.dv
        #    p, tempw = waterp.get_p(z), waterp.get_temp(z)
        #    rhow = self.rho(p=p,temp=tempw,v=self.piston.vol_min)
        #    rhof = self.rho(p=p,temp=tempw,v=self.piston.vol_min+dv)
        #    df = self.m*(-1.+rhow/rhof)*g
        #    print('Acceleration after an elementary piston displacement: %.1e m^2/s' %(df[0]/self.m))
        #    print('  corresponding speed and displacement after 1 min: %.1e m/s, %.1e m \n' \
        #          %(df[0]/self.m*60,df[0]/self.m*60**2/2.))

        return fmax, fmin, afmax, wmax

    def init_kalman(self, kalman, w, z, gammaE, Ve, usepiston, t0, verbose):

        dt = 1. #s
        depth_rms = 1e-3 # m
        vel_rms = depth_rms/dt # mm/s
        t2V = self.piston.vol_error  #vol_error = 7.158577010132995e-08
        gamma_alpha_gammaE = 1e-8
        kalman_default = {'dt': dt, 'm': self.m, 'a': self.a,
                          'rho': self.rho_cte,
                          'c1': self.c1, 'L' : self.L,
                          'vol_error': t2V,
                          'gammaV' : self.gammaV,
                          'gamma': np.diag([vel_rms**2, depth_rms**2,
                                            gamma_alpha_gammaE**2, (10.*t2V)**2]),
                          'gamma_alpha': np.diag([(10*vel_rms)**2, depth_rms**2,
                                                  gamma_alpha_gammaE**2, (10.*t2V)**2] ),
                          'gamma_beta': np.diag([depth_rms**2]),
                          'verbose': verbose}

        if type(kalman) is dict:
            kalman_default.update(kalman)

        x0 = [-w, -z, gammaE, Ve]

        self.kalman = Kalman(x0, **kalman_default)
        self.x_kalman = [self.kalman.x_hat]
        self.gamma_kalman =[np.diag(self.kalman.gamma)]
        self.t_kalman = [t0]

    def time_step(self, waterp, T=600., dt_step=1.,
                  z=None, w=None, v=None, Ve=None, t0=0., Lv=None,
                  usepiston=False, z_target=None, gammaE=None,
                  ctrl=None,
                  kalman=None,
                  eta=lambda t: 0.,
                  log=['t','z','w','v','dwdt', 'Ve', 'gammaV', 'u', 'z_kalman',
                       'w_kalman', 'v_kalman', 'Ve_kalman', 'gamma_diag1',
                       'gamma_diag2','gamma_diag3','gamma_diag4', 'dwdt_kalman', 'gammaE_kalman'], dt_store=60.,
                  log_nrg=True, p_float=1.e5,
                  verbose=0,
                  **kwargs):
        ''' Time step the float position given initial conditions

        Parameters
        ----------

        waterp: water profile object
                Contains information about the water profile
        T: float
            Length of the simulation in seconds [s]
        dt_step: float
            Simulation time step [s]
        z: float
            Initial position, 0. at the surface and negative downward [m]
        w: float
            Initial vertical velocity, negative for downward motions [m.s^-1]
        v: float
            Initial volume adjustement (total volume is V+v) [m^3]
        Ve: float
            Volume offset (total volume is Ve+v) [m^3]
        t0: float
            Initial time [t]
        Lv: float
            Drag length scale [m]
        usepiston: boolean, default is False
            Turns piston usage [no dimension]
        z_target: function
            Target trajectory as a function of time [m]
        w_target: function
            Target velocity as a function of time [m.^s-1]
        ctrl: dict
            Contains control parameters
        eta: function
            Isopycnal displacement as a function of time
        log: list of strings or False
            List of variables that will logged
        dt_store: float
            Time interval between log storage
        log_nrg: boolean, default is True
            Turns on/off nrg computation and storage
        p_float: float [Pa]
            Internal float pressure in Pa
        '''
        t=t0
        #
        if z is None:
            if not hasattr(self,'z'):
                z=0.
            else:
                z=self.z
        self.z = z

        #
        if Ve is None:
            if not hasattr(self,'Ve'):
                Ve=0.
            else:
                Ve=self.Ve
        self.Ve = Ve

        #
        if w is None:
            if not hasattr(self,'w'):
                w=0.
            else:
                w=self.w
        self.w = w
        self.dwdt = 0.

        #
        if gammaE is None:
            gammaE = self.gammaV

        #kalman initialisation
        if kalman:
            self.init_kalman(kalman, w, z, gammaE, Ve,
                             usepiston, t0, verbose)

        if usepiston:
            if v is not None:
                self.piston.update_vol(v)
            self.v=self.piston.vol
            u = 0
            if ctrl:
                
                ctrl_default={'dt_ctrl': dt_step, 'dz_nochattering': 0.}
                if ctrl['mode'] == 'sliding':
                    ctrl_default = {'tau': 60., 'mode': 'sliding',
                                    'waterp': waterp, 'Lv': self.L, } #,
                    #                'f': self}
                elif ctrl['mode'] == 'pid':
                    ctrl_default['error'] = 0.
                    ctrl_default['integral'] = 0.
                #
                elif ctrl['mode'] == 'feedback':
                    ctrl_default['tau'] = 3.25  # Set the root of feed-back regulation # s assesed by simulation
                    ctrl_default['nu'] = 0.10*2./np.pi # Set the limit speed : 3cm/s # m.s^-1 assesed by simulation
                    ctrl_default['delta'] = 0.11 #length scale that defines the zone of influence around the target depth, assesed by simulation
                    ctrl_default['gamma'] = self.gamma #mechanical compressibility [1/dbar]
                    ctrl_default['L'] = self.L
                    ctrl_default['c1'] = self.c1
                    ctrl_default['m'] = self.m
                    ctrl_default['gammaV'] = self.gammaV
                    ctrl_default['rho'] = self.rho_cte
                    ctrl_default['a'] = self.a
                    ctrl_default['waterp'] = waterp
                #
                elif ctrl['mode'] == 'kalman_feedback':
                    ctrl_default['tau'] = 3.25  # Set the root of feed-back regulation # s assesed by simulation
                    ctrl_default['nu'] = 0.10*2./np.pi # Set the limit speed : 3cm/s # m.s^-1 assesed by simulation
                    ctrl_default['delta'] = 0.11 #length scale that defines the zone of influence around the target depth, assesed by simulation
                    ctrl_default['kalman'] = self.kalman
                
                #print(ctrl_default)
                ctrl_default.update(ctrl)
                ctrl = ctrl_default
                self.ctrl = ctrl
                for key, val in ctrl_default.items():
                    if key not in ['waterp','f']:
                        print(' ctrl: '+key+' = '+str(val))
                        
        elif v is None:
            if not hasattr(self,'v'):
                self.v=0.
        else:
            self.v=v
        v0 = self.v
        #
        if Lv is None:
            Lv = self.L
        self.Lv = Lv
        #
        if log_nrg:
            if 'nrg' not in log:
                log.append('nrg')
            self.nrg = 0. # Wh
        if log:
            if hasattr(self,'log'):
                delattr(self,'log')
            self.log = logger(log)
        #
        print('Start time stepping for %d min ...'%(T/60.))
        #
        _f=0.
        u = 0 #u initialisation for kalman

        while t<t0+T:
            #
            # get vertical force on float
            waterp.update_eta(eta, t) # update isopycnal displacement
            _f = self._f(self.z, waterp, self.Lv)
            #
            # state estimation starts here
            if kalman:
                if self.kalman and t_modulo_dt(t, self.kalman.dt, dt_step):
                    self.kalman.update_kalman(u, self.v, self.z)
                    #
                    self.x_kalman.append(self.kalman.x_hat)
                    self.gamma_kalman.append(np.diag(self.kalman.gamma))
                    self.t_kalman.append(t)

            #
            # control starts here
            if usepiston and ctrl and t_modulo_dt(t, ctrl['dt_ctrl'], dt_step):
                # activate control only if difference between the target and actual vertical
                # position is more than the dz_nochattering threshold
                if np.abs(self.z-z_target(t)) > ctrl['dz_nochattering']:
                    if verbose>0:
                        print('[-w, -z, -dwdt, gammaV, Ve]',[-self.w, -self.z, -self.dwdt, self.gammaV, self.Ve])
                    u = control(self.z, z_target, ctrl, t=t, w=self.w,
                                dwdt=self.dwdt, v=self.v) #, f=ctrl['f'])
                    #
                    v0 = self.piston.vol
                    self.piston.update(dt_step, u)
                    self.v = self.piston.vol
                # energy integration, 1e4 converts from dbar to Pa
                if log_nrg and (self.v != v0):
                    self.nrg += dt_step * np.abs((waterp.get_p(self.z)*1.e4 - p_float)*u) \
                                *watth /self.piston.efficiency

            # Ve
            #self.Ve = _f/g/self.rho - gamma_e * self.z - self.v
            self.gammaV = self.gamma*self.volume(z=self.z, waterp=waterp) #m^2 #ajout
            self.Ve = _f/(g*self.rho_cte) - self.gammaV * self.z - self.v

            # store
            if log:
                if (dt_store is not None) and t_modulo_dt(t, dt_store, dt_step):
                    self.log.store(t=t, z=self.z, w=self.w, v=self.v, dwdt=_f/self.m, Ve=self.Ve,
                                   gammaV=self.gammaV, u=u)
                    if kalman:
                        self.log.store(z_kalman=self.kalman.x_hat[1],
                                   w_kalman=self.kalman.x_hat[0],gammaE_kalman=self.kalman.x_hat[2],
                                   Ve_kalman=self.kalman.x_hat[3], gamma_diag1=self.kalman.gamma[0,0],
                                   gamma_diag2=self.kalman.gamma[1,1],gamma_diag3=self.kalman.gamma[2,2],
                                   gamma_diag4=self.kalman.gamma[3,3], dwdt_kalman = -self.kalman.A_coeff*\
                                   (self.kalman.x_hat[2] + self.kalman.x_hat[3] -self.kalman.gammaV*self.kalman.x_hat[1]) \
                                   -self.kalman.B_coeff*abs(self.kalman.x_hat[0])*self.kalman.x_hat[0])
                    if log_nrg:
                        self.log.store(nrg=self.nrg)

            # update variables
            self.z += dt_step*self.w
            self.z = np.amin((self.z,0.))
            self.w += dt_step*_f/(1+self.a)/self.m
            self.dwdt = _f/(1+self.a)/self.m
            t+=dt_step
        print('... time stepping done')

#
def control(z, z_target, ctrl, t=None, w=None, f=None, dwdt=None, v=None):
    ''' Implements the control of the float position
    '''
    z_t = z_target(t)
    dz_t = (z_target(t+.05)-z_target(t-.05))/.1
    d2z_t = (z_target(t+.05)-2.*z_target(t)+z_target(t-.05))/.05**2
    #
    if ctrl['mode'] == 'sliding':
        # add tests: if w is None, if f is None ...
        #x1=self.z
        x2 = w
        #x3=self.V+self.v
        #f1=x2
        f2 = f._f(z, ctrl['waterp'], ctrl['Lv'])/f.m
        f3 = ( f.volume(z=z+.5, waterp=ctrl['waterp'])
             - f.volume(z=z-.5, waterp=ctrl['waterp']) )/1. *x2 # dVdz*w
        df1, df2, df3 = f._df(f.z, ctrl['waterp'], ctrl['Lv'])
        df1, df2, df3 = df1/f.m, df2/f.m, df3/f.m
        #
        d3y = ctrl['d3y_ctrl']*control_sliding(z, w, f2, z_t, dz_t, d2z_t, ctrl['tau'])
        u = df1*x2 + df2*f2 + df3*f3 - d3y
        u = -u/df3

    elif ctrl['mode'] == 'pid':
        error = z_t - z
        ctrl['integral'] += error*ctrl['dt_ctrl']
        ctrl['derivative'] = (error - ctrl['error'])/ctrl['dt_ctrl']
        ctrl['error'] = error
        u = ctrl['Kp']*ctrl['error'] + ctrl['Ki']*ctrl['integral'] + ctrl['Kd']*ctrl['derivative']

    elif ctrl['mode'] == 'feedback':
        ctrl['ldb1'] = 2/ctrl['tau'] # /s
        ctrl['ldb2'] = 1/ctrl['tau']**2 # /s^2
        #f2 = f._f(z, ctrl['waterp'], ctrl['L'])/f.m
        u = control_feedback(z, w, dwdt, z_t, ctrl['nu'], ctrl['gammaV'], ctrl['L'], ctrl['c1'],
                             ctrl['m'], ctrl['rho'], ctrl['a'], ctrl['waterp'],
                             ctrl['ldb1'], ctrl['ldb2'], ctrl['delta'])

    elif ctrl['mode'] == 'kalman_feedback':
        u = control_kalman_feedback(z_t, v, ctrl)

    else:
        print('!! mode '+ctrl['mode']+' is not implemented, exiting ...')
        sys.exit()
    return u

def control_sliding(z, dz, d2z, z_t, dz_t, d2z_t, tau_ctrl):
    ''' Several inputs are required:
    (z_target,w_target,dwdt_target) - describes the trajectory
    tau_ctrl  - a time scale of control
    '''
    return np.sign( d2z_t - d2z + 2.*(dz_t-dz)/tau_ctrl + (z_t-z)/tau_ctrl**2 )

def control_feedback(z, dz, d2z, z_t, nu, gammaV, L, c1, m, rho, a, waterp,
                     lbd1, lbd2, delta):

    ''' Control feedback of the float position
    Parameters
    ----------

    z: float
        Position of the float, 0. at the surface and negative downward [m]
    dz: float
        Vertical velocity of the float, negative for downward motions [m.s^-1]
    d2z: float
        Vertical acceleration of the float, negative for downward accelerations [m.s^-2]
    z_target: float
        Target depth [m]
    nu: float
        Travel velocity when the float is far from the target position [m.s^-1]
    gammaV: float
        Float mechanical compressibility x float volume [m^3/dbar]
    L: float
        Float length [m]
    c1: float
        Float drag parameter
    m: float
        Float mass [kg]
    rho: float
        Float constant density [kg.m^-3]
    a: float
        Float added mass [no dimension]
    waterp: water profile object
            Contains information about the water profile
    ldb1: float
        float control parameter 1 [s^-1]
    ldb2: float
        float control parameter 2 [s^-2]
    delta: float
        length scale that defines the zone of influence around the target depth [m]
    '''
    A = g*rho/((a+1)*m)
    B = c1/(2*L*(1+a))
    x1 = -dz
    dx1 = -d2z
    x2 = -z
    x2bar = -z_t
    e = x2bar - x2
    D = 1 + (e**2)/(delta**2)
    y = x1 - nu*np.arctan(e/delta)
    dy = dx1 + nu*x1/(delta*D)

    if dz < 0:
        return (1/A)*(lbd1*dy + lbd2*y\
               + nu/delta*(dx1*D + 2*e*x1**2/delta**2)/(D**2)\
               + 2*B*x1*dx1) + gammaV*x1
    else: #dz >= 0 not differentiable at value 0 : critical value
        return (1/A)*(lbd1*dy + lbd2*y\
               + nu/delta*(dx1*D + 2*e*x1**2/delta**2)/(D**2)\
               - 2*B*x1*dx1) + gammaV*x1
#



def control_kalman_feedback(depth_target, v, ctrl):

    ''' Control feedback of the float position with kalman filter
    Parameters
    ----------

    z: float
        Position of the float, 0. at the surface and negative downward [m]
    '''
    kalman = ctrl['kalman']


    x_control = kalman.x_hat

    return kalman.control(x_control, v, depth_target, ctrl)


def compute_gamma(R,t,material=None,E=None,mu=.35):
    ''' Compute the compressibility to pressure of a cylinder

    Parameters
    ----------
    R: float, [m]
        cylinder radius
    t: float, [m]
        cylinder thickness
    E: float, [GPa]
        Young's modulus
    mu: float, []
        Poisson ratio

    Returns
    -------
    gamma: float, [1/dbar]
        approximate compressibility of the float

    '''
    pmat = {'glass': {'E': 90., 'mu': .25}, 'aluminium': {'E': 70., 'mu': .35},
            'pom': {'E': 3.5, 'mu': .35}, 'polycarbonat':  {'E': 2.2, 'mu': .37}}
    if material is not None:
        if material in pmat:
            E = pmat[material]['E']
            mu = pmat[material]['mu']
        else:
            print('material not in our database')
            sys.exit()
    elif E is None:
        print('You need to provide material or E !')
        sys.exit()
    # convert E to dbar
    E=E*1.e5
    return R*(6.-7.*mu)/2./E/t


# ------------------------------------------------------------------------------------------------------------
# ------------------------------------------------------------------------------------------------------------


class logger():
    ''' Store a log of the float trajectory

    Parameters
    ----------
    var: list of strings
        List containing the name of variables that will be logged

    '''

    def __init__(self, var):
        self.var = var
        for item in var:
            setattr(self, item, np.array([]))

    def store(self, **kwargs):
        ''' Appends variables to the logger database:

        Usage:

        log.store(t=10., v=1.)

        The above line will append values 10. and 1. to variables t and v respectively

        '''
        for item in self.var:
            if item in kwargs:
                setattr(self, item, np.hstack((getattr(self, item), kwargs[item])))

# utils
def plot_float_density(z, f, waterp, mid=False):
    ''' Plot float density with respect to that of water profile

    Parameters
    ----------
    z: ndarray
        depth grid
    f: float object
    waterp: water profile object
    mid: boolean, True for curve at piston mid displacement
    '''
    #
    rho_w, p, temp = waterp.get_rho(z), waterp.get_p(z), waterp.get_temp(z)
    #
    plt.figure()
    ax = plt.subplot(111)
    #
    #iz = np.argmin(np.abs(z+500))
    rho_f = f.rho(p=p, temp=temp, v=0.)
    rho_f_vmax=f.rho(p=p, temp=temp, v=f.piston.vol_max)
    rho_f_vmin=f.rho(p=p, temp=temp, v=f.piston.vol_min)
    #
    ax.fill_betweenx(z, rho_f_vmax, rho_w, where=rho_f_vmax>=rho_w, facecolor='red', interpolate=True)
    ax.plot(rho_w, z, 'b', label='rho water')
    ax.plot(rho_f_vmax, z, '-+', color='orange', label='rho float vmax', markevery=10)
    ax.plot(rho_f_vmin, z, '--', color='orange', label='rho float vmin')
    if mid:
        #rho_f_vmid=f.rho(p=p, temp=temp, v=(f.piston.vol_max+f.piston.vol_min)*.5)
        rho_f_vmid=f.rho(p=p, temp=temp, v=mid)
        ax.plot(rho_f_vmid, z, '--', color='grey', label='rho float vmid')
    ax.legend()
    ax.set_xlabel('[kg/m^3]')
    ax.set_ylim((np.amin(z),0.))
    ax.set_ylabel('z [m]')
    ax.grid()
    iz = np.argmin(np.abs(z))
    ax.set_title('extra mass @surface: %.1f g' %( ( (f.V+f.piston.vol_max) * rho_w[iz] - f.m)*1e3 ) )

#
def plot_float_volume(z, f, waterp):
    ''' Plot float volume with respect to depth

    Parameters
    ----------
    z: ndarray
        depth grid
    f: float object
    waterp: water profile object

    '''
    #
    rho_w, p, temp = waterp.get_rho(z), waterp.get_p(z), waterp.get_temp(z)
    #
    plt.figure()
    ax = plt.subplot(111)
    #
    #iz = np.argmin(np.abs(z+500))
    v = f.volume(p=p, temp=temp, v=0.)
    vmax = f.volume(p=p, temp=temp, v=f.piston.vol_max)
    vmin = f.volume(p=p, temp=temp, v=f.piston.vol_min)
    #
    ax.plot(vmax*1e6, z, '-+', color='orange', label='vmax float', markevery=10)
    ax.plot(vmin*1e6, z, '--', color='orange', label='vmin float')
    ax.legend()
    ax.set_xlabel('[cm^3]')
    ax.set_ylim((np.amin(z),0.))
    ax.set_ylabel('z [m]')
    ax.grid()

#
def plot_log(f, z_target=None, eta=None, title=None):
    log = f.log
    t = log.t
    #
    if hasattr(log,'nrg'):
        # extrapolate to a 30d long simulation
        nrg = (log.nrg[-1]-log.nrg[0])/(t[-1]-t[0])
        print( 'Extrapolated energy conssumption: %.1f Wh/day = %.1f Wh/30day'
              %( nrg*86400, nrg*86400*30. ))
    #
    plt.figure(figsize=(12, 10))
    #
    ax = plt.subplot(321)
    ax.plot(t / 60., log.z, label='z')
    if z_target is not None:
        ax.plot(t / 60., z_target(t), color='r', label='target')
        if eta is not None:
            ax.plot(t / 60., z_target(t) + eta(t), color='green', label='target+eta')
    ax.legend(loc=0)
    ax.set_ylabel('z [m]')
    if title is not None:
        ax.set_title(title)
    ax.grid()
    #
    ax = plt.subplot(322)
    if z_target is not None:
        if hasattr(f, 'ctrl'):
            # (x,y) # width # height
            ax.fill_between(t / 60., t * 0. - f.ctrl['dz_nochattering'], t * 0. + f.ctrl['dz_nochattering'],
                            facecolor='orange', alpha=.5)
        ax.plot(t / 60., log.z - z_target(t), label='z-ztarget')
        ax.legend()
        ax.set_ylabel('[m]')
        ax.set_ylim([-2., 2.])
        if title is not None:
            ax.set_title(title)
        ax.grid()
        ax.yaxis.set_label_position('right')
        ax.yaxis.tick_right()
    #
    ax = plt.subplot(323, sharex=ax)
    ax.plot(t / 60., log.w * 1.e2, label='dzdt')
    ax.legend()
    ax.set_ylabel('[cm/s]')
    ax.grid()
    #
    ax = plt.subplot(324)
    ax.plot(t / 60., log.v * 1.e6, '-', label='v')
    # ax.axhline(f.piston.dv*1.e6,ls='--',color='k')
    ax.axhline(f.piston.vol_min * 1.e6, ls='--', color='k')
    ax.axhline(f.piston.vol_max * 1.e6, ls='--', color='k')
    ax.legend()
    ax.set_ylabel('[cm^3]')
    ax.grid()
    ax.yaxis.set_label_position('right')
    ax.yaxis.tick_right()
    #
    ax = plt.subplot(325, sharex=ax)
    ax.plot(t / 60., log.dwdt, label='d2zdt2')
    ax.legend()
    ax.set_xlabel('t [min]')
    ax.set_ylabel('[m/s^2]')
    ax.grid()
    #
    if hasattr(log,'nrg'):
        ax = plt.subplot(326, sharex=ax)
        ax.plot(t / 60., log.nrg, label='nrg')
        ax.legend()
        ax.set_xlabel('t [min]')
        ax.set_ylabel('[Wh]')
        ax.grid()
        ax.yaxis.set_label_position('right')
        ax.yaxis.tick_right()


# build scenarios
def descent(Tmax, zt, f, waterp, zstart = 0):
    ''' Contruct trajectory of a descent to some depth
    Parameters
    ----------
    Tmax: float
        Time length of the trajectory in SECONDS
    zt: target depth level
    f: float object
        Used to compute maximum accelerations
    waterp: water profile object
        Used to compute maximum accelerations

    '''
    # compute bounds on motions
    fmax, fmin, afmax, wmax = f.compute_bounds(waterp,-500.)
    # build time line
    t = np.arange(0.,Tmax,1.)
    # build trajectory
    #z_target = np.zeros_like(t)
    dzdt_target = -t*afmax/2./f.m
    dzdt_target[np.where(-dzdt_target>wmax)]=-wmax
    z_target = zstart + np.cumsum(dzdt_target*1.)
    z_target[np.where(z_target<zt)] = zt

    # convert to callable function
    return interp1d(t, z_target, kind='linear', fill_value='extrapolate')


#------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------


class piston():
    ''' Piston object, facilitate float buoyancy control
    '''

    def __init__(self,model, **kwargs):
        """ Piston object

        Parameters
        ----------
        r: float [m]
            piston radius
        phi: float [rad]
            angle of rotation
        d: float [m]
            current piston displacement
        vol: float [m^3]
            current volume
            vol = d x pi x r^2
        omega: float [rad/s]
            current rotation rate, omega=dphi/dt
        lead: float [m]
            screw lead (i.e. displacement after one screw revolution)
            d = phi/2/pi x lead
        tick_per_turn: [no dimension]
            number of notches on the thumbwheel of the piston
        d_increment: [m]
            smallest variation of translation motion for the piston
        increment_error: [no dimension]
            coefficient measuring the accuracy on the smallest variation of
            translation motion for the piston (coefficient >= 1)
        vol_error: [m^3]
            smallest variation of volume possible for the piston
        phi_max: float [rad]
            maximum angle of rotation
        phi_min: float [rad]
            minimum angle of rotation
        d_max: float [m]
            screw displacement when piston is fully out
        d_min: float [m]
            screw displacement when piston is fully in
        vol_max: float [m^3]
            volume when piston is fully out
        vol_min: float [m^3]
            volume when piston is fully in
        omega_max: float [rad/s]
            maximum rotation rate
        omega_min: float [rad/s]
            minimum rotation rate
        efficiency: float [<1]
            Piston efficiency, i.e. mechanical work produced over electrical work supplied

        """
        
        
        # default parameters
        params = {'r': 0.025, 'phi': 0., 'd': 0., 'vol': 0., 'omega': 0., 'lead': 0.00175, 'tick_per_turn': 48, \
          'phi_min': 0., 'd_min': 0., 'd_max': 0.07, 'vol_max': 1.718e-4,'vol_min': 0., \
          'omega_max': 60./48*2.*np.pi, 'omega_min': 0.,
          'efficiency':.1}
        
        
        
        if model == 'ENSTA':
            # default parameters: ENSTA float
            params = {'r': 0.025, 'phi': 0., 'd': 0., 'vol': 0., 'omega': 0., 'lead': 0.00175, 'tick_per_turn': 48, \
                      'phi_min': 0., 'd_min': 0., 'd_max': 0.07, 'vol_max': 1.718e-4,'vol_min': 0., \
                      'omega_max': 60./48*2.*np.pi, 'omega_min': 0.,
                      'efficiency':.1, 'increment_error' : 1}
            self.d_increment = params['lead']/params['tick_per_turn']
        
        elif model == 'IFREMER':
            # default parameters: IFREMER float
            params = {'r': 0.0195/2, 'phi': 0., 'd': 0., 'vol': 0., 'omega': 0., 'lead': 1, \
                      'phi_min': 0., 'd_min': 0., 'd_max': 0.090, 'vol_max': 2.688e-5,'vol_min': 0., \
                      'translation_max': 0.12/5600.*225., 'translation_min': 0.12/5600.*10.,
                      'efficiency':.1, 'd_increment' : 0.12/5600., 'increment_error' : 10}

            #translation_max = d_increment*(225 pulses par seconde)  (vitesse de translation max en m/s)
            #translation_min fixe arbitrairement pour l'instant
            
            #d_increment le 4 vient du facteur 4 de la roue codeuse de thomas
            #d_increment = 0.12/5600 ou 0.090/4200 selon la prise en compte ou non du gros piston

            #dmax = 0.102 ancienne valeur pour IFREMER
            
            #verifier si importance lead et angles lors de la regulation, ecraser parametres redondants
            #vol_max = 0.090*np.pi*(0.0195/2)**2+0.030*np.pi*(0.080/2)**2 = 1.777e-4
       
        
	#48 encoches
	#vitesse max de 60 encoches par seconde
        #
        params.update(kwargs)
        for key,val in params.items():
            setattr(self,key,val)
        # assumes here volumes are given
        #if 'd_min' not in kwargs:
        #    self.d_min = self.vol2d(self.vol_min)
        # (useless as will reset d_min to 0.)
        if 'vol_max' in params:
            if 'd_max' in params:
                self.d_max=params['d_max']
                self.vol_min = self.d2vol_no_volmin(self.d_min)

            self.d_max = self.vol2d(self.vol_max)
            print('Piston max displacement set from max volume')
        elif 'd_max' in params:
            self.vol_max = self.d2vol(self.d_max)
            print('Piston max volume set from max displacement')
        else:
            print('You need to provide d_max or vol_max')
            sys.exit()
        if 'translation_max' in params:
            self.omega_max = params['translation_max']*2.*np.pi/self.lead
        if 'translation_min' in params:
            self.omega_min = params['translation_min']*2.*np.pi/self.lead
        #
        self.phi_max = self.d2phi(self.d_max)
        self.update_dvdt()
        self.vol_error = self.d_increment*((self.r)**2)*np.pi*self.increment_error

    def __repr__(self):
        strout='Piston parameters and state: \n'
        strout+='  r     = %.2f mm        - piston radius\n'%(self.r*1.e3)
        strout+='  phi   = %.2f rad       - present angle of rotation\n'%(self.phi)
        strout+='  d     = %.2f mm        - present piston displacement\n'%(self.d*1.e3)
        strout+='  vol   = %.2f cm^3      - present volume addition\n'%(self.vol*1.e6)
        strout+='  lead  = %.2f cm        - screw lead\n'%(self.lead*1.e2)
        #strout+='  tick_per_turn  = %.2f no dimension        - number of notches on the thumbwheel of the piston\n'%(self.tick_per_turn)
        strout+='  d_increment  = %.2f m        - smallest variation of translation motion for the piston\n'%(self.d_increment)
        strout+='  vol_error  = %.2e m^3        - smallest variation of volume possible for the piston\n'%(self.vol_error)
        strout+='  phi_max = %.2f deg     - maximum rotation\n'%(self.phi_max*1.e2)
        strout+='  phi_min = %.2f deg     - minimum rotation\n'%(self.phi_min*1.e2)
        strout+='  d_max = %.2f mm        - maximum piston displacement\n'%(self.d_max*1.e3)
        strout+='  d_min = %.2f mm        - minimum piston displacement\n'%(self.d_min*1.e3)
        strout+='  vol_min = %.2f cm^3    - min volume displaced\n'%(self.vol_min*1.e6)
        strout+='  vol_max = %.2f cm^3    - max volume displaced\n'%(self.vol_max*1.e6)
        strout+='  omega_max = %.2f deg/s - maximum rotation rate\n'%(self.omega_max*180./np.pi)
        strout+='  omega_min = %.2f deg/s - minimum rotation rate\n'%(self.omega_min*180./np.pi)
        strout+='  efficiency = %.2f - mechanical work produced / electrical work supplied\n'%(self.efficiency)
        return strout

#------------------------------------------- update methods -----------------------------------------------

    def update(self, dt, dvdt):
        """ Update piston position given time interval and desired volume change

        Parameters
        ----------
        dt: float
            time interval
        dvdt: float
            desired volume change
        """
        omega=self.dvdt2omega(dvdt)
        self.update_omega(omega)
        self.update_dvdt()
        self.update_phi(dt)

    def update_phi(self,dt):
        self.phi+=self.omega*dt
        self._checkbounds()
        self._bcast_phi()

    def update_vol(self,vol):
        self.phi = self.vol2phi(vol)
        self._checkbounds()
        self._bcast_phi()

    def update_d(self,d):
        self.phi = self.d2phi(d)
        self._checkbounds()
        self._bcast_phi()

    def update_omega(self,omega):
        if np.abs(omega)<self.omega_min:
            self.omega=0.
        else:
            self.omega=np.sign(omega)*np.amin([np.abs(omega),self.omega_max])

    def update_dvdt(self):
        self.dvdt = self.omega2dvdt(self.omega)

    def _bcast_phi(self):
        self.d = self.phi2d(self.phi)
        self.vol = self.phi2vol(self.phi)

#------------------------------------------- conversion methods -----------------------------------------------

    def omega2dvdt(self,omega):
        # /np.pi*np.pi has been simplified
        #to compute omega for the ENSTA float:
        #the motor of the piston needs 48 notches to complete a full rotation
        #it can reach until 30 rotations a seconde
        #so omega = 2*pi*30/48 = 3.9 rad/s
        return omega*self.lead/2.*self.r**2

    def dvdt2omega(self,dvdt):
        # /np.pi*np.pi has been simplified
        return dvdt/(self.lead/2.*self.r**2)

    def phi2d(self,phi):
        return self.d_min+(phi-self.phi_min)/2./np.pi*self.lead

    def phi2vol(self,phi):
        return self.d2vol(self.phi2d(phi))

    def d2phi(self,d):
        return self.phi_min+(d-self.d_min)*2.*np.pi/self.lead

    def d2vol(self,d):
        return self.vol_min+(d-self.d_min)*np.pi*self.r**2

    def d2vol_no_volmin(self,d):
        return self.vol_max+(d-self.d_max)*np.pi*self.r**2

    def vol2d(self,vol):
        return self.d_min+(vol-self.vol_min)/(np.pi*self.r**2)

    def vol2phi(self,vol):
        return self.d2phi(self.vol2d(vol))

    def _checkbounds(self):
        self.phi = np.amin([np.amax([self.phi,self.phi_min]),self.phi_max])

#------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------

class Kalman(object):
    ''' Kalman filter for float state estimation
    '''

    def __init__(self, x0, **params):

        for key,val in params.items():
            setattr(self,key,val)

        self.x_hat = np.array(x0)
        #self.u = 0

        self.A_coeff = g*self.rho/((self.a+1)*self.m)
        self.B_coeff = self.c1/(2*self.L*(1+self.a))

        self.A = self.dt * \
                 np.array([[-self.B_coeff*abs(self.x_hat[0]), self.A_coeff*self.x_hat[2], self.A_coeff*self.x_hat[1], -self.A_coeff],
                           [1., 0., 0, 0],
                           [0, 0, 0., 0],
                           [0, 0, 0, 0.]])
        self.A += np.eye(4)
        self.C = np.array([[0, 1, 0, 0.]])

    def gen_obs(self, z, scale = 1.0):
        # build observations
        y_depth = -z + np.random.normal(loc=0.0,
                    scale=np.sqrt(self.gamma_beta[0,0]))
        return [y_depth]

    def update_kalman(self, u, v, z):
        # update state
        self.A[0,0] = 1 - self.dt*self.B_coeff*np.abs(self.x_hat[0])
        self.A[0,1] = 1 + self.dt*self.A_coeff*self.x_hat[2]
        self.A[0,2] = 1 + self.dt*self.A_coeff*self.x_hat[1]
        y = self.gen_obs(z)
        if self.verbose>0:
            print("x0 initial", self.x_hat)
        (self.x_hat, self.gamma) = self.kalman(self.x_hat, self.gamma, u, v, y,
                                              self.A)
        if self.verbose>0:
            print("x0 iteration", self.x_hat)
            print('x_hat', self.x_hat)
            print('u', u)
            print('z', z)
            print('gamma', self.gamma)


    def kalman(self,x0,gamma0,u, v, y, A):
        xup,Gup = self.kalman_correc(x0, gamma0, y)
        x1,gamma1=self.kalman_predict(xup, Gup, u, v, A)
        return x1, gamma1

    def kalman_predict(self, xup, Gup, u, v, A):
        gamma1 = (A @ Gup @ A.T)
        gamma1 += self.gamma_alpha
        x1 = xup + self.f(xup, u, v)*self.dt
        return x1, gamma1

    def kalman_correc(self,x0,gamma0,y):
        C = self.C
        if self.verbose>0:
            print(C.shape, self.gamma_beta.shape, gamma0.shape)
        S = C @ gamma0 @ C.T + self.gamma_beta
        K = gamma0 @ C.T @ np.linalg.inv(S)
        ytilde = np.array(y) - C @ x0
        Gup = (np.eye(len(x0))- K @ C) @ gamma0
        xup = x0 + K@ytilde
        #
        self.S = S
        self.K = K
        self.ytilde = ytilde
        #
        if self.verbose>0:
            print('K', K)
            print('ytilde', ytilde)
        return xup, Gup

    def f(self,x, u, v):
        dx = np.array(x)
        dx[0] = -self.A_coeff*(x[3] - x[2]*x[1] + v) \
                -self.B_coeff*x[0]*np.abs(x[0])
        dx[1] = x[0]
        dx[2] = 0.0
        dx[3] = 0.0
        return dx

    def control(self, x, v, depth_target, ctrl):

        l1 = 2/ctrl['tau'] # /s
        l2 = 1/ctrl['tau']**2 # /s^2
        nu = ctrl['nu'] # Set the limit speed : 3cm/s # m.s^-1 assesed by simulation
        delta = ctrl['delta'] #length scale that defines the zone of influence around the target depth, assesed by simulation


        e = -depth_target - x[1]
        y = x[0] - nu*atan(e/delta)
        
        dx1 = -self.A_coeff*(x[3] - x[2]*x[1] + v) \
              -self.B_coeff*x[0]*np.abs(x[0])
              
    
        D = 1. + (e/delta)**2
        dy = dx1 + nu*x[0]/(delta*D)

        if x[0] > 0:
            return (1/self.A_coeff)*(l1*dy + l2*y \
                    + nu/delta*(dx1*D + 2*e*x[0]**2/delta**2)/(D**2) \
                    + 2*self.B_coeff*x[0]*dx1) + x[2]*x[0]
            
            
        else:
            return (1/self.A_coeff)*(l1*dy + l2*y \
                    + nu/delta*(dx1*D + 2*e*x[0]**2/delta**2)/(D**2) \
                    - 2*self.B_coeff*x[0]*dx1) + x[2]*x[0]

#------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------


#
class waterp():
    ''' Data holder for a water column based on either:
     - in situ temperature, salinity, pressure profiles
     - the World Ocean Atlas (WOA) climatology, see:
        https://www.nodc.noaa.gov/OC5/woa18/

    Should base this on pandas !!

    Parameters
    ----------
    pressure: np.ndarray, optional
        pressure in dbar
    temperature: np.ndarray, optional
        in situ temperature in degC
    salinity: np.ndarray, optional
        salinity in PSU
    lon: float, optional
        longitude of the selected location
    lat: float, optional
        latitude of the selected location

    '''

    def __init__(self, pressure=None, temperature=None, salinity=None,
                       lon=None, lat=None, name=None):

        self._pts, self._woa = False, False

        if all([pressure, temperature, salinity, lon, lat]):
            self._load_from_pts(pressure, temperature, salinity,
                                lon, lat, name)
        elif all([lon ,lat]):
            self._load_from_woa(lon,lat,name)
        else:
            print('Inputs missing')

    def _load_from_pts(self, pressure, temperature, salinity, lon, lat,
                       name):
        self._pts=True
        #
        self.lon, self.lat = lon, lat
        #
        self.p = pressure
        self.z = gsw.z_from_p(self.p, self.lat)
        #
        self.temp, self.s = temperature, salinity
        # derive absolute salinity and conservative temperature
        self.SA = gsw.SA_from_SP(self.s, self.p, self.lon, self.lat)
        self.CT = gsw.CT_from_t(self.SA, self.temp, self.p)
        # isopycnal displacement and velocity
        self.eta = 0.
        self.detadt = 0.
        #
        if name is None:
            self.name = 'Provided water profile at lon=%.0f, lat=%.0f'%(self.lon,self.lat)

    def _load_from_woa(self, lon, lat, name):
        self._woa=True
        #
        self._tfile = 'woa18_A5B7_t00_01.nc'
        nc = Dataset(self._tfile,'r')
        #
        glon = nc.variables['lon'][:]
        glat = nc.variables['lat'][:]
        ilon = np.argmin(np.abs(lon-glon))
        ilat = np.argmin(np.abs(lat-glat))
        self.lon = glon[ilon]
        self.lat = glat[ilat]
        #
        self.z = -nc.variables['depth'][:].data
        self.p = gsw.p_from_z(self.z,self.lat)
        #
        self.temp = nc.variables['t_an'][0,:,ilat,ilon]
        nc.close()
        #
        self._sfile = 'woa18_A5B7_s00_01.nc'
        nc = Dataset(self._sfile,'r')
        self.s = nc.variables['s_an'][0,:,ilat,ilon]
        nc.close()
        # derive absolute salinity and conservative temperature
        self.SA = gsw.SA_from_SP(self.s, self.p, self.lon, self.lat)
        self.CT = gsw.CT_from_t(self.SA, self.temp, self.p)
        # isopycnal displacement and velocity
        self.eta = 0.
        self.detadt = 0.
        #
        if name is None:
            self.name = 'WOA water profile at lon=%.0f, lat=%.0f'%(self.lon,self.lat)


    def show_on_map(self):
        if self._woa:
            nc = Dataset(self._tfile,'r')
            glon = nc.variables['lon'][:]
            glat = nc.variables['lat'][:]
            temps = nc.variables['t_an'][0,0,:,:]
            nc.close()
            #
            crs=ccrs.PlateCarree()
            plt.figure(figsize=(10, 5))
            ax = plt.axes(projection=crs)
            hdl = ax.pcolormesh(glon,glat,temps,transform = crs,cmap=plt.get_cmap('CMRmap_r'))
            ax.plot(self.lon,self.lat,'*',markersize=10,markerfacecolor='CadetBlue',markeredgecolor='w',transform=crs)
            ax.coastlines(resolution='110m')
            ax.gridlines()
            plt.colorbar(hdl,ax=ax)
            ax.set_title('sea surface temperature [degC]')
            plt.show()
        else:
            print('No map to show')

    def __repr__(self):
        plt.figure(figsize=(7,5))
        ax = plt.subplot(121)
        ax.plot(self.get_temp(self.z),self.z,'k')
        ax.set_ylabel('z [m]')
        ax.set_title('in situ temperature [degC]')
        plt.grid()
        ax = plt.subplot(122)
        ax.plot(self.get_s(self.z),self.z,'k')
        ax.set_yticklabels([])
        #ax.set_ylabel('z [m]')
        ax.set_title('practical salinity [psu]')
        plt.grid()
        return self.name

    def get_temp(self,z):
        ''' get in situ temperature
        '''
        #return interp(self.z, self.temp, z)
        SA = interp(self.z, self.SA, z-self.eta)
        CT = interp(self.z, self.CT, z-self.eta)
        p = self.get_p(z)
        return gsw.conversions.t_from_CT(SA,CT,p)

    def get_s(self, z):
        ''' get practical salinity
        '''
        #return interp(self.z, self.s, z-self.eta)
        SA = interp(self.z, self.SA, z-self.eta)
        CT = interp(self.z, self.CT, z-self.eta)
        p = self.get_p(z)
        return gsw.conversions.SP_from_SA(SA, p, self.lon, self.lat)

    def get_p(self, z):
        ''' get pressure
        '''
        return interp(self.z, self.p, z)

    def get_theta(self, z):
        ''' get potential temperature
        '''
        SA = interp(self.z, self.SA, z-self.eta)
        CT = interp(self.z, self.CT, z-self.eta)
        return gsw.conversions.pt_from_CT(SA,CT)

    def get_rho(self, z, ignore_temp=False):
        p = self.get_p(z)
        SA = interp(self.z, self.SA, z-self.eta)
        CT = interp(self.z, self.CT, z-self.eta)
        if ignore_temp:
            CT[:]=self.CT[0]
            print('Uses a uniform conservative temperature in water density computation, CT= %.1f degC' %self.CT[0])
        return gsw.density.rho(SA, CT, p)

    def update_eta(self, eta, t):
        ''' Update isopycnal diplacement and water velocity given a function
        for isopycnal displacement and time

        Parameters
        ----------
        eta: func
            Isopycnal as a function time
        t: float
            Time in seconds
        '''
        self.eta = eta(t)
        self.detadt = (eta(t+.1)-eta(t-.1))/.2


# ------------------------------------------------------------------------------------------------------------
# ------------------------------------------------------------------------------------------------------------
# utils functions

#
def t_modulo_dt(t, dt, dt_step):
    threshold = 0.25 * dt_step / dt
    if np.abs(t / dt - np.rint(t / dt)) < threshold:
        return True
    else:
        return False

#
def interp(z_in, v_in, z_out):
    return interp1d(z_in, v_in, kind='linear', fill_value='extrapolate')(z_out)




#Functions necesary to estimate parameters for feedback regulation


def omega2dvdt(omega=12.4*2.*np.pi/60., lead=0.0175, r_piston=0.025):

    '''
    Function computing the piston flow u
    parameters:
        omega: float [rad/s]
            current rotation rate, omega=dphi/dt
            for ENSTA float, omega_max = 124.*2.*np.pi/60.,
            omega_min = 12.4*2.*np.pi/60.
        lead: float [m]
            screw lead (i.e. displacement after one screw revolution)
            d = phi/2/pi x lead
        r_piston: float [m]
            piston radius
    '''
    return omega*lead/2.*r_piston**2


def zf(t, params):

    '''
    Function computing the float position depending on time and float parameters
    for initial conditions zf = 0 and vf = 0 at the beginning
    '''
    rho_w = 997 #kg.m^3
    g = 9.81 #m.s^-2
    return (params['u']*g*rho_w*t**3) /6 /params['m'] /(1+params['a'])


def vf(t, params):

    '''
    Function computing the float speed depending on time and float parameters
    for initial conditions zf = 0 and vf = 0 at the beginning
    '''
    rho_w = 997 #kg.m^3
    g = 9.81 #m.s^-2
    return (params['u']*g*rho_w*t**2) / (2*params['m']*(1+params['a']))


def tv(v, params):

    '''
    Function computing the time necessary for the float to reach the speed v
    '''
    rho_w = 997 #kg.m^3
    g = 9.81 #m.s^-2
    return np.sqrt(2*v*params['m']*(1+params['a'])/(g*rho_w*params['u']))


def zv(v, params):

    '''
    Function computing the distance necessary for the float to reach the speed v
    '''
    return zf(tv(v,params),params)
